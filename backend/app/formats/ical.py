"""Conversion between Probyte records and iCalendar VTODO / VEVENT components.

Record values for system fields are keyed by `system_key`:
  dates are stored as "YYYY-MM-DD" (all-day) or ISO-8601 UTC "YYYY-MM-DDTHH:MM:SSZ".
"""
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from icalendar import Alarm, Calendar, Event, Todo
from icalendar.prop import vRecur

# Floating (timezone-less) times from the server are interpreted in this zone,
# and all-day values are derived in it too.
DEFAULT_TZ = ZoneInfo(os.environ.get("DEFAULT_TZ", "UTC"))

PALETTE = ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#a855f7", "#14b8a6", "#ec4899", "#64748b", "#84cc16", "#f97316"]

TASK_STATUS = {"NEEDS-ACTION": "To Do", "IN-PROCESS": "In Progress", "COMPLETED": "Completed", "CANCELLED": "Cancelled"}
EVENT_STATUS = {"TENTATIVE": "Tentative", "CONFIRMED": "Confirmed", "CANCELLED": "Cancelled"}
# After the fact: iCalendar has no such statuses, so they're stored as CONFIRMED plus X-PROBYTE-STATUS.
EVENT_OUTCOMES = {"ATTENDED": "Attended", "MISSED": "Missed"}
OUTCOME_PROP = "X-PROBYTE-STATUS"
PRIORITY_CHOICES = ["High", "Medium", "Low"]
# Reminder: when the phone alerts (an iCalendar VALARM), before an event's start or a task's due date (or start)
REMINDER_CHOICES = ["At time", "5 minutes before", "15 minutes before", "30 minutes before", "1 hour before",
                    "2 hours before", "1 day before", "2 days before", "1 week before"]


def _choices(names, colors=None):
    return [{"name": n, "color": (colors or PALETTE)[i % len(colors or PALETTE)]} for i, n in enumerate(names)]


# (system_key, name, type, options, is_primary)
SYSTEM_FIELDS = {
    "tasks": [
        ("summary", "Title", "text", {}, True),
        ("done", "Completed", "checkbox", {}, False),
        ("status", "Status", "single_select",
         {"choices": _choices(TASK_STATUS.values(), ["#64748b", "#3b82f6", "#22c55e", "#ef4444"])}, False),
        ("priority", "Priority", "single_select", {"choices": _choices(PRIORITY_CHOICES, ["#ef4444", "#f59e0b", "#3b82f6"])}, False),
        ("due", "Due", "date", {"includeTime": True}, False),
        ("dtstart", "Start", "date", {"includeTime": True}, False),
        ("alarm", "Reminder", "single_select", {"choices": _choices(REMINDER_CHOICES, ["#f59e0b"])}, False),
        ("description", "Description", "long_text", {}, False),
        ("categories", "Tags", "multi_select", {"choices": []}, False),
        ("started", "Started At", "date", {"includeTime": True}, False),
        ("completed", "Completed At", "date", {"includeTime": True}, False),
        ("rrule", "Repeat", "recurrence", {}, False),
    ],
    "events": [
        ("summary", "Title", "text", {}, True),
        ("done", "Completed", "checkbox", {}, False),   # events: ticked once they're over (not stored on the server)
        ("dtstart", "Start", "date", {"includeTime": True}, False),
        ("dtend", "End", "date", {"includeTime": True}, False),
        ("all_day", "All Day", "checkbox", {}, False),
        ("alarm", "Reminder", "single_select", {"choices": _choices(REMINDER_CHOICES, ["#f59e0b"])}, False),
        ("location", "Location", "text", {}, False),
        ("status", "Status", "single_select",
         {"choices": _choices([*EVENT_STATUS.values(), *EVENT_OUTCOMES.values()], ["#f59e0b", "#22c55e", "#ef4444", "#14b8a6", "#64748b"])}, False),
        ("description", "Description", "long_text", {}, False),
        ("categories", "Tags", "multi_select", {"choices": []}, False),
        ("rrule", "Repeat", "recurrence", {}, False),
    ],
}

# One calendar holding both VTODO and VEVENT. "End / Due" is DUE for tasks and DTEND for events.
TYPE_CHOICES = {"tasks": "Task", "events": "Event"}
SYSTEM_FIELDS["mixed"] = [
    ("summary", "Title", "text", {}, True),
    ("done", "Completed", "checkbox", {}, False),
    ("component", "Type", "single_select", {"choices": _choices(TYPE_CHOICES.values(), ["#6366f1", "#14b8a6"])}, False),
    ("status", "Status", "single_select",
     {"choices": _choices([*TASK_STATUS.values(), "Tentative", "Confirmed", *EVENT_OUTCOMES.values()],
                          ["#64748b", "#3b82f6", "#22c55e", "#ef4444", "#f59e0b", "#14b8a6", "#10b981", "#94a3b8"])}, False),
    ("priority", "Priority", "single_select", {"choices": _choices(PRIORITY_CHOICES, ["#ef4444", "#f59e0b", "#3b82f6"])}, False),
    ("dtstart", "Start", "date", {"includeTime": True}, False),
    ("dtend", "End / Due", "date", {"includeTime": True}, False),
    ("all_day", "All Day", "checkbox", {}, False),
    ("alarm", "Reminder", "single_select", {"choices": _choices(REMINDER_CHOICES, ["#f59e0b"])}, False),
    ("location", "Location", "text", {}, False),
    ("description", "Description", "long_text", {}, False),
    ("categories", "Tags", "multi_select", {"choices": []}, False),
    ("started", "Started At", "date", {"includeTime": True}, False),
    ("completed", "Completed At", "date", {"includeTime": True}, False),
    ("rrule", "Repeat", "recurrence", {}, False),
]

# Synced fields that are kept in Probyte only (none at the moment).
LOCAL_KEYS: set[str] = set()

from app.formats.vcard import CONTACT_FIELDS  # noqa: E402

SYSTEM_FIELDS["contacts"] = CONTACT_FIELDS

# Files tables (files.py): the file's own fields, filled from the storage. Name and Folder rename / move the file.
FILE_KINDS = ["Image", "Video", "Audio", "PDF", "Document", "Spreadsheet", "Presentation", "Text", "Code", "Archive", "Other"]
SYSTEM_FIELDS["files"] = [
    ("name", "Name", "text", {}, True),
    ("file", "File", "attachment", {}, False),
    ("folder", "Folder", "text", {}, False),
    ("kind", "Kind", "single_select", {"choices": _choices(FILE_KINDS, ["#3b82f6", "#a855f7", "#ec4899", "#ef4444", "#2563eb", "#22c55e",
                                                                        "#f59e0b", "#64748b", "#14b8a6", "#84cc16", "#94a3b8"])}, False),
    ("size", "Size", "number", {"numberFormat": "bytes"}, False),
    ("modified", "Modified", "date", {"includeTime": True}, False),
    ("mime", "Type", "text", {}, False),
    ("text", "Text", "long_text", {}, False),
]

STARTED_PROP = "X-PROBYTE-STARTED"  # no standard iCalendar property for "work started"


def component_kind(raw: str) -> str:
    """'tasks' or 'events' depending on the main component of an iCalendar body."""
    return "tasks" if "BEGIN:VTODO" in raw.upper() else "events"


def to_mixed(kind: str, vals: dict) -> dict:
    """Kind-specific values -> values of a mixed table."""
    vals = dict(vals)
    if kind == "tasks":
        vals["dtend"] = vals.pop("due", None)
        vals["all_day"] = is_date_only(vals["dtend"] or vals.get("dtstart"))
        vals["rrule"] = _rename_override_key(vals.get("rrule"), "due", "dtend")
    vals["component"] = TYPE_CHOICES[kind]
    return vals


def from_mixed(vals: dict) -> tuple[str, dict]:
    """Values of a mixed table -> (kind, kind-specific values)."""
    vals = dict(vals)
    kind = "events" if vals.pop("component", None) == "Event" else "tasks"
    if kind == "tasks":
        vals["due"] = vals.pop("dtend", None)
        if vals.pop("all_day", None):  # an all-day task: dates without a time
            for k in ("due", "dtstart"):
                d = parse_value_dt(vals.get(k))
                if d is not None:
                    vals[k] = to_value(as_date(d))
        if "rrule" in vals:
            vals["rrule"] = _rename_override_key(vals["rrule"], "dtend", "due")
    return kind, vals


def _rename_override_key(value, old: str, new: str):
    from app.data import recur
    ovs = recur.overrides(value)
    if not ovs:
        return value
    for o in ovs:
        if old in o["set"]:
            o["set"][new] = o["set"].pop(old)
    rule, exdates = recur.parse(value)
    return recur.compose(rule, exdates, ovs)


# Used to line up fields of different tables when building a combined database.
SEMANTIC_KEY = {
    "component": "type",
    "summary": "title", "dtstart": "start", "due": "end", "dtend": "end", "status": "status",
    "description": "description", "categories": "tags", "location": "location",
    "priority": "priority", "all_day": "all_day",
    "rrule": "repeat",
    "fn": "title", "note": "description", "email": "email", "phone": "phone", "org": "company",
}


# ---------------------------------------------------------------- value helpers

def is_date_only(v) -> bool:
    return isinstance(v, str) and len(v) == 10


def parse_value_dt(v):
    """Record value -> date | aware datetime | None."""
    if not v or not isinstance(v, str):
        return None
    try:
        if is_date_only(v):
            return date.fromisoformat(v)
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=DEFAULT_TZ)
    except ValueError:
        return None


def to_value(d) -> str | None:
    """date | datetime -> record value string."""
    if d is None:
        return None
    if isinstance(d, datetime):
        if d.tzinfo is None:
            d = d.replace(tzinfo=DEFAULT_TZ)
        return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return d.isoformat()


def as_date(d):
    if isinstance(d, datetime):
        return d.astimezone(DEFAULT_TZ).date()
    return d


def _dt_prop(comp, name):
    p = comp.get(name)
    return p.dt if p is not None else None


def _categories(comp) -> list[str]:
    v = comp.get("categories")
    if v is None:
        return []
    out = []
    for item in v if isinstance(v, list) else [v]:
        cats = getattr(item, "cats", None)
        out += [str(c) for c in cats] if cats is not None else [c.strip() for c in str(item).split(",")]
    return [c for c in out if c]


def _priority_to_choice(p) -> str | None:
    try:
        p = int(p)
    except (TypeError, ValueError):
        return None
    if p == 0:
        return None
    return "High" if p < 5 else "Medium" if p == 5 else "Low"


def _choice_to_priority(c) -> int | None:
    return {"High": 1, "Medium": 5, "Low": 9}.get(c)


def _recurrence(comp, cal=None, kind="events") -> str | None:
    from app.data import recur
    rr = comp.get("rrule")
    if rr is None:
        return None
    rule = (rr[0] if isinstance(rr, list) else rr).to_ical().decode()
    exdates = []
    ex = comp.get("exdate")
    for item in ex if isinstance(ex, list) else [ex] if ex is not None else []:
        exdates += [to_value(d.dt) for d in getattr(item, "dts", [])]
    ovs = []
    master_summary = str(comp.get("summary", "")) or None
    for c in _exceptions(cal, comp, kind) if cal is not None else []:
        rid = to_value(c["recurrence-id"].dt)
        if str(c.get("status", "")).upper() == "CANCELLED":
            ovs.append({"rid": rid, "set": {}, "cancelled": True})
            continue
        v = _occurrence_vals(c, kind)
        if v.get("summary") == master_summary:
            v.pop("summary")
        ovs.append({"rid": rid, "set": {k: x for k, x in v.items() if x is not None}})
    return recur.compose(rule, [e for e in exdates if e], ovs)


def _exceptions(cal, master, kind) -> list:
    """The RECURRENCE-ID components (single changed occurrences) of a series."""
    name = "VTODO" if kind == "tasks" else "VEVENT"
    uid = str(master.get("uid", ""))
    return [c for c in cal.walk(name) if c is not master and c.get("recurrence-id") is not None and str(c.get("uid", "")) == uid]


def _occurrence_vals(c, kind) -> dict:
    """The values Probyte shows for one occurrence component."""
    out = {"summary": str(c.get("summary", "")) or None, "dtstart": to_value(_dt_prop(c, "dtstart"))}
    if kind == "tasks":
        out["due"] = to_value(_dt_prop(c, "due"))
        return out
    start, end = _dt_prop(c, "dtstart"), _dt_prop(c, "dtend")
    if end is None and c.get("duration") is not None and start is not None:
        end = start + c.get("duration").dt
    if isinstance(start, date) and not isinstance(start, datetime) and end is not None:
        end = max(as_date(end) - timedelta(days=1), start)  # Probyte stores an inclusive end date
    out["dtend"] = to_value(end)
    return out


def _write_dt(comp, name, value):
    d = parse_value_dt(value) if isinstance(value, str) else value
    if isinstance(d, datetime):
        _set(comp, name, d.astimezone(timezone.utc))
    elif d is not None:
        _set(comp, name, d)
    else:
        _set(comp, name, None)


def _apply_overrides(cal, master, kind, value):
    """Make the series' RECURRENCE-ID components match the overrides in a repeat value."""
    from app.data import recur
    want = {recur.rid_key(o["rid"]): o for o in recur.overrides(value)} if recur.parse(value)[0] else {}
    for c in _exceptions(cal, master, kind):
        o = want.pop(to_value(c["recurrence-id"].dt), None)
        if o is None:
            cal.subcomponents.remove(c)  # reset to the series
        else:
            _write_override(c, kind, o, master, new=False)
    for rid, o in want.items():
        if not rid:
            continue
        c = Todo() if kind == "tasks" else Event()
        c.add("uid", str(master.get("uid")))
        d = parse_value_dt(rid)
        if isinstance(d, datetime):
            c.add("recurrence-id", d.astimezone(timezone.utc))
        else:
            c.add("recurrence-id", d, parameters={"VALUE": "DATE"})
        for prop in ("location", "description", "categories", "priority"):
            if master.get(prop) is not None:
                c.add(prop, master.get(prop))
        _write_override(c, kind, o, master, new=True)
        cal.add_component(c)


def _write_override(c, kind, o, master, new: bool):
    rid = to_value(c["recurrence-id"].dt)
    st = o.get("set") or {}
    cur = {} if new else _occurrence_vals(c, kind)
    # Unchanged keys keep what the occurrence has (or, for a new one, the series' own timing at that date).
    m_start, m_end = _dt_prop(master, "dtstart"), _dt_prop(master, "dtend" if kind != "tasks" else "due")
    start = st.get("dtstart") or cur.get("dtstart") or rid
    want = {"summary": st.get("summary") or cur.get("summary") or str(master.get("summary", "")), "dtstart": start}
    end_key = "due" if kind == "tasks" else "dtend"
    end = st.get(end_key) or cur.get(end_key)
    if end is None and m_start is not None and m_end is not None:
        s0 = parse_value_dt(start)
        if isinstance(s0, datetime) and isinstance(m_start, datetime) and isinstance(m_end, datetime):
            end = to_value(s0 + (m_end - m_start))
        elif not isinstance(s0, datetime):
            end = to_value(as_date(s0) + (as_date(m_end) - as_date(m_start)) - (timedelta(days=1) if kind != "tasks" else timedelta()))
    want[end_key] = end
    if not new and all(want[k] == cur.get(k) for k in want) and not o.get("cancelled") and str(c.get("status", "")).upper() != "CANCELLED":
        return
    now = datetime.now(timezone.utc)
    _set(c, "dtstamp", now)
    _set(c, "last-modified", now)
    _set(c, "sequence", int(c.get("sequence", 0)) + (0 if new else 1))
    _set(c, "summary", want["summary"])
    _write_dt(c, "dtstart", want["dtstart"])
    if kind == "tasks":
        _write_dt(c, "due", want["due"])
    else:
        _set(c, "duration", None)
        e = parse_value_dt(want["dtend"]) if want["dtend"] else None
        if e is not None and not isinstance(e, datetime):
            e = e + timedelta(days=1)  # exclusive
        _write_dt(c, "dtend", e)
    if o.get("cancelled"):
        _set(c, "status", "CANCELLED")
    elif str(c.get("status", "")).upper() == "CANCELLED":
        _set(c, "status", master.get("status"))


def _set_recurrence(comp, value):
    from app.data import recur
    rule, exdates = recur.parse(value)
    _set(comp, "exdate", None)
    if not rule:
        _set(comp, "rrule", None)
        return
    old = comp.get("rrule")
    if old is None or (old[0] if isinstance(old, list) else old).to_ical().decode().upper() != rule.upper():
        _set(comp, "rrule", None)
        comp.add("rrule", vRecur.from_ical(rule))
    for v in exdates:
        d = parse_value_dt(v)
        if isinstance(d, datetime):
            comp.add("exdate", d.astimezone(timezone.utc))
        elif d is not None:
            comp.add("exdate", d, parameters={"VALUE": "DATE"})


# ---------------------------------------------------------------- parse / build

def find_component(cal: Calendar, kind: str):
    name = "VTODO" if kind == "tasks" else "VEVENT"
    comps = [c for c in cal.walk(name)]
    # Prefer the master component (no RECURRENCE-ID).
    for c in comps:
        if c.get("recurrence-id") is None:
            return c
    return comps[0] if comps else None


# ---------------------------------------------------------------- reminders (VALARM)

_UNITS = [("week", timedelta(weeks=1)), ("day", timedelta(days=1)), ("hour", timedelta(hours=1)), ("minute", timedelta(minutes=1))]


def reminder_label(before: timedelta) -> str:
    """How long before the moment an alert comes, as the Reminder field shows it ("15 minutes before")."""
    if before <= timedelta(0):
        return "At time" if before == timedelta(0) else reminder_label(-before).replace("before", "after")
    for unit, size in _UNITS:
        if before % size == timedelta(0):
            n = before // size
            return f"{n} {unit}{'s' if n != 1 else ''} before"
    return f"{round(before.total_seconds() / 60)} minutes before"


def reminder_offset(label) -> timedelta | None:
    """The Reminder field's text -> how long before (None when empty or not understood)."""
    if not label:
        return None
    text = str(label).strip().lower()
    if text in ("at time", "at start", "on time"):
        return timedelta(0)
    parts = text.split()
    if len(parts) == 3 and parts[2] in ("before", "after") and parts[0].isdigit():
        size = next((sz for unit, sz in _UNITS if parts[1].rstrip("s") == unit), None)
        if size is not None:
            d = int(parts[0]) * size
            return d if parts[2] == "before" else -d
    return None


def _alarm_anchor(kind: str, comp):
    """The moment a task's or event's alerts count from: an event's start, a task's due date (or start).
    All-day dates count from 09:00 that day."""
    d = _dt_prop(comp, "dtstart") if kind == "events" else (_dt_prop(comp, "due") or _dt_prop(comp, "dtstart"))
    if d is not None and not isinstance(d, datetime):
        d = datetime(d.year, d.month, d.day, 9, tzinfo=DEFAULT_TZ)
    if isinstance(d, datetime) and d.tzinfo is None:
        d = d.replace(tzinfo=DEFAULT_TZ)
    return d


def _alarms(comp) -> list:
    return [c for c in comp.subcomponents if c.name == "VALARM" and c.get("trigger") is not None]


def _reminder(kind: str, comp) -> str | None:
    """The first time-based alert of an item, as a Reminder value."""
    for a in _alarms(comp):
        trig = a.get("trigger").dt
        if isinstance(trig, timedelta):
            related_end = str(a.get("trigger").params.get("RELATED", "START")).upper() == "END"
            if kind == "events" and related_end:
                start, end = _dt_prop(comp, "dtstart"), _dt_prop(comp, "dtend")
                if isinstance(start, datetime) and isinstance(end, datetime):
                    trig = trig + (end - start)
            return reminder_label(-trig)
        anchor = _alarm_anchor(kind, comp)
        if isinstance(trig, datetime) and anchor is not None:
            if trig.tzinfo is None:
                trig = trig.replace(tzinfo=DEFAULT_TZ)
            return reminder_label(anchor - trig)
    return None


def _set_reminder(kind: str, comp, label):
    """Write the Reminder as the item's one alert. Events: relative to the start (doesn't go stale when the event
    moves). Tasks: at a set time before the due date, the way Apple Reminders writes them (refreshed on each write)."""
    for a in _alarms(comp):
        comp.subcomponents.remove(a)
    before = reminder_offset(label)
    if before is None:
        return
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", str(comp.get("summary", "")) or "Reminder")
    if kind == "events":
        alarm.add("trigger", -before)
    else:
        anchor = _alarm_anchor(kind, comp)
        if anchor is None:
            return  # a task without a date can't remind
        alarm.add("trigger", (anchor - before).astimezone(timezone.utc), parameters={"VALUE": "DATE-TIME"})  # an absolute TRIGGER must say so
    comp.add_component(alarm)


def ical_to_values(kind: str, raw: str) -> tuple[str | None, dict]:
    """Parse an iCalendar body. Returns (uid, {system_key: value})."""
    cal = Calendar.from_ical(raw)
    comp = find_component(cal, kind)
    if comp is None:
        return None, {}
    uid = str(comp.get("uid")) if comp.get("uid") else None
    vals = {
        "summary": str(comp.get("summary", "")) or None,
        "description": str(comp.get("description", "")) or None,
        "categories": _categories(comp),
        "rrule": _recurrence(comp, cal, kind),
        "location": str(comp.get("location", "")) or None,       # VTODOs can have one too
        "alarm": _reminder(kind, comp),
        "last_modified": to_value(_dt_prop(comp, "last-modified") or _dt_prop(comp, "dtstamp")),  # not a field
    }
    status = str(comp.get("status", "")).upper()
    if kind == "tasks":
        vals["status"] = TASK_STATUS.get(status, "To Do")
        vals["done"] = vals["status"] == "Completed"
        vals["priority"] = _priority_to_choice(comp.get("priority"))
        vals["due"] = to_value(_dt_prop(comp, "due"))
        vals["dtstart"] = to_value(_dt_prop(comp, "dtstart"))
        vals["completed"] = to_value(_dt_prop(comp, "completed"))
        started = comp.get(STARTED_PROP)
        vals["started"] = to_value(parse_value_dt(str(started))) if started else None
    else:
        start = _dt_prop(comp, "dtstart")
        end = _dt_prop(comp, "dtend")
        all_day = isinstance(start, date) and not isinstance(start, datetime)
        if end is None and comp.get("duration") is not None and start is not None:
            end = start + comp.get("duration").dt
        if all_day:
            # DTEND is exclusive for all-day events; Probyte stores an inclusive end date.
            end = (as_date(end) - timedelta(days=1)) if end is not None else start
            if end < start:
                end = start
        vals["dtstart"] = to_value(start)
        vals["dtend"] = to_value(end)
        vals["all_day"] = all_day
        vals["location"] = str(comp.get("location", "")) or None
        vals["status"] = EVENT_STATUS.get(status) if status else None
        outcome = str(comp.get(OUTCOME_PROP, "")).upper()
        if outcome in EVENT_OUTCOMES:
            vals["status"] = EVENT_OUTCOMES[outcome]
    return uid, vals


def _set(comp, name, value, **params):
    if name in comp:
        del comp[name]
    if value is None or value == "" or value == []:
        return
    comp.add(name, value, parameters=params or None)


def build_ical(kind: str, uid: str, vals: dict, raw: str | None = None) -> str:
    """Build (or update, preserving unknown properties of `raw`) an iCalendar body."""
    cal = comp = None
    if raw:
        try:
            cal = Calendar.from_ical(raw)
            comp = find_component(cal, kind)
        except Exception:
            cal = comp = None
    if comp is None:
        cal = Calendar()
        cal.add("prodid", "-//Probyte//EN")
        cal.add("version", "2.0")
        comp = Todo() if kind == "tasks" else Event()
        cal.add_component(comp)

    now = datetime.now(timezone.utc)
    _set(comp, "uid", uid)
    _set(comp, "dtstamp", now)
    _set(comp, "last-modified", now)
    _set(comp, "sequence", int(comp.get("sequence", 0)) + 1 if raw else 0)
    _set(comp, "summary", vals.get("summary") or "")
    _set(comp, "description", vals.get("description"))
    _set(comp, "categories", [str(c) for c in (vals.get("categories") or [])])
    if "rrule" in vals:
        _set_recurrence(comp, vals.get("rrule"))
        _apply_overrides(cal, comp, kind, vals.get("rrule"))

    if kind == "tasks":
        # The Completed checkbox decides whether the task is done; Status keeps the finer state.
        if vals.get("done") is True and vals.get("status") != "Completed":
            vals = {**vals, "status": "Completed"}
        elif vals.get("done") is False and vals.get("status") == "Completed":
            vals = {**vals, "status": "To Do"}
        status = {v: k for k, v in TASK_STATUS.items()}.get(vals.get("status"), "NEEDS-ACTION")
        _set(comp, "status", status)
        if status == "COMPLETED":
            done = parse_value_dt(vals.get("completed")) or now
            if not isinstance(done, datetime):
                done = datetime(done.year, done.month, done.day, tzinfo=DEFAULT_TZ)
            _set(comp, "completed", done.astimezone(timezone.utc))  # RFC 5545: COMPLETED must be UTC
            _set(comp, "percent-complete", 100)
        else:
            _set(comp, "completed", None)
            if "percent-complete" in comp and int(comp.get("percent-complete", 0)) == 100:
                _set(comp, "percent-complete", None)
        _set(comp, STARTED_PROP, to_value(parse_value_dt(vals.get("started"))))
        if "location" in vals:
            _set(comp, "location", vals.get("location"))
        _set(comp, "priority", _choice_to_priority(vals.get("priority")))
        _set(comp, "due", parse_value_dt(vals.get("due")))
        _set(comp, "dtstart", parse_value_dt(vals.get("dtstart")))
    else:
        start = parse_value_dt(vals.get("dtstart"))
        end = parse_value_dt(vals.get("dtend"))
        if start is None:
            start = date.today()
        all_day = bool(vals.get("all_day")) or (isinstance(start, date) and not isinstance(start, datetime))
        if all_day:
            start = as_date(start)
            end = as_date(end) if end is not None else start
            if end < start:
                end = start
            end = end + timedelta(days=1)  # exclusive
        else:
            if not isinstance(start, datetime):
                start = datetime(start.year, start.month, start.day, tzinfo=DEFAULT_TZ)
            if end is not None and not isinstance(end, datetime):
                end = datetime(end.year, end.month, end.day, 23, 59, tzinfo=DEFAULT_TZ)
            if end is None or end < start:
                end = start + timedelta(hours=1)
        _set(comp, "duration", None)
        _set(comp, "dtstart", start)
        _set(comp, "dtend", end)
        _set(comp, "location", vals.get("location"))
        outcome = {v: k for k, v in EVENT_OUTCOMES.items()}.get(vals.get("status"))
        status = "CONFIRMED" if outcome else {v: k for k, v in EVENT_STATUS.items()}.get(vals.get("status"))
        _set(comp, "status", status)
        _set(comp, OUTCOME_PROP, outcome)

    if "alarm" in vals:
        # an event's alert is only rewritten when the Reminder changed (other apps' alerts stay as they were);
        # a task's is written fresh each time, as its time follows the due date
        if kind == "tasks" or _reminder(kind, comp) != (vals.get("alarm") or None):
            _set_reminder(kind, comp, vals.get("alarm"))
    return cal.to_ical().decode()


def new_uid() -> str:
    return f"{uuid.uuid4()}@probyte"
