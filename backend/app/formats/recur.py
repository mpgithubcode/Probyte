"""Repeat values: an iCalendar RRULE plus the dates skipped from it and single occurrences changed.

Stored as text, e.g.  "FREQ=WEEKLY;BYDAY=MO,WE"  or, with skipped dates and changed occurrences,
                      "FREQ=WEEKLY;BYDAY=MO\nEXDATE:2026-10-12T15:00:00Z,2026-10-19T15:00:00Z\n"
                      'OVERRIDE:{"rid":"2026-10-26T15:00:00Z","set":{"dtstart":"2026-10-27T16:00:00Z","summary":"Moved"}}'
EXDATE values and the dates in an override use the record value format ("YYYY-MM-DD" or UTC "YYYY-MM-DDTHH:MM:SSZ").
An override (an iCalendar RECURRENCE-ID exception) says which occurrence it replaces ("rid") and the values it
changes, keyed by a field's system key (or field id in tables without system fields); "cancelled" drops it.
"""
import json
import re
from datetime import datetime, timedelta, timezone

from dateutil.rrule import rrulestr

from app.formats.ical import DEFAULT_TZ, parse_value_dt, to_value

FREQS = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}


def parse(value) -> tuple[str | None, list[str]]:
    if not value or not isinstance(value, str):
        return None, []
    rule, exdates = None, []
    for line in value.replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        up = line.upper()
        if up.startswith("EXDATE:"):
            exdates += [x.strip() for x in line[7:].split(",") if x.strip()]
        elif rule is None:
            rule = line[6:] if up.startswith("RRULE:") else line
    return rule, exdates


def overrides(value) -> list[dict]:
    out = []
    if isinstance(value, str):
        for line in value.replace("\r", "").split("\n"):
            if line.strip().upper().startswith("OVERRIDE:"):
                try:
                    o = json.loads(line.strip()[9:])
                except ValueError:
                    continue
                if isinstance(o, dict) and o.get("rid"):
                    out.append({"rid": str(o["rid"]), "set": o.get("set") if isinstance(o.get("set"), dict) else {},
                                **({"cancelled": True} if o.get("cancelled") else {})})
    return out


def compose(rule: str | None, exdates: list[str] | None = None, overrides: list[dict] | None = None) -> str | None:
    if not rule:
        return None
    ex = sorted(dict.fromkeys(exdates or []))
    lines = [rule] + ([f"EXDATE:{','.join(ex)}"] if ex else [])
    by_rid = {o["rid"]: o for o in overrides or []}  # one per occurrence
    lines += ["OVERRIDE:" + json.dumps(o, separators=(",", ":"), sort_keys=True) for _, o in sorted(by_rid.items())]
    return "\n".join(lines)


def rid_key(v) -> str | None:
    """A recurrence id in a comparable form."""
    return to_value(parse_value_dt(v)) if v else None


def normalize(value) -> str | None:
    """Validate a repeat value; raises ValueError with a readable message."""
    if value in (None, "", []):
        return None
    if not isinstance(value, str):
        raise ValueError("Repeat must be text like FREQ=WEEKLY;BYDAY=MO")
    rule, exdates = parse(value)
    if not rule:
        return None
    parts = dict(p.split("=", 1) for p in rule.upper().split(";") if "=" in p)
    if parts.get("FREQ") not in FREQS:
        raise ValueError("Repeat needs FREQ=DAILY, WEEKLY, MONTHLY or YEARLY")
    try:
        _rule(rule, datetime(2026, 1, 1, tzinfo=DEFAULT_TZ))
    except Exception as e:
        raise ValueError(f"Invalid repeat rule: {e}")
    ex = [v for v in (to_value(parse_value_dt(x)) for x in exdates) if v]
    ovs = []
    for o in overrides(value):
        rid = rid_key(o["rid"])
        if not rid:
            continue
        clean = {k: (to_value(parse_value_dt(v)) if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}", v) else v)
                 for k, v in o["set"].items() if isinstance(k, str) and (v is None or isinstance(v, (str, bool, int, float)))}
        ovs.append({"rid": rid, "set": clean, **({"cancelled": True} if o.get("cancelled") else {})})
    return compose(rule.upper(), ex, ovs)


def _rule(rule: str, start):
    """dateutil rule anchored at `start` (date -> local midnight; datetimes step in local wall time)."""
    if isinstance(start, datetime):
        start = start.astimezone(DEFAULT_TZ)
    else:
        start = datetime(start.year, start.month, start.day, tzinfo=DEFAULT_TZ)
    # dateutil wants UNTIL in UTC for an aware start; a date-only UNTIL means "through that day".
    def fix_until(m):
        v = m.group(1)
        if re.fullmatch(r"\d{8}", v):
            d = datetime(int(v[:4]), int(v[4:6]), int(v[6:8]), 23, 59, 59, tzinfo=DEFAULT_TZ)
            return "UNTIL=" + d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        if not v.endswith("Z"):
            d = datetime.strptime(v, "%Y%m%dT%H%M%S").replace(tzinfo=DEFAULT_TZ)
            return "UNTIL=" + d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return m.group(0)
    rule = re.sub(r"UNTIL=([0-9TZ]+)", fix_until, rule.upper())
    return rrulestr(rule, dtstart=start), start


def next_after(value, anchor_value):
    """The first occurrence strictly after the anchor (a record date value), skipping EXDATEs; None if the series ended."""
    rule, exdates = parse(value)
    anchor = parse_value_dt(anchor_value)
    if not rule or anchor is None:
        return None
    rr, start = _rule(rule, anchor)
    skip = set(exdates) | {rid_key(o["rid"]) for o in overrides(value) if o.get("cancelled")}
    cur = start
    for _ in range(1000):
        cur = rr.after(cur)
        if cur is None:
            return None
        out = cur.date() if not isinstance(anchor, datetime) else cur
        if to_value(out) not in skip:
            return out
    return None


def shift(value, delta):
    """Move a record date value by a timedelta (whole days for date-only values)."""
    d = parse_value_dt(value)
    if d is None:
        return value
    if isinstance(d, datetime):
        # keep the local wall-clock time across DST changes
        local = d.astimezone(DEFAULT_TZ).replace(tzinfo=None) + delta
        return to_value(local.replace(tzinfo=DEFAULT_TZ))
    return to_value(d + timedelta(days=delta.days))


def delta_between(a_value, b) -> timedelta:
    """b (a date/datetime) minus the record date value a, in local wall-clock terms."""
    da = parse_value_dt(a_value)
    if isinstance(da, datetime) or isinstance(b, datetime):
        def to_local(x):
            return x.astimezone(DEFAULT_TZ).replace(tzinfo=None) if isinstance(x, datetime) else datetime(x.year, x.month, x.day)
        return to_local(b) - to_local(da)
    return timedelta(days=(b - da).days)


def used_one(value) -> str | None:
    """The rule after one occurrence was used up: COUNT goes down by one (None when nothing is left)."""
    rule, exdates = parse(value)
    if not rule:
        return None
    m = re.search(r"COUNT=(\d+)", rule, re.I)
    if m:
        n = int(m.group(1)) - 1
        if n <= 1:
            return None  # only the occurrence we're moving to is left: no longer repeating
        rule = rule[:m.start()] + f"COUNT={n}" + rule[m.end():]
    return compose(rule, exdates, overrides(value))
