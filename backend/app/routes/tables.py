"""Tables (local, synced and linked) and their fields."""

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import remote, store, sync, values
from ..compute import Engine
from ..db import Jsonb, connection
from .common import COMPONENTS, FastJSON, FieldIn, FieldPatch, FormulaCheck, RelinkIn, TableIn, TablePatch, User, _own_account, _own_field, _table_out

router = APIRouter()


# ======================================================================== tables

@router.post("/api/tables")
def create_table(body: TableIn, user=User):
    with connection() as conn:
        base = store.get_base(conn, body.base_id, user["id"]) if body.base_id else store.ensure_base(conn, user["id"])
        if body.kind == "remote":
            if not body.remote:
                raise HTTPException(400, "Paste the public link to add")
            r = body.remote
            return _table_out(remote.create_table(conn, store, user["id"], base["id"], body.name, r.url, r.password, r.item_id, r.view_id))
    calendar_url = body.calendar_url
    if body.kind != "generic":
        if not body.account_id:
            raise HTTPException(400, "An account is required")
        with connection() as conn:
            a = _own_account(conn, body.account_id, user)
        book = body.kind == "contacts"
        if body.new_calendar_name:
            try:
                calendar_url = (sync.create_addressbook(a, body.new_calendar_name) if book
                                else sync.create_calendar(a, body.new_calendar_name, COMPONENTS[body.kind]))
            except Exception as e:
                raise HTTPException(400, f"Could not create {'address book' if book else 'calendar'}: {e}")
        if not calendar_url:
            raise HTTPException(400, "Choose an address book" if book else "Choose a calendar")
    with connection() as conn:
        t = store.create_table(conn, user["id"], base["id"], body.name, body.kind,
                               body.account_id if body.kind != "generic" else None, calendar_url)
    if body.kind != "generic":
        store.schedule_sync(t["id"], 0)
    return t


@router.get("/api/tables/{table_id}")
def get_table(table_id: str, user=User):
    with connection() as conn:
        t = store.get_table(conn, table_id, user["id"])
        eng = Engine(conn)
        token = eng.token(table_id) if t["kind"] != "remote" else None  # before the records: see Engine.changes
        records = store.list_records(conn, table_id, eng)
        refs = eng.refs(table_id, eng.records(table_id))
        if t["kind"] == "remote":
            t = conn.execute("SELECT * FROM tables WHERE id = %s", (table_id,)).fetchone()  # load() may have updated its status
        return FastJSON({"table": _table_out(t), "fields": eng.describe(table_id), "views": store.get_views(conn, table_id=table_id),
                         "records": records, "refs": refs,
                         "link_sources": remote.sources(conn, t) if t["kind"] == "remote" else None,
                         "dav": _dav_info(conn, t), "token": token})


@router.get("/api/tables/{table_id}/changes")
def table_changes(table_id: str, since: str = "", user=User):
    """Records written or deleted since a token from GET /api/tables/{id} (or the previous call)."""
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
        out = Engine(conn).changes(table_id, since)
    if "records" in out:
        out["records"] = [store.clean_record(r) for r in out["records"]]
    return FastJSON(out)


# ======================================================================== linked tables (from a public link)

class InspectIn(BaseModel):
    url: str
    password: str | None = None


@router.post("/api/remote/inspect")
def inspect_link(body: InspectIn, user=User):
    """Look at a public link before adding it: its name, permissions and shared tables/views."""
    with connection() as conn:
        link = remote.parse_link(body.url)
        link["local"] = remote.is_local(conn, link["token"])
        if body.password:
            link["cookie"] = remote.unlock(conn, link, body.password)
        meta = remote.fetch_meta(conn, link)
    return {**meta, "local": link["local"], "origin": link["origin"], "view_hint": link["view_id"]}


@router.post("/api/tables/{table_id}/relink")
def relink_table(table_id: str, body: RelinkIn, user=User):
    """Re-enter a linked table's password, or point it at a different link."""
    with connection() as conn:
        t = store.get_table(conn, table_id, user["id"])
        if t["kind"] != "remote":
            raise HTTPException(400, "Only linked tables have a link")
        return _table_out(remote.relink(conn, t, body.url, body.password))


@router.post("/api/tables/{table_id}/refresh")
def refresh_table(table_id: str, user=User):
    with connection() as conn:
        t = store.get_table(conn, table_id, user["id"])
        if t["kind"] == "remote":
            remote.load(conn, t, force=True)
    return {"ok": True}


@router.get("/api/remote/{table_id}/attachments/{attachment_id}")
def remote_attachment(table_id: str, attachment_id: str, download: bool = False, user=User):
    with connection() as conn:
        t = store.get_table(conn, table_id, user["id"])
        if t["kind"] != "remote":
            store.not_found()
        return remote.attachment(conn, t, attachment_id, download)


@router.get("/api/tables/{table_id}/titles")
def table_titles(table_id: str, user=User):
    """Record titles of a table, for the link picker."""
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
        titles = Engine(conn).titles(table_id)
    return [{"id": k, "title": v} for k, v in titles.items()]


@router.post("/api/tables/{table_id}/formula")
def check_formula(table_id: str, body: FormulaCheck, user=User):
    """Validate a formula and preview its result on the first few records."""
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
        try:
            compiled = store.compile_formula(conn, table_id, body.expression, body.field_id)
        except HTTPException as e:
            return {"ok": False, "error": e.detail}
        eng = Engine(conn)
        temp = {"id": "__preview", "table_id": table_id, "type": "formula", "options": {"expression": compiled}}
        recs = [{**r, "data": dict(r["data"])} for r in eng.records(table_id)[:3]]
        eng._compute_field(temp, recs)
        titles = eng.titles(table_id)
        preview = [{"title": titles.get(str(r["id"]), "Untitled"), "value": r["data"].get("__preview")} for r in recs]
        return {"ok": True, "resultType": eng.value_type(temp), "preview": preview}


@router.patch("/api/tables/{table_id}")
def update_table(table_id: str, body: TablePatch, user=User):
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
        if body.name:
            conn.execute("UPDATE tables SET name = %s WHERE id = %s", (body.name, table_id))
        if body.base_id:
            store.get_base(conn, body.base_id, user["id"])
            conn.execute("UPDATE tables SET base_id = %s WHERE id = %s", (body.base_id, table_id))
        return _table_out(store.get_table(conn, table_id))


def _dav_info(conn, t):
    """For a synced table: which account, whether it's Probyte's own server, and whether another table uses the same collection."""
    if not store.is_synced(t):
        return None
    a = conn.execute("SELECT name, is_builtin FROM caldav_accounts WHERE id = %s", (t["account_id"],)).fetchone()
    shared = conn.execute("SELECT count(*) AS n FROM tables WHERE id <> %s AND account_id = %s AND calendar_url = %s",
                          (t["id"], t["account_id"], t["calendar_url"])).fetchone()["n"]
    items = conn.execute("SELECT count(*) AS n FROM records WHERE table_id = %s AND caldav_href IS NOT NULL", (t["id"],)).fetchone()["n"]
    return {"account": a["name"] if a else None, "builtin": bool(a and a["is_builtin"]), "shared": shared > 0, "items": items,
            "collection": "address book" if t["kind"] == "contacts" else "calendar"}


def _delete_on_server(conn, t) -> str:
    """Remove a table's data from its DAV server: the whole collection on Probyte's own server, else each item."""
    info = _dav_info(conn, t)
    if not info:
        return "none"
    if info["shared"]:
        return "kept (another table uses the same " + info["collection"] + ")"
    a = conn.execute("SELECT * FROM caldav_accounts WHERE id = %s", (t["account_id"],)).fetchone()
    session = requests.Session()
    if a["username"]:
        session.auth = (a["username"], a["password"] or "")
    try:
        if info["builtin"]:
            r = session.delete(t["calendar_url"], timeout=30)
            if r.status_code not in (200, 204, 404):
                raise HTTPException(502, f"The server refused to delete the {info['collection']} (HTTP {r.status_code})")
            return f"deleted the {info['collection']}"
        hrefs = [x["caldav_href"] for x in conn.execute(
            "SELECT caldav_href FROM records WHERE table_id = %s AND caldav_href IS NOT NULL", (t["id"],)).fetchall()]
        for h in hrefs:
            r = session.delete(h, timeout=30)
            if r.status_code not in (200, 204, 404):
                raise HTTPException(502, f"The server refused to delete an item (HTTP {r.status_code}); nothing else was deleted locally")
        return f"deleted {len(hrefs)} item(s)"
    except requests.RequestException as e:
        raise HTTPException(502, f"Couldn't reach the server to delete the data there: {e}")


@router.delete("/api/tables/{table_id}")
def delete_table(table_id: str, delete_on_server: bool | None = None, user=User):
    """Delete a table. For a synced table, its data on the DAV server goes too: by default when it's on Probyte's own
    server (the whole calendar / address book), and for other accounts only with ?delete_on_server=true (each item)."""
    with connection() as conn:
        t = store.get_table(conn, table_id, user["id"])
        info = _dav_info(conn, t)
        server = "none"
        if info and (delete_on_server if delete_on_server is not None else info["builtin"]):
            server = _delete_on_server(conn, t)
        conn.execute("DELETE FROM tables WHERE id = %s", (table_id,))
        remote.invalidate(table_id)
    return {"ok": True, "server": server}


@router.post("/api/tables/{table_id}/sync")
def sync_now(table_id: str, user=User):
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
    try:
        return sync.sync_table(table_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Sync failed: {e}")


# ======================================================================== fields

def _describe_field(conn, field_id, table_id):
    return next(f for f in Engine(conn).describe(table_id) if str(f["id"]) == str(field_id))


@router.post("/api/tables/{table_id}/fields")
def create_field(table_id: str, body: FieldIn, user=User):
    with connection() as conn:
        table = store.get_table(conn, table_id, user["id"])
        if table["kind"] == "remote":
            raise HTTPException(400, "A linked table's fields come from its source link")
        opts = store.prepare_field(conn, table, body.type, body.options, user["id"])
        if body.type == "link":
            f = store.create_link_pair(conn, table, body.name, {**opts, "symmetric": body.options.get("symmetric", True)}, user["id"])
        else:
            f = store.create_field(conn, table_id, body.name, body.type, opts)
        return _describe_field(conn, f["id"], table_id)


@router.patch("/api/fields/{field_id}")
def update_field(field_id: str, body: FieldPatch, user=User):
    with connection() as conn:
        f = _own_field(conn, field_id, user)
        if f["system_key"] and f["system_key"] != "remote":
            raise HTTPException(400, "This field is locked: it's part of the synced calendar / contact item (or built in). "
                                     "Hide it in a view, or add your own field next to it.")
        table = store.get_table(conn, f["table_id"], user["id"])
        new_type = body.type or f["type"]
        if new_type != f["type"] and f["system_key"]:
            raise HTTPException(400, "The type of a CalDAV field can't be changed")
        name = store.check_field_name(conn, f["table_id"], body.name, exclude=f["id"]) if body.name is not None else f["name"]
        renames = (body.options or {}).pop("renames", None) if new_type == f["type"] else None
        options = f["options"]
        if new_type != f["type"] or body.options is not None:
            options = store.prepare_field(conn, table, new_type, f["options"] if body.options is None else body.options,
                                          user["id"], field=f)
        snapshot = values.snapshot(conn, f) if new_type != f["type"] else None
        if new_type != f["type"] and f["type"] == "link":
            store.delete_field_cleanup(conn, f)  # drops the counterpart field (values were captured above)
        updated = conn.execute(
            "UPDATE fields SET name = %s, type = %s, options = %s WHERE id = %s RETURNING *",
            (name, new_type, Jsonb(options), field_id),
        ).fetchone()
        if renames and new_type in ("single_select", "multi_select"):
            store.rename_choices(conn, updated, renames)
        if new_type == "link" and f["type"] != "link" and (body.options or {}).get("symmetric", True) \
                and str(options["foreignTableId"]) != str(table["id"]) and not store.is_remote_table(conn, options["foreignTableId"]):
            sym = store.create_field(conn, options["foreignTableId"], store.unique_field_name(conn, options["foreignTableId"], table["name"]), "link",
                                     {"foreignTableId": str(table["id"]), "relationship": "many", "symmetricFieldId": str(f["id"])})
            updated = conn.execute("UPDATE fields SET options = options || %s WHERE id = %s RETURNING *",
                                   (Jsonb({"symmetricFieldId": str(sym["id"])}), f["id"])).fetchone()
        if snapshot is not None:
            values.convert_field(conn, f, updated, snapshot)
        return _describe_field(conn, updated["id"], f["table_id"])


@router.delete("/api/fields/{field_id}")
def delete_field(field_id: str, user=User):
    with connection() as conn:
        f = _own_field(conn, field_id, user)
        if f["system_key"] == "remote":
            raise HTTPException(400, "A linked table's fields come from its source link")
        if f["system_key"]:
            raise HTTPException(400, "CalDAV fields can't be deleted (hide them in the view instead)")
        if f["is_primary"]:
            raise HTTPException(400, "The primary field can't be deleted")
        store.delete_field_cleanup(conn, f)
        conn.execute("DELETE FROM fields WHERE id = %s", (field_id,))
    return {"ok": True}
