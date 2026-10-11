"""Views and combined databases."""

from fastapi import APIRouter, HTTPException

from .. import recordops, remote, store
from ..compute import Engine
from ..db import Jsonb, connection
from ..ical import PALETTE
from .common import ComboIn, ComboPatch, ComboRecordIn, ComboSource, FastJSON, RecordIn, User, ViewIn, ViewPatch, _own_view, _record_out

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


@router.post("/api/tables/{table_id}/views")
def create_table_view(table_id: str, body: ViewIn, user=User):
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
        config = _with_defaults(body.type, body.config, store.get_fields(conn, table_id))
        return store.create_view(conn, table_id=table_id, name=body.name, type=body.type, config=config)


@router.post("/api/combos/{combo_id}/views")
def create_combo_view(combo_id: str, body: ViewIn, user=User):
    with connection() as conn:
        combo = store.get_combo(conn, combo_id, user["id"])
        _, fields = store.resolve_combo(conn, combo)
        config = _with_defaults(body.type, body.config, fields)
        return store.create_view(conn, combo_id=combo_id, name=body.name, type=body.type, config=config)


@router.patch("/api/views/{view_id}")
def update_view(view_id: str, body: ViewPatch, user=User):
    with connection() as conn:
        v = _own_view(conn, view_id, user)
        return conn.execute(
            "UPDATE views SET name = %s, config = %s WHERE id = %s RETURNING *",
            (body.name or v["name"], Jsonb(v["config"] if body.config is None else body.config), view_id),
        ).fetchone()


@router.delete("/api/views/{view_id}")
def delete_view(view_id: str, user=User):
    with connection() as conn:
        _own_view(conn, view_id, user)
        conn.execute("DELETE FROM views WHERE id = %s", (view_id,))
    return {"ok": True}


# ======================================================================== combined databases

def _combo_payload(conn, combo):
    eng = Engine(conn)
    sources, fields = store.resolve_combo(conn, combo, eng)
    for s in sources:  # a source that is a linked combined database: where its new records can go
        if s["kind"] == "remote":
            s["link_sources"] = remote.sources(conn, store.get_table(conn, s["table_id"]))
    return {"combo": {"id": combo["id"], "name": combo["name"], "base_id": combo["base_id"]}, "sources": sources, "fields": fields,
            "views": store.get_views(conn, combo_id=combo["id"]), "records": store.combo_records(conn, combo, eng),
            "refs": store.combo_refs(conn, combo, eng)}


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
def get_combo(combo_id: str, user=User):
    with connection() as conn:
        return FastJSON(_combo_payload(conn, store.get_combo(conn, combo_id, user["id"])))


@router.patch("/api/combos/{combo_id}")
def update_combo(combo_id: str, body: ComboPatch, user=User):
    with connection() as conn:
        c = store.get_combo(conn, combo_id, user["id"])
        if body.base_id:
            store.get_base(conn, body.base_id, user["id"])
            conn.execute("UPDATE combos SET base_id = %s WHERE id = %s", (body.base_id, combo_id))
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
        return _combo_payload(conn, c)


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
