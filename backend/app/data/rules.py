"""Field rules set in the field editor: required, unique, and default values."""
from datetime import datetime, timezone

from app.calc.compute import COMPUTED_TYPES
from app.core.db import Jsonb
from app.data.schema import bad
from app.formats.ical import DEFAULT_TZ


RULE_TYPES = {"text", "long_text", "number", "rating", "email", "url", "phone", "date", "single_select", "multi_select",
              "checkbox", "user", "link", "attachment", "recurrence"}
UNIQUE_TYPES = {"text", "number", "email", "url", "phone", "date"}
DEFAULT_TYPES = {"text", "long_text", "number", "rating", "email", "url", "phone", "date", "single_select", "multi_select",
                 "checkbox", "user"}


def is_blank(v) -> bool:
    return v is None or v is False or (isinstance(v, (str, list, dict)) and not (v.strip() if isinstance(v, str) else v))


def field_rules(conn, f, *, required=None, unique=None, default=None, clear_default=False) -> dict:
    """Validate the rules set in the field editor; returns the column values to store."""
    t = f["type"]
    req = bool(required) if required is not None else f.get("required", False)
    uniq = bool(unique) if unique is not None else f.get("unique_values", False)
    dv = None if clear_default else (default if default is not None else f.get("default_value"))
    req = req and t in RULE_TYPES
    uniq = uniq and t in UNIQUE_TYPES
    if dv is not None and t not in DEFAULT_TYPES:
        dv = None
    if t == "date" and isinstance(dv, str) and dv not in ("today", "now") and not dv[:10].replace("-", "").isdigit():
        bad("A date's default is “today”, “now” or a date")
    if t in ("number", "rating") and dv is not None and not (isinstance(dv, (int, float)) and not isinstance(dv, bool)):
        bad("A number's default must be a number")
    if t == "multi_select" and dv is not None and not isinstance(dv, list):
        dv = [dv]
    if is_blank(dv) and t != "checkbox":
        dv = None
    if uniq and not f.get("unique_values") and f.get("id"):
        dup = conn.execute(
            "SELECT lower(data->>%s) AS v FROM records WHERE table_id = %s AND NOT deleted AND COALESCE(data->>%s, '') <> '' "
            "GROUP BY 1 HAVING count(*) > 1 LIMIT 1", (str(f["id"]), f["table_id"], str(f["id"]))).fetchone()
        if dup:
            bad(f"{f['name']} already has the value “{dup['v']}” more than once; make the values unique first")
    return {"required": req, "unique_values": uniq, "default_value": dv}


def _resolve_default(f, dv, user_id):
    if f["type"] == "date" and dv == "today":
        return datetime.now(DEFAULT_TZ).date().isoformat()
    if f["type"] == "date" and dv == "now":
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if f["type"] == "user":
        ids = dv if isinstance(dv, list) else [dv]
        return [str(user_id) if x == "me" else x for x in ids if x and (x != "me" or user_id)]
    return dv


def apply_defaults(fields, data: dict, user_id=None) -> dict:
    """A new record's empty fields get their default value."""
    out = dict(data)
    for f in fields:
        dv = f.get("default_value")
        if dv is not None and f["type"] not in COMPUTED_TYPES and (str(f["id"]) not in out or out[str(f["id"])] in (None, "", [])):
            out[str(f["id"])] = _resolve_default(f, dv, user_id)
    return out


def check_rules(conn, table_id, fields, data: dict, keys, record_id=None):
    """Required fields can't be left empty and unique fields can't repeat a value (for the fields in keys)."""
    for f in fields:
        fid = str(f["id"])
        if fid not in keys:
            continue
        v = data.get(fid)
        if f.get("required") and is_blank(v):
            bad(f"{f['name']} is required")
        if f.get("unique_values") and not is_blank(v) and isinstance(v, list) and v and isinstance(v[0], dict):
            # several values: none of them may be one of another record's
            vals = [str(x.get("value", "")).lower() for x in v if x.get("value")]
            hit = conn.execute(
                "SELECT e->>'value' AS v FROM records r, jsonb_array_elements(CASE WHEN jsonb_typeof(r.data->%s) = 'array' "
                "THEN r.data->%s ELSE '[]'::jsonb END) e WHERE r.table_id = %s AND NOT r.deleted AND r.id IS DISTINCT FROM %s "
                "AND lower(e->>'value') = ANY(%s) LIMIT 1", (fid, fid, table_id, record_id, vals)).fetchone()
            if hit:
                bad(f"{f['name']} must be unique: another record already has “{hit['v']}”")
            continue
        if f.get("unique_values") and not is_blank(v):
            if isinstance(v, str):
                q = "lower(data->>%s) = lower(%s)"
            else:
                q, v = "data->%s = %s", Jsonb(v)
            if conn.execute(f"SELECT 1 FROM records WHERE table_id = %s AND NOT deleted AND id IS DISTINCT FROM %s AND {q} LIMIT 1",
                            (table_id, record_id, fid, v)).fetchone():
                bad(f"{f['name']} must be unique: another record already has “{data.get(fid)}”")
