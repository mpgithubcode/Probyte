"""Tables (local, synced and linked) and their fields."""
import logging

import requests
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.calc.compute import Engine
from app.core.db import connection, Jsonb
from app.core.secretbox import unseal
from app.data import access, pages, stamps, store, values
from app.data.schema import BUILTIN_FIELDS, PRIMARY_TYPES
from app.dav import sync
from app.sharing import linked as remote
from app.web.common import _own_account, access_info, _own_field, _table_out, COMPONENTS, FastJSON, FieldIn, FieldPatch, FormulaCheck, RelinkIn, TableIn, TablePatch, User

router = APIRouter()
BUILTIN_KEYS = {k for k, _, _ in BUILTIN_FIELDS}
log = logging.getLogger("probyte.trash")


# ======================================================================== tables

@router.post("/api/tables")
def create_table(body: TableIn, user=User):
    with connection() as conn:
        base = store.get_base(conn, body.base_id, user["id"], need="admin") if body.base_id else store.ensure_base(conn, user["id"])
        owner = base["user_id"]  # a table made in a shared base is the base owner's, like the rest of it
        if body.kind == "remote":
            if not body.remote:
                raise HTTPException(400, "Paste the public link to add")
            r = body.remote
            return _table_out(remote.create_table(conn, store, owner, base["id"], body.name, r.url, r.password, r.item_id, r.view_id))
        if body.kind == "files":
            from app.files import catalog as files
            t = files.create_table(conn, owner, base["id"], body)
    if body.kind == "files":
        sync.schedule(t["id"], 0)
        return _table_out(t)
    calendar_url = body.calendar_url
    if body.kind != "generic":
        with connection() as conn:
            a = _own_account(conn, body.account_id, user) if body.account_id else None
            if a is None or a["is_builtin"]:  # on Probyte's own server: the table is the calendar / address book
                t = store.create_table(conn, owner, base["id"], body.name, body.kind)
                return _table_out(conn.execute("UPDATE tables SET dav_slug = %s WHERE id = %s RETURNING *", (str(t["id"]), t["id"])).fetchone())
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
        t = store.create_table(conn, owner, base["id"], body.name, body.kind,
                               body.account_id if body.kind != "generic" else None, calendar_url)
    if body.kind != "generic":
        sync.schedule(t["id"], 0)
    return t


@router.get("/api/tables/{table_id}")
def get_table(table_id: str, user=User):
    with connection() as conn:
        t = store.get_table(conn, table_id, user["id"])
        eng = Engine(conn)
        sync.sync_if_stale(conn, t)  # linked to another server: catch up with what changed there (a phone's edits)
        token = eng.token(table_id) if t["kind"] != "remote" else None  # before the records: see Engine.changes
        records = store.list_records(conn, table_id, eng)
        refs = eng.refs(table_id, eng.records(table_id))
        if t["kind"] == "remote":
            t = conn.execute("SELECT * FROM tables WHERE id = %s", (table_id,)).fetchone()  # load() may have updated its status
        return FastJSON({"table": _table_out(t), "fields": eng.describe(table_id),
                         "views": store.get_views(conn, table_id=table_id, user_id=user["id"]),
                         "records": records, "refs": refs, "access": access_info(conn, t["base_id"], user["id"]),
                         "link_sources": remote.sources(conn, t) if t["kind"] == "remote" else None,
                         "dav": _dav_info(conn, t), "storage": _storage_info(conn, t), "token": token})


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


class LinkSettings(BaseModel):
    follow_views: bool


@router.patch("/api/tables/{table_id}/link")
def link_settings(table_id: str, body: LinkSettings, user=User):
    """A linked table's settings: whether its view stays in step with the source's view."""
    with connection() as conn:
        t = store.get_table(conn, table_id, user["id"], need="admin")
        if t["kind"] != "remote":
            raise HTTPException(400, "Only linked tables have a link")
        remote.set_follow_views(conn, t, body.follow_views)
        return _table_out(store.get_table(conn, table_id))


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


@router.get("/api/remote/{table_id}/files/{record_id}/{part}")
def remote_file(table_id: str, record_id: str, part: str, request: Request, download: bool = False, text: bool = False, user=User):
    """A file of a linked files table (contents, cover image or document preview), fetched through its public link."""
    with connection() as conn:
        t = store.get_table(conn, table_id, user["id"])
        if t["kind"] != "remote":
            store.not_found()
    return remote.file(None, t, record_id, part, download=download, text=text, rng=request.headers.get("range"))


def _linked(conn, table_id, user):
    t = store.get_table(conn, table_id, user["id"])
    if t["kind"] != "remote":
        store.not_found()
    return t


@router.post("/api/remote/{table_id}/files")
def remote_upload(table_id: str, files: list[UploadFile] = File(...), folder: str = Form(""), user=User):
    """Add files to a linked files table (its link must allow adding)."""
    with connection() as conn:
        t = _linked(conn, table_id, user)
    return {"records": remote.upload(t, files, folder)}


@router.put("/api/remote/{table_id}/files/{record_id}/file")
def remote_replace(table_id: str, record_id: str, file: UploadFile = File(...), user=User):
    with connection() as conn:
        t = _linked(conn, table_id, user)
    return remote.replace_file(t, _source_id(table_id, record_id), file)


class _Text(BaseModel):
    text: str


@router.put("/api/remote/{table_id}/files/{record_id}/text")
def remote_text(table_id: str, record_id: str, body: _Text, user=User):
    with connection() as conn:
        t = _linked(conn, table_id, user)
    return remote.save_text(t, _source_id(table_id, record_id), body.text)


def _source_id(table_id, record_id) -> str:
    """A linked table's file items carry the source's record id already; its own record ids are mapped back."""
    with connection() as conn:
        t = store.get_table(conn, table_id)
        rev = remote.load(conn, t)["reverse"]
    return rev.get(str(record_id), str(record_id))


@router.post("/api/remote/{table_id}/vcards")
async def remote_vcards_import(table_id: str, files: list[UploadFile] = File(...), existing: str = Form("skip"), user=User):
    from app.data import contacts
    with connection() as conn:
        t = _linked(conn, table_id, user)
    from starlette.concurrency import run_in_threadpool
    texts = await contacts.read_files(files)
    return await run_in_threadpool(remote.import_vcards, t, texts, existing)  # (it may call another server: off the event loop)


@router.get("/api/remote/{table_id}/vcards")
def remote_vcards_export(table_id: str, user=User):
    with connection() as conn:
        t = _linked(conn, table_id, user)
    return remote.export_vcards(t)


@router.get("/api/tables/{table_id}/titles")
def table_titles(table_id: str, field: str | None = None, user=User):
    """Record titles of a table, for the link picker. field: the link field picking from it: only the records its
    settings offer (those of a view of this table, in its order, and / or matching its own conditions)."""
    from app.calc.viewfilter import apply_view
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
        eng = Engine(conn)
        titles = eng.titles(table_id)
        lf = conn.execute("SELECT options FROM fields WHERE id = %s AND type = 'link'", (field,)).fetchone() if field else None
        o = (lf or {}).get("options") or {}
        if o.get("foreignTableId") == str(table_id) and (o.get("pickView") or o.get("pickFilter")):
            fields = eng.describe(table_id)
            recs = eng.records(table_id)
            if o.get("pickView"):
                v = conn.execute("SELECT config FROM views WHERE id = %s AND table_id = %s", (o["pickView"], table_id)).fetchone()
                if v:
                    recs = apply_view(eng, fields, recs, v["config"] or {})
            if (o.get("pickFilter") or {}).get("items"):
                recs = apply_view(eng, fields, recs, {"filter": o["pickFilter"]})
            return [{"id": str(r["id"]), "title": titles.get(str(r["id"]), "Untitled")} for r in recs]
    return [{"id": k, "title": v} for k, v in titles.items()]


@router.post("/api/tables/{table_id}/formula")
def check_formula(table_id: str, body: FormulaCheck, user=User):
    """Validate a formula and preview its result on the first few records."""
    with connection() as conn:
        store.get_table(conn, table_id, user["id"], need="viewer")
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
        t = store.get_table(conn, table_id, user["id"], need="viewer")
        if body.hidden is not None and access.role(conn, t["base_id"], user["id"]) != "owner":
            # a member hides or shows it in their own sidebar only
            conn.execute("UPDATE base_members SET hidden = hidden || jsonb_build_object(%s::text, %s::boolean) WHERE base_id = %s AND user_id = %s",
                         (str(table_id), body.hidden, t["base_id"], user["id"]))
            body.hidden = None
        if set(body.model_fields_set) - {"hidden"}:
            access.check(conn, t["base_id"], user["id"], "admin")
        if body.name:
            conn.execute("UPDATE tables SET name = %s WHERE id = %s", (body.name, table_id))
        if body.base_id:
            nb = store.get_base(conn, body.base_id, user["id"], need="admin")
            if str(nb["user_id"]) != str(t["user_id"]):
                raise HTTPException(400, "A table can only move to another base of the same owner")
            conn.execute("UPDATE tables SET base_id = %s WHERE id = %s", (body.base_id, table_id))
        if body.hidden is not None:
            conn.execute("UPDATE tables SET hidden = %s WHERE id = %s", (body.hidden, table_id))
        if body.icon is not None:
            conn.execute("UPDATE tables SET icon = %s WHERE id = %s", (body.icon.strip()[:16] or None, table_id))
        if body.description is not None:
            conn.execute("UPDATE tables SET description = %s WHERE id = %s", (body.description.strip()[:2000] or None, table_id))
        if body.page is not None:
            page = pages.clean(store.get_fields(conn, table_id), body.page)
            conn.execute("UPDATE tables SET page = %s WHERE id = %s", (Jsonb(page) if page is not None else None, table_id))
            conn.execute("SELECT notify_tables(%s)", (str(table_id),))  # open copies load it again (page is in the token)
        return _table_out(store.get_table(conn, table_id))


class FieldOrder(BaseModel):
    ids: list[str]


@router.get("/api/tables/{table_id}/inbound")
def inbound_links(table_id: str, user=User):
    """Link fields (of other tables, or this one) that point at this table, in tables the user can open: what a page's
    related list can show, with those tables' shared views (for their filter and order)."""
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
        out = []
        for f in conn.execute("SELECT f.id, f.name, f.table_id, t.name AS table_name FROM fields f JOIN tables t ON t.id = f.table_id "
                              "WHERE f.type = 'link' AND f.options->>'foreignTableId' = %s AND t.trashed_at IS NULL ORDER BY t.name, f.position",
                              (str(table_id),)).fetchall():
            try:
                store.get_table(conn, f["table_id"], user["id"])
            except HTTPException:
                continue
            views = conn.execute("SELECT id, name FROM views WHERE table_id = %s AND user_id IS NULL ORDER BY position", (f["table_id"],)).fetchall()
            out.append({"table_id": str(f["table_id"]), "table_name": f["table_name"], "field_id": str(f["id"]), "field_name": f["name"],
                        "views": [{"id": str(v["id"]), "name": v["name"]} for v in views]})
    return out


class PrimaryIn(BaseModel):
    field_id: str


@router.post("/api/tables/{table_id}/primary")
def set_primary(table_id: str, body: PrimaryIn, user=User):
    """Which field is the table's primary field: each record's title (in links, the record panel, a page's title) and the
    first column."""
    with connection() as conn:
        t = store.get_table(conn, table_id, user["id"], need="admin")
        if t["kind"] == "remote":
            raise HTTPException(400, "A linked table's fields come from its source link")
        f = conn.execute("SELECT * FROM fields WHERE id::text = %s AND table_id = %s", (body.field_id, table_id)).fetchone() \
            or store.not_found("Field not found")
        if f["type"] not in PRIMARY_TYPES:
            raise HTTPException(400, "This kind of field can't be the primary field: choose text, a number, a date, a single select, "
                                     "an email, a link address, a phone number, an autonumber or a formula")
        conn.execute("UPDATE fields SET is_primary = (id = %s) WHERE table_id = %s AND is_primary <> (id = %s)", (f["id"], table_id, f["id"]))
        # it comes first (the table's own order; views keep theirs)
        fields = store.get_fields(conn, table_id)
        ordered = [x for x in fields if x["id"] == f["id"]] + [x for x in fields if x["id"] != f["id"] and x["type"] != "record_id"] \
            + [x for x in fields if x["type"] == "record_id"]
        for n, x in enumerate(ordered):
            conn.execute("UPDATE fields SET position = %s WHERE id = %s", (n, x["id"]))
    return {"ok": True}


@router.post("/api/tables/{table_id}/fields/order")
def order_fields(table_id: str, body: FieldOrder, user=User):
    """The table's own field order (views follow it unless they set their own); Record ID stays last."""
    with connection() as conn:
        store.get_table(conn, table_id, user["id"], need="admin")
        fields = store.get_fields(conn, table_id)
        rank = {fid: n for n, fid in enumerate(body.ids)}
        ordered = sorted((f for f in fields if f["type"] != "record_id"), key=lambda f: (rank.get(str(f["id"]), len(rank)), f["position"]))
        ordered += [f for f in fields if f["type"] == "record_id"]
        for n, f in enumerate(ordered):
            conn.execute("UPDATE fields SET position = %s WHERE id = %s", (n, f["id"]))
    return {"ok": True}


def _dav_info(conn, t):
    """For a calendar or contacts table: where it is (Probyte's own server, or which account), and whether another
    table uses the same collection."""
    if store.is_served(t):
        items = conn.execute("SELECT count(*) AS n FROM records WHERE table_id = %s AND NOT deleted", (t["id"],)).fetchone()["n"]
        owner = conn.execute("SELECT username FROM users WHERE id = %s", (t["user_id"],)).fetchone()
        return {"account": "Probyte", "builtin": True, "shared": False, "items": items,
                "collection": "address book" if t["kind"] == "contacts" else "calendar",
                "path": f"/dav/{owner['username']}/{t['dav_slug']}/"}
    if not store.is_synced(t):
        return None
    a = conn.execute("SELECT name, is_builtin FROM caldav_accounts WHERE id = %s", (t["account_id"],)).fetchone()
    shared = conn.execute("SELECT count(*) AS n FROM tables WHERE id <> %s AND account_id = %s AND calendar_url = %s",
                          (t["id"], t["account_id"], t["calendar_url"])).fetchone()["n"]
    items = conn.execute("SELECT count(*) AS n FROM records WHERE table_id = %s AND caldav_href IS NOT NULL", (t["id"],)).fetchone()["n"]
    return {"account": a["name"] if a else None, "builtin": bool(a and a["is_builtin"]), "shared": shared > 0, "items": items,
            "collection": "address book" if t["kind"] == "contacts" else "calendar"}


def _storage_info(conn, t):
    """For a files table: where its files are."""
    if t["kind"] != "files":
        return None
    s = conn.execute("SELECT name, kind, endpoint, bucket FROM storages WHERE id = %s", (t["storage_id"],)).fetchone() if t["storage_id"] else None
    place = (s["bucket"] if s["kind"] == "s3" else s["endpoint"].rstrip("/")) if s else None
    prefix = (t["storage_prefix"] or "").strip("/")
    return {"name": s["name"] if s else None, "kind": s["kind"] if s else None,
            "location": f"{place}/{prefix}" if place and prefix else place}


def _delete_on_server(conn, t) -> str:
    """Remove a table's data from its DAV server: the whole collection on Probyte's own server, else each item."""
    info = _dav_info(conn, t)
    if not info:
        return "none"
    if store.is_served(t):
        return f"deleted the {info['collection']}"  # it was the table itself
    if info["shared"]:
        return "kept (another table uses the same " + info["collection"] + ")"
    a = conn.execute("SELECT * FROM caldav_accounts WHERE id = %s", (t["account_id"],)).fetchone()
    session = requests.Session()
    if a["username"]:
        session.auth = (a["username"], unseal(a["password"]) or "")
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
def delete_table(table_id: str, delete_on_server: bool | None = None, forever: bool = False, user=User):
    """Move a table to the trash (restorable for 30 days), or with ?forever=true delete it now.
    For a synced table, its data on the DAV server goes too when it's really deleted: by default when it's on
    Probyte's own server (the whole calendar / address book), for other accounts only with ?delete_on_server=true."""
    with connection() as conn:
        t = store.get_table(conn, table_id, user["id"], need="admin")
        info = _dav_info(conn, t)
        on_server = bool(info and (delete_on_server if delete_on_server is not None else info["builtin"]))
        if not forever:
            conn.execute("UPDATE tables SET trashed_at = now(), trash_delete_on_server = %s WHERE id = %s", (on_server, table_id))
            return {"ok": True, "trashed": True, "server": "deleted with the table, in 30 days" if on_server else "none"}
        server = purge_table(conn, t, on_server)
    return {"ok": True, "server": server}


def purge_table(conn, t, on_server: bool) -> str:
    """Delete a table for good (and its data on the DAV server when asked)."""
    server = _delete_on_server(conn, t) if on_server and _dav_info(conn, t) else "none"
    conn.execute("DELETE FROM tables WHERE id = %s", (t["id"],))
    remote.invalidate(t["id"])
    return server


# ======================================================================== trash

@router.get("/api/trash")
def trash(user=User):
    """Tables and records deleted in the last 30 days."""
    with connection() as conn:
        tables = conn.execute(
            "SELECT t.id, t.name, t.kind, t.base_id, t.trashed_at, t.trash_delete_on_server, b.name AS base_name, "
            "(SELECT count(*) FROM records r WHERE r.table_id = t.id AND NOT r.deleted) AS records "
            "FROM tables t JOIN bases b ON b.id = t.base_id WHERE t.trashed_at IS NOT NULL AND (t.user_id = %s OR t.base_id IN "
            "(SELECT base_id FROM base_members WHERE user_id = %s AND role = 'admin')) ORDER BY t.trashed_at DESC",
            (user["id"], user["id"])).fetchall()
        records = conn.execute(
            "SELECT r.id, r.table_id, t.name AS table_name, r.deleted_at, r.data -> (SELECT f.id::text FROM fields f "
            "  WHERE f.table_id = t.id AND f.is_primary LIMIT 1) AS title "
            "FROM record_trash r JOIN tables t ON t.id = r.table_id WHERE t.trashed_at IS NULL AND (t.user_id = %s OR t.base_id IN "
            "(SELECT base_id FROM base_members WHERE user_id = %s AND role IN ('editor', 'admin'))) "
            "ORDER BY r.deleted_at DESC LIMIT 500", (user["id"], user["id"])).fetchall()
    return {"tables": tables, "records": records}


class Ids(BaseModel):
    ids: list[str]


@router.post("/api/trash/records/restore")
def restore_records(body: Ids, user=User):
    """Put records back (e.g. Undo after deleting); each back in its table, synced ones on the server again."""
    restored = []
    with connection() as conn:
        for rid in body.ids[:5000]:
            try:
                with conn.transaction():
                    store.restore_record(conn, rid, user["id"])
                restored.append(rid)
            except HTTPException:
                pass
    return {"restored": restored}


@router.post("/api/trash/records/delete")
def purge_records(body: Ids, user=User):
    """Empty records from the trash for good."""
    with connection() as conn:
        n = conn.execute("DELETE FROM record_trash r USING tables t WHERE r.table_id = t.id AND r.id = ANY(%s::uuid[]) AND (t.user_id = %s "
                         "OR t.base_id IN (SELECT base_id FROM base_members WHERE user_id = %s AND role IN ('editor', 'admin')))",
                         (body.ids, user["id"], user["id"])).rowcount
    from app.files import catalog as files
    files.purge_deleted()  # files of files tables: deleted from the storage now
    return {"deleted": n}


@router.post("/api/trash/tables/{table_id}/restore")
def restore_table(table_id: str, user=User):
    with connection() as conn:
        store.get_table(conn, table_id, user["id"], trashed=True, need="admin")
        conn.execute("UPDATE tables SET trashed_at = NULL, trash_delete_on_server = false WHERE id = %s", (table_id,))
        t = store.get_table(conn, table_id)
        if store.is_synced(t) or (t["kind"] == "files" and t["storage_id"]):
            sync.schedule(table_id, 0, conn=conn)  # catch up with what changed on the server meanwhile
    return {"ok": True}


@router.delete("/api/trash/tables/{table_id}")
def purge_trashed_table(table_id: str, user=User):
    """Delete a table in the trash for good now."""
    with connection() as conn:
        t = store.get_table(conn, table_id, user["id"], trashed=True, need="admin")
        if not t["trashed_at"]:
            raise HTTPException(400, "Only tables in the trash can be deleted for good here")
        server = purge_table(conn, t, t["trash_delete_on_server"])
    return {"ok": True, "server": server}


def purge_expired(days: int = 30):
    """Run by the worker: what's been in the trash longer than `days` is deleted for good."""
    with connection() as conn:
        for t in conn.execute("SELECT * FROM tables WHERE trashed_at < now() - make_interval(days => %s)", (days,)).fetchall():
            try:
                with conn.transaction():
                    purge_table(conn, t, t["trash_delete_on_server"])
            except Exception as e:  # e.g. the calendar server is down: try again next time
                log.warning("couldn't purge table %s: %s", t["id"], e)
        conn.execute("DELETE FROM record_trash WHERE deleted_at < now() - make_interval(days => %s)", (days,))
    from app.files import catalog as files
    files.purge_deleted()


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
        table = store.get_table(conn, table_id, user["id"], need="admin")
        if table["kind"] == "remote":
            raise HTTPException(400, "A linked table's fields come from its source link")
        opts = store.prepare_field(conn, table, body.type, body.options, user["id"])
        if body.type == "link":
            f = store.create_link_pair(conn, table, body.name, {**opts, "symmetric": body.options.get("symmetric", True)}, user["id"])
        else:
            f = store.create_field(conn, table_id, body.name, body.type, opts)
        rules = store.field_rules(conn, {**f, "required": False, "unique_values": False, "default_value": None, "id": None},
                                  required=body.required, unique=body.unique, default=body.default_value)
        conn.execute("UPDATE fields SET description = %s, required = %s, unique_values = %s, default_value = %s WHERE id = %s",
                     ((body.description or "").strip()[:1000] or None, rules["required"], rules["unique_values"],
                      Jsonb(rules["default_value"]) if rules["default_value"] is not None else None, f["id"]))
        if f["type"] == "date":
            stamps.backfill(conn, table_id, f)   # a Started / Completed date: records it already applies to
        return _describe_field(conn, f["id"], table_id)


@router.patch("/api/fields/{field_id}")
def update_field(field_id: str, body: FieldPatch, user=User):
    with connection() as conn:
        f = _own_field(conn, field_id, user)
        if f["system_key"] and f["system_key"] != "remote" and set(body.model_fields_set) <= {"description"}:
            # a locked field can still be described
            conn.execute("UPDATE fields SET description = %s WHERE id = %s", ((body.description or "").strip()[:1000] or None, field_id))
            return _describe_field(conn, field_id, f["table_id"])
        if f["system_key"] and f["system_key"] != "remote":
            raise HTTPException(400, "This field is locked: it's part of the synced calendar / contact item (or built in). "
                                     "Hide it in a view, or add your own field next to it.")
        table = store.get_table(conn, f["table_id"], user["id"], need="admin")
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
        sent = body.model_fields_set
        rules = store.field_rules(conn, updated if new_type == f["type"] else {**updated, "unique_values": False},
                                  required=body.required, unique=body.unique,
                                  default=body.default_value,
                                  clear_default=("default_value" in sent and body.default_value is None)
                                  or (new_type != f["type"] and "default_value" not in sent))
        desc = (body.description or "").strip()[:1000] or None if "description" in sent else updated["description"]
        updated = conn.execute(
            "UPDATE fields SET description = %s, required = %s, unique_values = %s, default_value = %s WHERE id = %s RETURNING *",
            (desc, rules["required"], rules["unique_values"],
             Jsonb(rules["default_value"]) if rules["default_value"] is not None else None, field_id)).fetchone()
        if updated["type"] == "date" and (updated["options"].get("stampWhen"), updated["options"].get("stampClear")) != \
                ((f["options"] or {}).get("stampWhen"), (f["options"] or {}).get("stampClear")):
            stamps.backfill(conn, f["table_id"], updated)
        return _describe_field(conn, updated["id"], f["table_id"])


@router.delete("/api/fields/{field_id}")
def delete_field(field_id: str, user=User):
    with connection() as conn:
        f = _own_field(conn, field_id, user)
        if f["system_key"] == "remote":
            raise HTTPException(400, "A linked table's fields come from its source link")
        if f["system_key"] in BUILTIN_KEYS:
            raise HTTPException(400, "Built-in fields can't be deleted (hide them in the view instead)")
        if f["system_key"]:
            raise HTTPException(400, "CalDAV fields can't be deleted (hide them in the view instead)")
        if f["is_primary"]:
            raise HTTPException(400, "The primary field can't be deleted")
        store.delete_field_cleanup(conn, f)
        conn.execute("DELETE FROM fields WHERE id = %s", (field_id,))
    return {"ok": True}
