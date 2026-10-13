"""What tasks and events do on their own: Started At / Completed At follow the status, the Completed box drives it,
a completed repeating task moves to its next date, and a date-only start makes an all-day event."""
from datetime import datetime

from app.data.schema import now_iso
from app.formats import recur
from app.formats.ical import DEFAULT_TZ, is_date_only, to_value


def apply_event_defaults(table, fields, data: dict, patch: dict):
    """For events: a date-only start implies an all-day event unless set explicitly."""
    if table["kind"] not in ("events", "mixed"):
        return
    keys = {f["system_key"]: str(f["id"]) for f in fields if f["system_key"]}
    if table["kind"] == "mixed" and data.get(keys["component"]) != "Event":
        return
    start_id, allday_id = keys.get("dtstart"), keys.get("all_day")
    if start_id in patch and allday_id not in patch and is_date_only(data.get(start_id)):
        data[allday_id] = True


def apply_task_auto(table, fields, old: dict, data: dict, patch: dict):
    """Fill Started At / Completed At when a task's status changes (unless set explicitly in the same edit)."""
    if table["kind"] not in ("tasks", "mixed"):
        return
    keys = {f["system_key"]: str(f["id"]) for f in fields if f["system_key"]}
    if "started" not in keys or "completed" not in keys:
        return
    if table["kind"] == "mixed" and data.get(keys["component"]) != "Task":
        return
    st, s_id, c_id = keys["status"], keys["started"], keys["completed"]
    before, after = old.get(st), data.get(st)
    if st not in patch or before == after:
        return
    if after == "Completed":
        if c_id not in patch:
            data[c_id] = now_iso()
    elif before == "Completed" and c_id not in patch:
        data[c_id] = None
    if after == "In Progress" and not data.get(s_id) and s_id not in patch:
        data[s_id] = now_iso()
    if after == "To Do" and s_id not in patch:
        data[s_id] = None


def _is_task(table, keys, data) -> bool:
    return table["kind"] == "tasks" or (table["kind"] == "mixed" and data.get(keys.get("component")) == "Task")


def apply_done(table, keys, old, data, patch):
    """The Completed checkbox drives a task's status (ticked = Completed, unticked = To Do).
    Returns the patch as if the status had been set, so the usual completion rules run. Events ignore it (it's calculated)."""
    d = keys.get("done")
    if not d or d not in patch:
        return patch
    if not _is_task(table, keys, data):
        data[d] = old.get(d)
        return {k: v for k, v in patch.items() if k != d}
    st = keys.get("status")
    if st and st not in patch:
        if data.get(d) and data.get(st) != "Completed":
            data[st] = "Completed"
            return {**patch, st: "Completed"}
        if not data.get(d) and data.get(st) == "Completed":
            data[st] = "To Do"
            return {**patch, st: "To Do"}
    return patch


def sync_done(table, keys, data):
    """Keep a task's Completed checkbox in line with its status."""
    if keys.get("done") and keys.get("status") and _is_task(table, keys, data):
        data[keys["done"]] = data.get(keys["status"]) == "Completed"


def _move_on(data, rule_id, date_ids, undated_id=None) -> bool:
    """Move a repeating record to its next date: its dates shift together (start and due keep their distance) and a
    COUNT goes down by one. Without a date, the next date after today goes into undated_id. False: the series is over."""
    rule = data.get(rule_id)
    anchor = next((f for f in date_ids if f and data.get(f)), None)
    if anchor is None:
        if not undated_id:
            return False
        nxt = recur.next_after(rule, to_value(datetime.now(DEFAULT_TZ).date()))
        if nxt is None:
            return False
        data[undated_id] = to_value(nxt)
    else:
        nxt = recur.next_after(rule, data[anchor])
        if nxt is None:
            return False
        delta = recur.delta_between(data[anchor], nxt)
        for f in date_ids:
            if f and data.get(f):
                data[f] = recur.shift(data[f], delta)
    data[rule_id] = recur.used_one(rule)
    return True


def advance_recurring(table, keys, old, data, patch):
    """Completing a repeating task moves it to its next date (status back to To Do), like Reminders does; one without
    a date gets the next date after today as its due date.

    Returns the data for a completed copy of the finished occurrence, or None if nothing repeats.
    """
    if table["kind"] not in ("tasks", "mixed") or "rrule" not in keys or "status" not in keys:
        return None
    if table["kind"] == "mixed" and data.get(keys["component"]) != "Task":
        return None
    st = keys["status"]
    if st not in patch or data.get(st) != "Completed" or old.get(st) == "Completed" or not data.get(keys["rrule"]):
        return None
    history = {**data, keys["rrule"]: None}
    due_id = keys.get("due") or keys.get("dtend")
    if not _move_on(data, keys["rrule"], [keys.get("dtstart"), due_id], undated_id=due_id):
        return None  # the series is over: this occurrence just completes
    data[st] = "To Do"
    for k in ("started", "completed"):
        if k in keys:
            data[keys[k]] = None
    return history


def repeat_settings(fields) -> list[tuple[str, str, list[str]]]:
    """Ordinary tables: each Repeat field with the checkbox that completes it and the dates it moves — (repeat, checkbox,
    dates). Chosen in the Repeat field's settings; by default the table's only checkbox and all its date fields."""
    boxes = [f for f in fields if f["type"] == "checkbox" and not f["system_key"]]
    dates = [str(f["id"]) for f in fields if f["type"] == "date"]
    out = []
    for f in fields:
        if f["type"] != "recurrence" or f["system_key"]:
            continue
        o = f["options"] or {}
        box = o.get("doneField") or (str(boxes[0]["id"]) if len(boxes) == 1 else None)
        if box and o.get("doneField") is not False:
            out.append((str(f["id"]), str(box), [d for d in (o.get("dateFields") or dates) if d in dates]))
    return out


def advance_repeating_record(table, fields, old, data, patch):
    """Ordinary tables: ticking a repeating record's checkbox keeps a ticked copy and moves the record to its next date,
    unticked. Returns the copy's data, or None."""
    if table["kind"] != "generic":
        return None
    for rep, box, dates in repeat_settings(fields):
        if box in patch and data.get(box) and not old.get(box) and data.get(rep):
            history = {**data, rep: None}
            if _move_on(data, rep, dates, undated_id=dates[0] if dates else None):
                data[box] = False
                return history
    return None
