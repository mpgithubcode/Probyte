"""Combined databases: several tables shown as one through a field mapping, and writing back to the sources."""
from app.calc.compute import Engine
from app.data import access
from app.data.records import clean_record, computed_record, list_records
from app.data.schema import base_allowed, get_fields, not_found, SELECT_TYPES
from app.formats.ical import SEMANTIC_KEY


def get_combo(conn, combo_id, user_id=None, need=None):
    c = conn.execute("SELECT * FROM combos WHERE id = %s", (combo_id,)).fetchone()
    if not c or (user_id and not base_allowed(c["base_id"])):
        not_found("Combined database not found")
    if user_id:
        access.check(conn, c["base_id"], user_id, need, "Combined database")
    return c


# What a field means, for lining up fields of different tables in a combined database. Synced fields know it
# (SEMANTIC_KEY); other fields get it from a common name, if their type fits that meaning.
NAME_MEANINGS = {
    "start": ({"start", "start date", "starts", "begin", "begins", "from", "start time"}, {"date"}),
    "end": ({"end", "end date", "ends", "due", "due date", "deadline", "until", "finish", "end time"}, {"date"}),
    "status": ({"status", "state", "stage"}, {"single_select"}),
    "description": ({"description", "notes", "note", "details", "comments"}, {"text", "long_text"}),
    "tags": ({"tags", "tag", "labels", "label", "categories", "category"}, {"multi_select", "single_select"}),
    "location": ({"location", "place", "where", "venue"}, {"text", "long_text"}),
    "priority": ({"priority"}, {"single_select"}),
    "email": ({"email", "e-mail", "mail"}, {"email", "text"}),
    "phone": ({"phone", "telephone", "mobile", "phone number"}, {"text", "phone"}),
    "company": ({"company", "organisation", "organization", "org"}, {"text"}),
}


def _semantic(f):
    key = f["system_key"]
    if key == "remote":  # a linked table's field: the role it has in its source (a calendar's start, status…)
        key = (f["options"] or {}).get("_source_key")
        if key and SEMANTIC_KEY.get(key):
            return SEMANTIC_KEY[key]
    elif key:
        return SEMANTIC_KEY.get(key)
    name = f["name"].strip().lower()
    return next((sem for sem, (names, types) in NAME_MEANINGS.items() if name in names and f["type"] in types), None)


def build_combo_fields(conn, table_ids: list[str]) -> list[dict]:
    """Auto-map fields of several tables onto one unified field list."""
    import uuid as _uuid

    unified: list[dict] = []
    for tid in table_ids:
        for f in get_fields(conn, tid):
            if f["type"] == "button":
                continue
            sem, lname = _semantic(f), f["name"].strip().lower()
            match = None
            for u in unified:
                if tid in u["mapping"]:
                    continue
                if f["is_primary"] and u["_primary"]:  # every table's title column lines up
                    match = u
                    break
                if (sem and u.get("_sem") == sem) or (not sem and not u.get("_sem") and u["_lname"] == lname and u["_type"] == f["type"]):
                    match = u
                    break
            if match:
                match["mapping"][tid] = str(f["id"])
                if f["name"] not in match["_names"]:
                    match["_names"].append(f["name"])
            else:
                unified.append({"id": str(_uuid.uuid4()), "mapping": {tid: str(f["id"])}, "_sem": sem,
                                "_lname": lname, "_type": f["type"], "_names": [f["name"]], "_primary": f["is_primary"]})
    # Primary (title) fields first, then fields shared by most sources.
    unified.sort(key=lambda u: (not u["_primary"], -len(u["mapping"])))
    return [{"id": u["id"], "name": " / ".join(u["_names"]), "mapping": u["mapping"]} for u in unified]


def resolve_combo(conn, combo, engine=None):
    """Expand a combo into its sources and derived unified fields (type/options from source fields)."""
    eng = engine or Engine(conn)
    sources, field_index = [], {}
    for s in combo["sources"]:
        t = conn.execute("SELECT id, name, kind FROM tables WHERE id = %s AND user_id IS NOT DISTINCT FROM %s AND trashed_at IS NULL",
                         (s["table_id"], combo["user_id"])).fetchone()
        if not t:
            continue
        sources.append({"table_id": str(t["id"]), "name": t["name"], "kind": t["kind"], "color": s.get("color")})
        for f in eng.describe(t["id"]):
            field_index[str(f["id"])] = f

    live = {s["table_id"] for s in sources}
    fields = [{
        "id": "__source", "name": "Source", "type": "single_select", "readonly": True, "computed": True,
        "options": {"choices": [{"name": s["name"], "color": s["color"]} for s in sources]}, "mapping": {},
    }, {"id": "__id", "name": "Record ID", "type": "record_id", "readonly": True, "computed": True, "options": {}, "mapping": {}}]
    for i, u in enumerate(combo["fields"]):
        mapping = {tid: fid for tid, fid in u["mapping"].items() if tid in live and fid in field_index}
        src = [field_index[fid] for fid in mapping.values()]
        ftype = src[0]["type"] if src else "text"
        options = dict(src[0]["options"]) if src else {}
        if ftype in SELECT_TYPES:
            seen, choices = set(), []
            for f in src:
                for c in f["options"].get("choices", []):
                    if c["name"] not in seen:
                        seen.add(c["name"])
                        choices.append(c)
            options["choices"] = choices
        if ftype == "date":
            options["includeTime"] = any(f["options"].get("includeTime") for f in src)
        computed = any(f["computed"] for f in src)
        fields.append({"id": u["id"], "name": u["name"], "type": ftype, "options": options, "mapping": mapping,
                       "is_primary": i == 0, "readonly": not mapping or computed, "computed": computed})
    return sources, fields


def _project(fields, src_name, tid, record):
    data = {"__source": src_name, "__id": str(record.get("source_id") or record["id"])}  # (a linked record: its source's id)
    for f in fields:
        fid = f["mapping"].get(tid)
        if fid is not None:
            data[f["id"]] = record["data"].get(fid)
    return {**clean_record(record), "data": data}


def combo_records(conn, combo, engine=None, only: set | None = None):
    """Records of a combined database, projected onto its fields. `only`: just these source tables."""
    eng = engine or Engine(conn)
    sources, fields = resolve_combo(conn, combo, eng)
    out = []
    for s in sources:
        if only is not None and str(s["table_id"]) not in only:
            continue
        for r in list_records(conn, s["table_id"], eng):
            out.append(_project(fields, s["name"], s["table_id"], r))
    out.sort(key=lambda r: r["created_at"])
    return out


def combo_refs(conn, combo, engine, only: set | None = None):
    refs = {"records": {}, "users": {}}
    for s in combo["sources"]:
        if only is not None and str(s["table_id"]) not in only:
            continue
        try:
            recs = engine.records(s["table_id"])
        except Exception:
            continue
        r = engine.refs(s["table_id"], recs)
        refs["records"].update(r["records"])
        refs["users"].update(r["users"])
    return refs


def project_record(conn, combo, record):
    """Project a single source record (computed) into the combo's unified shape."""
    sources, fields = resolve_combo(conn, combo)
    tid = str(record["table_id"])
    src = next((s for s in sources if s["table_id"] == tid), None)
    full = computed_record(conn, record["id"], tid) or clean_record(record)
    return _project(fields, src["name"] if src else None, tid, full)


def unified_to_source(conn, combo, table_id: str, data: dict) -> dict:
    _, fields = resolve_combo(conn, combo)
    out = {}
    for f in fields:
        if f["id"] in data and table_id in f["mapping"]:
            out[f["mapping"][table_id]] = data[f["id"]]
    return out
