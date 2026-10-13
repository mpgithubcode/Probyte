"""Views and combined databases."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.calc.compute import Engine
from app.calc.viewfilter import normalize_config
from app.core.db import connection, Jsonb
from app.data import access, store
from app.formats.ical import PALETTE
from app.sharing import linked as remote, recordops
from app.web.common import _own_view, access_info, _record_out, ComboIn, ComboPatch, ComboRecordIn, ComboSource, FastJSON, RecordIn, User, ViewIn, ViewPatch

router = APIRouter()


# ======================================================================== views

def _with_defaults(vtype: str, config: dict, fields: list) -> dict:
    """Fill in what a board / calendar / timeline needs to show anything, as the app does when you add one."""
    config = dict(config or {})
    if vtype == "kanban" and not config.get("groupBy"):
        f = next((f for f in fields if f["type"] == "single_select" and f.get("system_key") != "component"), None) \
            or next((f for f in fields if f["type"] == "single_select"), None)
        if f:
            config["groupBy"] = str(f["id"])
    if vtype in ("calendar", "timeline") and not config.get("dateField"):
        dates = [f for f in fields if f["type"] == "date"]
        start = next((f for f in dates if f.get("system_key") == "dtstart"), None) or (dates[0] if dates else None)
        end = next((f for f in dates if f.get("system_key") in ("dtend", "due")), None)
        if start:
            config["dateField"] = str(start["id"])
            if end and "endField" not in config:
                config["endField"] = str(end["id"])
    return config


def _personal(conn, base_id, user) -> bool:
    """New views of a shared base are the member's own unless they may change the base's views (data/access.py)."""
    m = access.membership(conn, base_id, user["id"])
    return bool(m) and not (m["role"] == "owner" or (access.RANK[m["role"]] >= access.RANK["editor"] and m["follow_views"]))


def _new_view(conn, base_id, user, **kw):
    v = store.create_view(conn, **kw)
    if _personal(conn, base_id, user):
        v = conn.execute("UPDATE views SET user_id = %s WHERE id = %s RETURNING *", (user["id"], v["id"])).fetchone()
    return v


@router.post("/api/tables/{table_id}/views")
def create_table_view(table_id: str, body: ViewIn, user=User):
    with connection() as conn:
        t = store.get_table(conn, table_id, user["id"], need="viewer")
        config = _with_defaults(body.type, normalize_config(body.type, body.config), store.get_fields(conn, table_id))
        return _new_view(conn, t["base_id"], user, table_id=table_id, name=body.name, type=body.type, config=config)


@router.post("/api/combos/{combo_id}/views")
def create_combo_view(combo_id: str, body: ViewIn, user=User):
    with connection() as conn:
        combo = store.get_combo(conn, combo_id, user["id"], need="viewer")
        _, fields = store.resolve_combo(conn, combo)
        config = _with_defaults(body.type, normalize_config(body.type, body.config), fields)
        return _new_view(conn, combo["base_id"], user, combo_id=combo_id, name=body.name, type=body.type, config=config)


def _view_fields(conn, v, user):
    if v["table_id"]:
        return store.get_fields(conn, v["table_id"])
    return store.resolve_combo(conn, store.get_combo(conn, v["combo_id"], user["id"]))[1]


@router.patch("/api/views/{view_id}")
def update_view(view_id: str, body: ViewPatch, user=User):
    with connection() as conn:
        v = _own_view(conn, view_id, user)
        vtype = body.type or v["type"]
        config = normalize_config(vtype, v["config"] if body.config is None else body.config)
        if vtype != v["type"]:  # another kind of view: fill in what it needs (a board's column field, a calendar's dates…)
            config = _with_defaults(vtype, config, _view_fields(conn, v, user))
        if store.view_edit_target(conn, v, user["id"]) == "own":  # a member's own settings for the base's view
            conn.execute("INSERT INTO view_overrides (view_id, user_id, config) VALUES (%s,%s,%s) ON CONFLICT (view_id, user_id) "
                         "DO UPDATE SET config = EXCLUDED.config, updated_at = now()", (view_id, user["id"], Jsonb(config)))
            return {**{k: x for k, x in v.items() if k != "_base_id"}, "config": config, "own_settings": True}
        return conn.execute("UPDATE views SET name = %s, type = %s, config = %s, updated_at = now() WHERE id = %s RETURNING *",
                            ((body.name or v["name"]).strip()[:100], vtype, Jsonb(config), view_id)).fetchone()


class ViewOrder(BaseModel):
    ids: list[str]


@router.post("/api/views/order")
def order_views(body: ViewOrder, user=User):
    """The order of a table's (or combined database's) view tabs."""
    with connection() as conn:
        views = [_own_view(conn, vid, user) for vid in body.ids]
        if len({(str(v["table_id"]), str(v["combo_id"])) for v in views}) > 1:
            raise HTTPException(400, "These views belong to different tables")
        if any(store.view_edit_target(conn, v, user["id"]) == "own" for v in views):
            return {"ok": True}  # the base's view tabs keep their order for a member who can't change its views
        for n, v in enumerate(views):
            conn.execute("UPDATE views SET position = %s WHERE id = %s", (n, v["id"]))
    return {"ok": True}


@router.post("/api/views/{view_id}/duplicate")
def duplicate_view(view_id: str, user=User):
    """A copy of the view (filters, sorts, layout), right after it."""
    with connection() as conn:
        v = _own_view(conn, view_id, user)
        col, owner = ("table_id", v["table_id"]) if v["table_id"] else ("combo_id", v["combo_id"])
        mine = v["user_id"] or (user["id"] if _personal(conn, v["_base_id"], user) else None)
        shown = next((x for x in store.get_views(conn, **{col: owner}, user_id=user["id"]) if x["id"] == v["id"]), v)
        conn.execute(f"UPDATE views SET position = position + 1 WHERE {col} = %s AND position > %s", (owner, v["position"]))
        return conn.execute(f"INSERT INTO views ({col}, name, type, config, position, user_id) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
                            (owner, f"{v['name']} copy"[:100], v["type"], Jsonb(shown["config"]), v["position"] + 1, mine)).fetchone()


@router.delete("/api/views/{view_id}")
def delete_view(view_id: str, user=User):
    with connection() as conn:
        v = _own_view(conn, view_id, user)
        if store.view_edit_target(conn, v, user["id"]) == "own":
            raise HTTPException(403, "This view belongs to the base: its owner, and editors who follow its views, can delete it")
        conn.execute("DELETE FROM views WHERE id = %s", (view_id,))
    return {"ok": True}


# ======================================================================== combined databases

def _combo_payload(conn, combo, defer_remote=False, user_id=None):
    """A combined database with its records. defer_remote: leave out the sources that are linked tables (they
    load from another server, maybe slowly); they're listed in "pending" and fetched one by one (combo_source)."""
    eng = Engine(conn)
    sources, fields = store.resolve_combo(conn, combo, eng)
    pending = [str(s["table_id"]) for s in sources if s["kind"] == "remote"] if defer_remote else []
    for s in sources:  # a source that is a linked combined database: where its new records can go
        if s["kind"] == "remote" and str(s["table_id"]) not in pending:
            s["link_sources"] = remote.sources(conn, store.get_table(conn, s["table_id"]))
    now = {str(s["table_id"]) for s in sources} - set(pending)
    return {"combo": {"id": combo["id"], "name": combo["name"], "base_id": combo["base_id"], "icon": combo.get("icon")}, "sources": sources, "fields": fields,
            "access": access_info(conn, combo["base_id"], user_id) if user_id else None,
            "views": store.get_views(conn, combo_id=combo["id"], user_id=user_id), "records": store.combo_records(conn, combo, eng, only=now),
            "refs": store.combo_refs(conn, combo, eng, only=now), "pending": pending}


def _sources_json(conn, sources: list[ComboSource], user):
    for s in sources:
        store.get_table(conn, s.table_id, user["id"])
    return [{"table_id": s.table_id, "color": s.color or PALETTE[i % len(PALETTE)]} for i, s in enumerate(sources)]


@router.post("/api/combos")
def create_combo(body: ComboIn, user=User):
    if len(body.sources) < 1:
        raise HTTPException(400, "Pick at least one source table")
    with connection() as conn:
        base = store.get_base(conn, body.base_id, user["id"]) if body.base_id else store.ensure_base(conn, user["id"])
        sources = _sources_json(conn, body.sources, user)
        fields = store.build_combo_fields(conn, [s["table_id"] for s in sources])
        c = conn.execute(
            "INSERT INTO combos (user_id, base_id, name, sources, fields) VALUES (%s,%s,%s,%s,%s) RETURNING *",
            (user["id"], base["id"], body.name, Jsonb(sources), Jsonb(fields)),
        ).fetchone()
        store.create_view(conn, combo_id=c["id"], name="Table", type="grid")
        sem = {s: next((f["id"] for f in fields if f["name"].lower() in names), None)
               for s, names in {"start": ("start",), "end": ("due / end", "end / due", "end", "due")}.items()}
        if sem["start"] or sem["end"]:
            store.create_view(conn, combo_id=c["id"], name="Calendar", type="calendar",
                              config={"dateField": sem["start"] or sem["end"], "endField": sem["end"] if sem["start"] else None,
                                      "colorField": "__source"})
        return {"id": c["id"], "name": c["name"]}


@router.get("/api/combos/{combo_id}")
def get_combo(combo_id: str, defer_remote: bool = False, user=User):
    with connection() as conn:
        return FastJSON(_combo_payload(conn, store.get_combo(conn, combo_id, user["id"]), defer_remote, user["id"]))


@router.get("/api/combos/{combo_id}/sources/{table_id}")
def combo_source(combo_id: str, table_id: str, user=User):
    """One source's records of a combined database (for sources left out with defer_remote)."""
    with connection() as conn:
        combo = store.get_combo(conn, combo_id, user["id"])
        if table_id not in {str(s["table_id"]) for s in combo["sources"]}:
            store.not_found("Not a source of this combined database")
        eng = Engine(conn)
        t = store.get_table(conn, table_id)
        return FastJSON({"table_id": table_id, "records": store.combo_records(conn, combo, eng, only={table_id}),
                         "refs": store.combo_refs(conn, combo, eng, only={table_id}),
                         "link_sources": remote.sources(conn, t) if t["kind"] == "remote" else None,
                         "error": ((t["remote"] or {}).get("error") if t["kind"] == "remote" else None)})


@router.patch("/api/combos/{combo_id}")
def update_combo(combo_id: str, body: ComboPatch, user=User):
    with connection() as conn:
        c = store.get_combo(conn, combo_id, user["id"])
        if body.base_id:
            store.get_base(conn, body.base_id, user["id"])
            conn.execute("UPDATE combos SET base_id = %s WHERE id = %s", (body.base_id, combo_id))
        if body.hidden is not None:
            conn.execute("UPDATE combos SET hidden = %s WHERE id = %s", (body.hidden, combo_id))
        if body.icon is not None:
            conn.execute("UPDATE combos SET icon = %s WHERE id = %s", (body.icon.strip()[:16] or None, combo_id))
        if body.description is not None:
            conn.execute("UPDATE combos SET description = %s WHERE id = %s", (body.description.strip()[:2000] or None, combo_id))
        name = body.name or c["name"]
        sources = _sources_json(conn, body.sources, user) if body.sources is not None else c["sources"]
        if body.remap:
            fields = store.build_combo_fields(conn, [s["table_id"] for s in sources])
        elif body.fields is not None:
            fields = [f.model_dump() for f in body.fields]
        else:
            fields = c["fields"]
        c = conn.execute(
            "UPDATE combos SET name = %s, sources = %s, fields = %s WHERE id = %s RETURNING *",
            (name, Jsonb(sources), Jsonb(fields), combo_id),
        ).fetchone()
        return _combo_payload(conn, c, user_id=user["id"])


@router.delete("/api/combos/{combo_id}")
def delete_combo(combo_id: str, user=User):
    with connection() as conn:
        store.get_combo(conn, combo_id, user["id"])
        conn.execute("DELETE FROM combos WHERE id = %s", (combo_id,))
    return {"ok": True}


@router.post("/api/combos/{combo_id}/records")
def create_combo_record(combo_id: str, body: ComboRecordIn, user=User):
    with connection() as conn:
        c = store.get_combo(conn, combo_id, user["id"])
        if body.table_id not in {s["table_id"] for s in c["sources"]}:
            raise HTTPException(400, "Table is not a source of this combined database")
        data = store.unified_to_source(conn, c, body.table_id, body.data)
        rid, table = recordops.create(conn, body.table_id, data, user["id"], link_table_id=body.link_table_id)
        out = _combo_record_out(conn, c, body.table_id, rid)
    return out


def _combo_record_out(conn, combo, table_id, record_id):
    sources, fields = store.resolve_combo(conn, combo)
    src = next((x for x in sources if x["table_id"] == str(table_id)), None)
    return store._project(fields, src["name"] if src else None, str(table_id), _record_out(conn, table_id, record_id))


@router.patch("/api/combos/{combo_id}/records/{record_id}")
def update_combo_record(combo_id: str, record_id: str, body: RecordIn, user=User):
    with connection() as conn:
        c = store.get_combo(conn, combo_id, user["id"])
        if body.table_id:
            tid = body.table_id
        else:
            rec = conn.execute("SELECT table_id FROM records WHERE id = %s", (record_id,)).fetchone() or store.not_found()
            tid = str(rec["table_id"])
        if tid not in {s["table_id"] for s in c["sources"]}:
            store.not_found()
        patch = store.unified_to_source(conn, c, tid, body.data)
        recordops.update(conn, tid, record_id, patch, user["id"])
        out = _combo_record_out(conn, c, tid, record_id)
    return out
