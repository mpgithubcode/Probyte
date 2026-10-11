"""The public API's core, shared by the REST API (/api/v1) and the MCP server (/mcp).

Tables can be named by id or by name, records are read and written with fields keyed by field name, and
values are converted from plain text the way a person would type them: "2026-10-12" for a date, "Done" for
a select option, a record's title for a link, a username for a user field.
"""
import json
import re

from fastapi import HTTPException

from . import recordops, store, sync
from . import values as values_
from .compute import COMPUTED_TYPES, FILTER_OPS, Engine
from .viewfilter import apply_view, display_text, view_filter, view_sorts

MAX_LIMIT = 1000
MAX_BATCH = 1000
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
HIDDEN_TYPES = {"record_id", "button"}  # left out of record output (the id is already there)


def bad(msg: str, code: int = 400):
    raise HTTPException(code, msg)


# ---------------------------------------------------------------- bases & tables

def _bases(conn, user):
    return [b for b in conn.execute("SELECT id, name, color FROM bases WHERE user_id = %s ORDER BY position, created_at",
                                    (user["id"],)).fetchall() if store.base_allowed(b["id"])]


def list_bases(conn, user) -> list[dict]:
    bases = _bases(conn, user)
    tables = conn.execute("SELECT id, base_id, name, kind FROM tables WHERE user_id = %s AND trashed_at IS NULL ORDER BY created_at", (user["id"],)).fetchall()
    combos = conn.execute("SELECT id, base_id, name FROM combos WHERE user_id = %s ORDER BY created_at", (user["id"],)).fetchall()
    return [{
        "id": str(b["id"]), "name": b["name"],
        "tables": [{"id": str(t["id"]), "name": t["name"], "kind": t["kind"]} for t in tables if t["base_id"] == b["id"]],
        "combined_databases": [{"id": str(c["id"]), "name": c["name"]} for c in combos if c["base_id"] == b["id"]],
    } for b in bases]


def resolve_base(conn, user, ref=None):
    bases = _bases(conn, user)
    if not bases:
        return store.ensure_base(conn, user["id"])
    if ref in (None, ""):
        return bases[0]
    ref = str(ref).strip()
    hit = [b for b in bases if str(b["id"]) == ref] or [b for b in bases if b["name"].lower() == ref.lower()]
    if not hit:
        bad(f"No base '{ref}'. Bases: {', '.join(b['name'] for b in bases)}", 404)
    return hit[0]


def resolve(conn, user, ref, base=None, allow_combo=True):
    """('table' | 'combo', row) for an id, a name, or 'Base / Table'."""
    ref = str(ref or "").strip()
    if not ref:
        bad("Say which table: its id or name")
    if UUID_RE.match(ref):
        t = conn.execute("SELECT * FROM tables WHERE id = %s AND user_id = %s AND trashed_at IS NULL", (ref, user["id"])).fetchone()
        if t and store.base_allowed(t["base_id"]):
            return "table", t
        if allow_combo:
            c = conn.execute("SELECT * FROM combos WHERE id = %s AND user_id = %s", (ref, user["id"])).fetchone()
            if c and store.base_allowed(c["base_id"]):
                return "combo", c
        bad("Table not found", 404)
    if base is None and " / " in ref:
        base, ref = (x.strip() for x in ref.split(" / ", 1))
    bases = {str(b["id"]): b for b in _bases(conn, user)}
    if base:
        b = resolve_base(conn, user, base)
        bases = {str(b["id"]): b}
    cands = [("table", t) for t in conn.execute("SELECT * FROM tables WHERE user_id = %s AND trashed_at IS NULL", (user["id"],)).fetchall()
             if str(t["base_id"]) in bases]
    if allow_combo:
        cands += [("combo", c) for c in conn.execute("SELECT * FROM combos WHERE user_id = %s", (user["id"],)).fetchall()
                  if str(c["base_id"]) in bases]
    hit = [x for x in cands if x[1]["name"].lower() == ref.lower()]
    if len(hit) == 1:
        return hit[0]
    if len(hit) > 1:
        bad(f"More than one table is called '{ref}'. Use its id, or 'Base / {ref}'.")
    names = ", ".join(sorted(f"{bases[str(x[1]['base_id'])]['name']} / {x[1]['name']}" for x in cands)[:40])
    bad(f"No table '{ref}'. Tables: {names or 'none'}", 404)


def _fields(conn, kind, row, eng):
    if kind == "table":
        return eng.describe(row["id"])
    return store.resolve_combo(conn, row, eng)[1]


def field_out(f) -> dict:
    o = f.get("options") or {}
    out = {"id": str(f["id"]), "name": f["name"], "type": f["type"]}
    if f.get("is_primary"):
        out["primary"] = True
    if f.get("computed") or f["type"] in COMPUTED_TYPES or f.get("readonly"):
        out["read_only"] = True
    if f.get("system_key") and f["system_key"] not in ("record_id",):
        out["synced"] = True  # part of the CalDAV/CardDAV item
    opts = {}
    if o.get("choices") is not None:
        opts["choices"] = [c["name"] for c in o["choices"]]
    if f["type"] == "link":
        opts["linked_table_id"] = o.get("foreignTableId")
        opts["relationship"] = o.get("relationship", "many")
    if f["type"] == "date":
        opts["include_time"] = bool(o.get("includeTime"))
    if f["type"] == "formula":
        opts["expression"] = o.get("expression")
    if f["type"] == "number" and o.get("precision") is not None:
        opts["precision"] = o["precision"]
    if f["type"] == "rating":
        opts["max"] = o.get("max", 5)
    if f["type"] == "recurrence":
        opts["format"] = "iCalendar RRULE, e.g. FREQ=WEEKLY;BYDAY=MO,WE"
    if opts:
        out["options"] = opts
    return out


def table_schema(conn, user, ref, base=None) -> dict:
    kind, row = resolve(conn, user, ref, base)
    eng = Engine(conn)
    fields = _fields(conn, kind, row, eng)
    col = "table_id" if kind == "table" else "combo_id"
    views = conn.execute(f"SELECT id, name, type FROM views WHERE {col} = %s ORDER BY position", (row["id"],)).fetchall()
    b = conn.execute("SELECT id, name FROM bases WHERE id = %s", (row["base_id"],)).fetchone()
    out = {"id": str(row["id"]), "name": row["name"], "base": {"id": str(b["id"]), "name": b["name"]},
           "kind": row.get("kind", "combined") if kind == "table" else "combined",
           "fields": [field_out(f) for f in fields if f["id"] != "__id"],
           "views": [{"id": str(v["id"]), "name": v["name"], "type": v["type"]} for v in views]}
    if kind == "combo":
        out["read_only"] = True
        out["note"] = "A combined database shows records of several tables; write to the source tables instead."
    elif row["kind"] in ("tasks", "events", "mixed", "contacts"):
        out["synced_with"] = "CardDAV address book" if row["kind"] == "contacts" else "CalDAV calendar"
    return out


# ---------------------------------------------------------------- reading

def _field_index(fields):
    idx = {}
    for f in fields:
        idx.setdefault(str(f["id"]), f)
    for f in fields:
        idx.setdefault(f["name"].lower(), f)
    return idx


def find_field(fields, key, idx=None):
    idx = idx or _field_index(fields)
    f = idx.get(str(key)) or idx.get(str(key).lower())
    if not f:
        bad(f"Unknown field '{key}'. Fields: {', '.join(x['name'] for x in fields if x['id'] not in ('__id',))}")
    return f


def _is_blank(v):
    return v is None or v == "" or (isinstance(v, list) and not v)


def record_out(eng, fields, r, cell_format="json", field_key="name") -> dict:
    data = {}
    for f in fields:
        if f["type"] in HIDDEN_TYPES or f["id"] == "__id":
            continue
        v = r["data"].get(str(f["id"]))
        if isinstance(v, dict) and "__error" in v:
            v = "#ERROR"
        elif cell_format == "text":
            v = display_text(eng, f, v) if not _is_blank(v) else None
            if v == "false" and f["type"] == "checkbox":
                v = None
        if _is_blank(v) or (v is False and f["type"] == "checkbox" and cell_format == "text"):
            continue
        data[f["name"] if field_key == "name" else str(f["id"])] = v
    return {"id": str(r["id"]), "table_id": str(r["table_id"]), "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"), "fields": data}


def _parse_filters(fields, filters) -> list[dict]:
    if filters in (None, "", []):
        return []
    if isinstance(filters, str):
        try:
            filters = json.loads(filters)
        except ValueError:
            bad('filter must be JSON, e.g. [{"field": "Status", "op": "is", "value": "Done"}]')
    if isinstance(filters, dict):  # {"Status": "Done"} shorthand = equality
        filters = [{"field": k, "op": "is", "value": v} for k, v in filters.items()]
    out = []
    idx = _field_index(fields)
    for flt in filters:
        if not isinstance(flt, dict) or "field" not in flt:
            bad('Each filter needs a field, e.g. {"field": "Status", "op": "is", "value": "Done"}')
        op = flt.get("op", "is")
        if op not in FILTER_OPS:
            bad(f"Unknown filter op '{op}'. Use one of: {', '.join(sorted(FILTER_OPS))}")
        f = find_field(fields, flt["field"], idx)
        val = flt.get("value")
        out.append({"field": str(f["id"]), "op": op, "value": "" if val is None else (json.dumps(val) if isinstance(val, (list, dict)) else str(val))})
    return out


def _parse_sort(fields, sort) -> list[tuple[str, str]]:
    if sort in (None, "", []):
        return []
    items = sort if isinstance(sort, list) else [x for x in str(sort).split(",") if x.strip()]
    out = []
    for it in items:
        if isinstance(it, dict):
            name, d = it.get("field"), "desc" if str(it.get("direction", it.get("dir", "asc"))).lower().startswith("d") else "asc"
        else:
            it = str(it).strip()
            name, d = (it[1:], "desc") if it.startswith("-") else (it, "asc")
        out.append((str(find_field(fields, name)["id"]), d))
    return out


def query_records(conn, user, ref, *, base=None, view=None, filters=None, search=None, sort=None, fields=None,
                  limit=100, offset=0, cell_format="json", field_key="name") -> dict:
    kind, row = resolve(conn, user, ref, base)
    eng = Engine(conn)
    all_fields = _fields(conn, kind, row, eng)
    records = eng.records(row["id"]) if kind == "table" else store.combo_records(conn, row, eng)
    vcfg = {}
    if view:
        col = "table_id" if kind == "table" else "combo_id"
        v = (conn.execute(f"SELECT config FROM views WHERE {col} = %s AND id::text = %s", (row["id"], str(view))).fetchone()
             or conn.execute(f"SELECT config FROM views WHERE {col} = %s AND lower(name) = lower(%s)", (row["id"], str(view))).fetchone())
        if not v:
            bad(f"No view '{view}' in {row['name']}", 404)
        vcfg = v["config"]
    records = apply_view(eng, all_fields, records,
                         {"filter": {"conjunction": "and", "items": [view_filter(vcfg), *_parse_filters(all_fields, filters)]}})
    if search:
        q = str(search).lower()
        records = [r for r in records if any(q in display_text(eng, f, r["data"].get(str(f["id"]))).lower()
                                             for f in all_fields if f["type"] not in HIDDEN_TYPES)]
    sorts = _parse_sort(all_fields, sort) or [(str(x["field"]), x.get("dir", "asc")) for x in view_sorts(vcfg)]
    records = apply_view(eng, all_fields, records, {"sorts": [{"field": fid, "dir": d} for fid, d in sorts]})
    limit = max(1, min(int(limit or 100), MAX_LIMIT))
    offset = max(0, int(offset or 0))
    out_fields = all_fields
    if fields:
        names = fields if isinstance(fields, list) else [x for x in str(fields).split(",") if x.strip()]
        idx = _field_index(all_fields)
        out_fields = [find_field(all_fields, n.strip(), idx) for n in names]
    page = records[offset:offset + limit]
    return {"records": [record_out(eng, out_fields, r, cell_format, field_key) for r in page],
            "total": len(records), "offset": offset, "next_offset": offset + limit if offset + limit < len(records) else None}


def get_record(conn, user, record_id, cell_format="json", field_key="name", ref=None) -> dict:
    if not UUID_RE.match(str(record_id)):
        bad("Record ids look like 1b4e28ba-2fa1-11d2-883f-0016d3cca427", 404)
    if ref:
        kind, row = resolve(conn, user, ref)
        tid = row["id"]
    else:
        r = conn.execute("SELECT table_id FROM records WHERE id = %s AND NOT deleted", (record_id,)).fetchone()
        if not r:
            bad("Record not found", 404)
        kind, row = resolve(conn, user, str(r["table_id"]))
        tid = row["id"]
    eng = Engine(conn)
    fields = _fields(conn, kind, row, eng)
    recs = eng.records(tid) if kind == "table" else store.combo_records(conn, row, eng)
    rec = next((x for x in recs if str(x["id"]) == str(record_id)), None)
    if not rec:
        bad("Record not found", 404)
    return record_out(eng, fields, rec, cell_format, field_key)


# ---------------------------------------------------------------- writing

def _writable_table(conn, user, ref, base=None):
    kind, row = resolve(conn, user, ref, base)
    if kind == "combo":
        bad("Combined databases are read-only through the API; write to one of their source tables")
    return row


def coerce(fields, values: dict, lookups: values_.Lookups) -> dict:
    """Field name/id -> value (as a person would write it)  =>  field id -> stored value."""
    if not isinstance(values, dict):
        bad('fields must be an object like {"Name": "Ada", "Status": "Done"}')
    idx = _field_index(fields)
    out = {}
    for key, val in values.items():
        f = find_field(fields, key, idx)
        if f["type"] in COMPUTED_TYPES or f.get("computed"):
            bad(f"'{f['name']}' is calculated and can't be set")
        if f["type"] == "attachment":
            bad(f"'{f['name']}' holds uploaded files, which can't be set through the API")
        out[str(f["id"])] = values_.from_api(f, val, lookups)
    return out


def _records_by_id(conn, table_id, ids, cell_format="json", field_key="name"):
    eng = Engine(conn)
    fields = eng.describe(table_id)
    want = {str(i) for i in ids}
    if len(want) <= 100:  # just written: compute those records alone
        found = {i: r for i in want if (r := eng.record(table_id, i))}
    else:
        found = {str(r["id"]): r for r in eng.records(table_id) if str(r["id"]) in want}
    return [record_out(eng, fields, found[str(i)], cell_format, field_key) for i in ids if str(i) in found]


def create_records(conn, user, ref, records: list[dict], base=None, cell_format="json", field_key="name") -> tuple[list, dict]:
    t = _writable_table(conn, user, ref, base)
    if not records:
        bad("Send at least one record")
    if len(records) > MAX_BATCH:
        bad(f"At most {MAX_BATCH} records per request")
    fields = Engine(conn).describe(t["id"])
    lookups, ids = values_.Lookups(conn), []
    for n, rec in enumerate(records, start=1):
        values = rec.get("fields", rec) if isinstance(rec, dict) else rec
        try:
            data = coerce(fields, values, lookups)
            rid, table = recordops.create(conn, t["id"], data, user["id"])
        except HTTPException as e:
            bad(f"Record {n}: {e.detail}" if len(records) > 1 else e.detail, e.status_code)
        ids.append(rid)
    return _records_by_id(conn, t["id"], ids, cell_format, field_key), t


def update_records(conn, user, ref, updates: list[dict], base=None, cell_format="json", field_key="name") -> tuple[list, dict]:
    t = _writable_table(conn, user, ref, base)
    if not updates:
        bad("Send at least one record")
    if len(updates) > MAX_BATCH:
        bad(f"At most {MAX_BATCH} records per request")
    fields = Engine(conn).describe(t["id"])
    lookups, ids = values_.Lookups(conn), []
    for n, u in enumerate(updates, start=1):
        rid = str((u or {}).get("id", ""))
        try:
            if not UUID_RE.match(rid):
                bad("Each record needs its id")
            data = coerce(fields, u.get("fields") or {}, lookups)
            recordops.update(conn, t["id"], rid, data, user["id"])
        except HTTPException as e:
            bad(f"Record {n} ({rid or 'no id'}): {e.detail}" if len(updates) > 1 else e.detail, e.status_code)
        ids.append(rid)
    return _records_by_id(conn, t["id"], ids, cell_format, field_key), t


def _match_key(v):
    if isinstance(v, list):
        return json.dumps(sorted(str(x).lower() for x in v))
    if isinstance(v, str):
        return v.strip().lower()
    return json.dumps(v)


def upsert_records(conn, user, ref, records: list[dict], match_on: list[str], base=None, cell_format="json", field_key="name") -> tuple[dict, dict]:
    """Update the record whose match_on fields equal the given values, or create one."""
    t = _writable_table(conn, user, ref, base)
    if not match_on:
        bad("match_on needs at least one field name, e.g. [\"Email\"]")
    if len(records) > MAX_BATCH:
        bad(f"At most {MAX_BATCH} records per request")
    eng = Engine(conn)
    fields = eng.describe(t["id"])
    keys = [str(find_field(fields, m)["id"]) for m in match_on]
    index = {}
    for r in eng.records(t["id"]):
        index.setdefault(tuple(_match_key(r["data"].get(k)) for k in keys), str(r["id"]))
    lookups, created, updated = values_.Lookups(conn), [], []
    for n, rec in enumerate(records, start=1):
        values = rec.get("fields", rec) if isinstance(rec, dict) else rec
        try:
            data = coerce(fields, values, lookups)
            if any(k not in data for k in keys):
                bad(f"needs a value for {', '.join(match_on)}")
            key = tuple(_match_key(data[k]) for k in keys)
            if key in index:
                recordops.update(conn, t["id"], index[key], data, user["id"])
                updated.append(index[key])
            else:
                rid, _ = recordops.create(conn, t["id"], data, user["id"])
                index[key] = str(rid)
                created.append(str(rid))
        except HTTPException as e:
            bad(f"Record {n}: {e.detail}", e.status_code)
    return {"created": _records_by_id(conn, t["id"], created, cell_format, field_key),
            "updated": _records_by_id(conn, t["id"], updated, cell_format, field_key)}, t


def delete_records(conn, user, ref, ids: list[str], base=None) -> tuple[list, dict]:
    t = _writable_table(conn, user, ref, base)
    if not ids:
        bad("Send at least one record id")
    if len(ids) > MAX_BATCH:
        bad(f"At most {MAX_BATCH} records per request")
    for rid in ids:
        if not UUID_RE.match(str(rid)):
            bad(f"'{rid}' isn't a record id")
        recordops.delete(conn, t["id"], str(rid), user["id"])
    return [str(i) for i in ids], t


# ---------------------------------------------------------------- schema changes

def _field_options(conn, user, ftype, options):
    o = dict(options or {})
    if ftype in store.SELECT_TYPES and isinstance(o.get("choices"), list):
        o["choices"] = [c if isinstance(c, dict) else {"name": str(c), "color": store.PALETTE[i % len(store.PALETTE)]}
                        for i, c in enumerate(o["choices"])]
    if ftype == "link":
        target = o.pop("linked_table", None) or o.get("foreignTableId") or o.pop("linked_table_id", None)
        if not target:
            bad("A link field needs options.linked_table (the table to link to)")
        o["foreignTableId"] = str(_writable_table(conn, user, target)["id"])
    if ftype == "date" and "include_time" in o:
        o["includeTime"] = bool(o.pop("include_time"))
    return o


def add_field(conn, user, ref, name, ftype, options=None, base=None) -> dict:
    t = _writable_table(conn, user, ref, base)
    if t["kind"] == "remote":
        bad("A linked table's fields come from its source link")
    name = (name or "").strip()
    if not name:
        bad("The field needs a name")
    if any(f["name"].lower() == name.lower() for f in store.get_fields(conn, t["id"])):
        bad(f"{t['name']} already has a field called '{name}'")
    opts = store.prepare_field(conn, t, ftype, _field_options(conn, user, ftype, options), user["id"])
    if ftype == "link":
        f = store.create_link_pair(conn, t, name, {**opts, "symmetric": (options or {}).get("symmetric", True)}, user["id"])
    else:
        f = store.create_field(conn, t["id"], name, ftype, opts)
    return field_out(next(x for x in Engine(conn).describe(t["id"]) if str(x["id"]) == str(f["id"])))


def create_table(conn, user, name, base=None, fields=None) -> dict:
    name = (name or "").strip()
    if not name:
        bad("The table needs a name")
    b = resolve_base(conn, user, base)
    t = store.create_table(conn, user["id"], b["id"], name, "generic")
    if fields:
        existing = store.get_fields(conn, t["id"])
        primary = next(f for f in existing if f["is_primary"])
        first = fields[0]
        ftype = first.get("type") or "text"
        if ftype not in store.PRIMARY_TYPES or ftype in ("autonumber", "formula"):
            ftype = "text"
        opts = store.prepare_field(conn, t, ftype, _field_options(conn, user, ftype, first.get("options")), user["id"]) if ftype != "text" else {}
        conn.execute("UPDATE fields SET name = %s, type = %s, options = %s WHERE id = %s",
                     ((first.get("name") or "Name").strip(), ftype, store.Jsonb(opts), primary["id"]))
        for f in existing:  # replace the starter Notes / Status fields with the ones asked for
            if not f["is_primary"] and f["type"] != "record_id":
                store.delete_field_cleanup(conn, f)
                conn.execute("DELETE FROM fields WHERE id = %s", (f["id"],))
        for f in fields[1:]:
            add_field(conn, user, str(t["id"]), f.get("name"), f.get("type", "text"), f.get("options"))
    return table_schema(conn, user, str(t["id"]))


def search_all(conn, user, query: str, limit: int = 20) -> list[dict]:
    q = (query or "").strip().lower()
    if not q:
        bad("Say what to search for")
    eng = Engine(conn)
    out = []
    for b in list_bases(conn, user):
        for t in b["tables"]:
            if t["kind"] == "remote":
                continue
            fields = [f for f in eng.describe(t["id"]) if f["type"] not in HIDDEN_TYPES]
            prim = next((f for f in fields if f.get("is_primary")), None)
            for r in eng.records(t["id"]):
                hit = next((f for f in fields if q in display_text(eng, f, r["data"].get(str(f["id"]))).lower()), None)
                if hit:
                    out.append({"base": b["name"], "table": t["name"], "table_id": t["id"], "id": str(r["id"]),
                                "title": display_text(eng, prim, r["data"].get(str(prim["id"]))) if prim else "",
                                "matched_field": hit["name"],
                                "match": display_text(eng, hit, r["data"].get(str(hit["id"])))[:200]})
                    if len(out) >= limit:
                        return out
    return out


def sync_table(user, ref, base=None) -> dict:
    from .db import connection
    with connection() as conn:
        t = _writable_table(conn, user, ref, base)
    if t["kind"] not in store.DAV_KINDS:
        bad(f"{t['name']} isn't synced with a calendar or address book")
    return sync.sync_table(t["id"])
