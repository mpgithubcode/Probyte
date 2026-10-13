"""Dates that fill themselves in: a date field with a "fill in when" formula (Started date, Completed date…). After a
record changes, a stamp whose formula is true and that is still empty gets the current date (and time); one whose
formula is no longer true is cleared, if the field says so ("stampClear"). Stored like any date, so filters, sorting,
calendars and formulas use it as one."""
from datetime import datetime

from app.calc import formula as F
from app.calc.compute import Engine
from app.core.db import Jsonb
from app.formats.ical import DEFAULT_TZ, to_value


def stamp_fields(fields) -> list:
    return [f for f in fields if f["type"] == "date" and (f["options"] or {}).get("stampWhen")]


def _now(f):
    now = datetime.now(DEFAULT_TZ)
    return to_value(now) if (f["options"] or {}).get("includeTime") else to_value(now.date())


def _true(eng, f, rec, by_id) -> bool | None:
    ctx = F.Context(lambda ref: eng._formula_value(by_id.get(ref), rec), record_id=str(rec["id"]), auto_number=rec.get("seq"),
                    created=rec.get("created_at"), modified=rec.get("updated_at"))
    try:
        return F.to_bool(F.evaluate(F.parse(f["options"]["stampWhen"]), ctx))
    except (F.FormulaError, RecursionError):
        return None   # a formula that can't be worked out leaves the date as it is


def apply(conn, table_id, record_id, fields) -> dict:
    """Fill in / clear the record's stamps after a change. Returns what changed."""
    stamps = stamp_fields(fields)
    if not stamps:
        return {}
    eng = Engine(conn)
    rec = eng.record(table_id, record_id)
    if not rec:
        return {}
    by_id = {str(f["id"]): f for f in eng.fields(table_id)}
    patch = {}
    for f in stamps:
        fid, on = str(f["id"]), _true(eng, f, rec, by_id)
        if on and not rec["data"].get(fid):
            patch[fid] = _now(f)
        elif on is False and rec["data"].get(fid) and f["options"].get("stampClear"):
            patch[fid] = None
    if patch:
        conn.execute("UPDATE records SET data = data || %s WHERE id = %s", (Jsonb(patch), record_id))
    return patch


def backfill(conn, table_id, field) -> int:
    """A new (or changed) stamp: records its formula is already true for get the time they were last changed (the best
    guess there is); with stampClear, ones it isn't true for are cleared."""
    if not (field["options"] or {}).get("stampWhen"):
        return 0
    eng = Engine(conn)
    by_id = {str(f["id"]): f for f in eng.fields(table_id)}
    fid, n = str(field["id"]), 0
    for rec in eng.records(table_id):
        on = _true(eng, field, rec, by_id)
        if on and not rec["data"].get(fid):
            when = rec["updated_at"].astimezone(DEFAULT_TZ)
            value = to_value(when) if field["options"].get("includeTime") else to_value(when.date())
        elif on is False and rec["data"].get(fid) and field["options"].get("stampClear"):
            value = None
        else:
            continue
        conn.execute("UPDATE records SET data = data || %s WHERE id = %s", (Jsonb({fid: value}), rec["id"]))
        n += 1
    return n
