"""Records: every write (create, update, delete to the trash, restore) with its checks, two-way links kept in step,
its history entry and its change-feed event."""
from app.calc.compute import COMPUTED_TYPES, Engine
from app.core import events
from app.core.db import Jsonb
from app.data import audit, stamps
from app.data.behaviors import _is_task, advance_recurring, advance_repeating_record, apply_done, apply_event_defaults, apply_task_auto, sync_done
from app.data.rules import apply_defaults, check_rules
from app.data.schema import bad, DAV_KINDS, ensure_choices, get_fields, get_table, is_remote_table, is_served, not_found, SELECT_TYPES
from app.formats import fieldformats, recur
from app.formats.ical import LOCAL_KEYS, new_uid


def clean_record(r):
    out = {k: r[k] for k in ("id", "table_id", "data", "sync_status", "created_at", "updated_at")}
    if r.get("source_id"):  # a linked table's record: its id in the source
        out["source_id"] = r["source_id"]
    return out


def normalize_data(conn, table, fields, data: dict, user_id=None, record_id=None) -> dict:
    """Drop unknown/computed keys, coerce link/user/attachment values, register new select choices."""
    by_id = {str(f["id"]): f for f in fields}
    out = {}
    for k, v in data.items():
        f = by_id.get(str(k))
        if not f or f["type"] in COMPUTED_TYPES:
            continue
        t = f["type"]
        if t == "link":
            ids = [str(x) for x in (v if isinstance(v, list) else [v] if v else [])]
            if ids:
                ftid = f["options"].get("foreignTableId")
                if is_remote_table(conn, ftid):  # records of a linked table live at its source
                    ok = set(Engine(conn).titles(ftid))
                else:
                    ok = {str(r["id"]) for r in conn.execute(
                        "SELECT id FROM records WHERE table_id = %s AND id = ANY(%s::uuid[]) AND NOT deleted",
                        (ftid, ids)).fetchall()}
                ids = list(dict.fromkeys(x for x in ids if x in ok))
            v = ids[:1] if f["options"].get("relationship") == "one" else ids
        elif t in ("number", "rating") and v is not None and not (isinstance(v, (int, float)) and not isinstance(v, bool)):
            try:
                v = float(str(v).replace(",", "").strip()) if str(v).strip() else None
                v = int(v) if v is not None and v.is_integer() else v
            except ValueError:
                bad(f"{f['name']} needs a number, not “{v}”")
        elif t == "checkbox":
            v = bool(v) and str(v).lower() not in ("false", "0", "no", "off")
        elif t in fieldformats.FORMAT_TYPES:
            try:
                v = fieldformats.clean(t, v)
            except ValueError as e:
                bad(f"{f['name']}: “{v}” {e}")
        elif t == "recurrence":
            try:
                v = recur.normalize(v)
            except ValueError as e:
                bad(str(e))
        elif t == "user":
            ids = [str(x) for x in (v if isinstance(v, list) else [v] if v else [])]
            if ids:
                ok = {str(r["id"]) for r in conn.execute("SELECT id FROM users WHERE id = ANY(%s::uuid[])", (ids,)).fetchall()}
                ids = list(dict.fromkeys(x for x in ids if x in ok))
            v = ids if f["options"].get("multiple") else ids[:1]
        elif t == "attachment":
            items = [a for a in (v or []) if isinstance(a, dict) and a.get("id")]
            if items and user_id:
                rows = {str(r["id"]): r for r in conn.execute(
                    "SELECT id, name, mime, size FROM attachments WHERE user_id = %s AND id = ANY(%s::uuid[])",
                    (user_id, [str(a["id"]) for a in items])).fetchall()}
                v = [{"id": i, "name": rows[i]["name"], "mime": rows[i]["mime"], "size": rows[i]["size"]}
                     for i in dict.fromkeys(str(a["id"]) for a in items) if i in rows]
            elif items:
                continue  # attachments can only be set by a logged-in user
        if t in SELECT_TYPES and f["options"].get("allowNew") is False:
            known = {c["name"] for c in f["options"].get("choices", [])}
            for x in (v if isinstance(v, list) else [v]):
                if x not in (None, "") and str(x) not in known:
                    bad(f"“{x}” isn't one of the options of {f['name']}")
        ensure_choices(conn, f, v)
        out[str(k)] = v
    return out


def _sync_links(conn, fields, record_id, table_id, old: dict, new: dict):
    """Keep two-way link fields consistent: mirror added/removed links into the other table."""
    for f in fields:
        sym = f["options"].get("symmetricFieldId") if f["type"] == "link" else None
        fid = str(f["id"])
        if not sym or fid not in new:
            continue
        before, after = set(old.get(fid) or []), set(new.get(fid) or [])
        ftid = f["options"]["foreignTableId"]
        sym_field = conn.execute("SELECT * FROM fields WHERE id = %s", (sym,)).fetchone()
        one = sym_field and sym_field["options"].get("relationship") == "one"
        for rid in after - before:
            row = conn.execute("SELECT data FROM records WHERE id = %s AND table_id = %s", (rid, ftid)).fetchone()
            if row:
                cur = [x for x in (row["data"].get(sym) or []) if x != str(record_id)]
                vals = [str(record_id)] if one else cur + [str(record_id)]
                conn.execute("UPDATE records SET data = jsonb_set(data, %s, %s), updated_at = now() WHERE id = %s",
                             ([sym], Jsonb(vals), rid))
                if one:  # the other record can only point at one record: drop its previous link here too
                    for prev in cur:
                        _remove_link(conn, prev, fid, rid)
        for rid in before - after:
            _remove_link(conn, rid, sym, record_id, ftid)


def _remove_link(conn, rid, field_id, target, table_id=None):
    row = conn.execute("SELECT data FROM records WHERE id = %s" + (" AND table_id = %s" if table_id else ""),
                       (rid, table_id) if table_id else (rid,)).fetchone()
    if row and str(target) in (row["data"].get(str(field_id)) or []):
        vals = [x for x in row["data"][str(field_id)] if x != str(target)]
        conn.execute("UPDATE records SET data = jsonb_set(data, %s, %s), updated_at = now() WHERE id = %s",
                     ([str(field_id)], Jsonb(vals), rid))


def _changed(conn, table, event, record_id, source="app", old=None):
    events.record_changed(conn, table, event, record_id, source, old)


def insert_record(conn, table_id, data, *, user_id=None, caldav_uid=None, caldav_href=None, caldav_raw=None, sync_status="synced",
                  record_id=None):
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(table_id),))  # serialise auto numbers
    return conn.execute(
        "INSERT INTO records (id, table_id, data, caldav_uid, caldav_href, caldav_raw, sync_status, seq, created_by, updated_by) "
        "VALUES (COALESCE(%s, gen_random_uuid()),%s,%s,%s,%s,%s,%s,(SELECT COALESCE(MAX(seq), 0) + 1 FROM records WHERE table_id = %s),%s,%s) RETURNING *",
        (record_id, table_id, Jsonb(data), caldav_uid, caldav_href, caldav_raw, sync_status, table_id, user_id, user_id),
    ).fetchone()


def create_record(conn, table_id, data: dict, user_id=None, *, table=None, fields=None, source="app"):
    """table / fields: already loaded (and checked) by the caller, e.g. once for a whole import."""
    table = table or get_table(conn, table_id, user_id)
    if table["kind"] == "remote":
        bad("Records of a linked table are written through its link")
    fields = fields if fields is not None else get_fields(conn, table_id)
    data = normalize_data(conn, table, fields, apply_defaults(fields, data or {}, user_id), user_id)
    check_rules(conn, table_id, fields, data, {str(f["id"]) for f in fields})
    if table["kind"] in ("tasks", "mixed"):
        keys = {f["system_key"]: str(f["id"]) for f in fields if f["system_key"]}
        if table["kind"] == "mixed" and not data.get(keys["component"]):
            # Something placed on a day (calendar view) is an event; anything else starts as a task.
            data[keys["component"]] = "Event" if data.get(keys["dtstart"]) else "Task"
        is_task = table["kind"] == "tasks" or (table["kind"] == "mixed" and data[keys["component"]] == "Task")
        if is_task and not data.get(keys["status"]):
            data[keys["status"]] = "To Do"
        if keys.get("done") and data.get(keys["done"]) and _is_task(table, keys, data) and data.get(keys["status"]) != "Completed":
            data[keys["status"]] = "Completed"
        apply_task_auto(table, fields, {keys["status"]: "To Do"}, data, data)
        sync_done(table, keys, data)
    apply_event_defaults(table, fields, data, data)
    if table["kind"] in DAV_KINDS:
        r = insert_record(conn, table_id, data, user_id=user_id, caldav_uid=new_uid(), sync_status="dirty")
    else:
        r = insert_record(conn, table_id, data, user_id=user_id)
    _sync_links(conn, fields, r["id"], table_id, {}, data)
    stamps.apply(conn, table_id, r["id"], fields)   # Started date, Completed date…
    audit.note(conn, table_id, r["id"], "created")
    _changed(conn, table, "record.created", r["id"], source)
    return r, table


def update_record(conn, record_id, patch: dict, user_id=None, source="app"):
    # FOR UPDATE: simultaneous edits of one record wait for each other instead of overwriting each other's fields
    r = conn.execute("SELECT * FROM records WHERE id = %s AND NOT deleted FOR UPDATE", (record_id,)).fetchone() or not_found("Record not found")
    table = get_table(conn, r["table_id"], user_id)
    fields = get_fields(conn, r["table_id"])
    patch = normalize_data(conn, table, fields, patch or {}, user_id, record_id)
    old = dict(r["data"])
    check_rules(conn, r["table_id"], fields, patch, {k for k, v in patch.items() if v != old.get(k)}, record_id)
    data = {**old, **patch}
    keys = {f["system_key"]: str(f["id"]) for f in fields if f["system_key"]}
    patch = apply_done(table, keys, old, data, patch)
    history = advance_recurring(table, keys, old, data, patch) or advance_repeating_record(table, fields, old, data, patch)
    apply_task_auto(table, fields, old, data, patch)
    apply_event_defaults(table, fields, data, patch)
    sync_done(table, keys, data)
    # Only edits to CalDAV-managed fields need pushing; local extra fields don't.
    caldav_ids = {str(f["id"]) for f in fields if f["system_key"] and f["system_key"] not in LOCAL_KEYS}
    changed = {k for k in data if data.get(k) != old.get(k)}
    status = r["sync_status"]
    if table["kind"] in DAV_KINDS and changed & caldav_ids:
        status = "dirty"
    r = conn.execute(
        "UPDATE records SET data = %s, sync_status = %s, updated_at = now(), updated_by = COALESCE(%s, updated_by) "
        "WHERE id = %s RETURNING *",
        (Jsonb(data), status, user_id, record_id),
    ).fetchone()
    _sync_links(conn, fields, record_id, r["table_id"], old, patch)
    data.update(stamps.apply(conn, r["table_id"], record_id, fields))   # Started date, Completed date…
    audit.note(conn, r["table_id"], record_id, "updated", old, data, fields)
    if history is not None:
        # A repeating task moved on to its next date; keep the finished occurrence as a completed task.
        apply_task_auto(table, fields, old, history, patch)
        sync_done(table, keys, history)
        h = (insert_record(conn, r["table_id"], history, user_id=user_id, caldav_uid=new_uid(), sync_status="dirty")
             if table["kind"] in DAV_KINDS else insert_record(conn, r["table_id"], history, user_id=user_id))
        _sync_links(conn, fields, h["id"], r["table_id"], {}, history)
        audit.note(conn, r["table_id"], h["id"], "created")
        _changed(conn, table, "record.created", h["id"], source)
    _changed(conn, table, "record.updated", record_id, source, old)
    return r, table


def complete_occurrence(conn, record_id, occurrence: str, user_id=None, source="app"):
    """Mark one date of a repeating task (or record) done ahead of the others: its current date completes it as usual
    (it moves on to the next); a later date becomes a completed copy on that date and is skipped in the series."""
    from app.data.behaviors import repeat_settings
    r = conn.execute("SELECT * FROM records WHERE id = %s AND NOT deleted", (record_id,)).fetchone() or not_found("Record not found")
    table = get_table(conn, r["table_id"], user_id)
    fields = get_fields(conn, r["table_id"])
    keys = {f["system_key"]: str(f["id"]) for f in fields if f["system_key"]}
    data = dict(r["data"])
    if table["kind"] in ("tasks", "mixed") and keys.get("rrule") and _is_task(table, keys, data):
        rule_id, done = keys["rrule"], {keys["status"]: "Completed"}
        dates = [keys.get("dtstart"), keys.get("due") or keys.get("dtend")]
    else:
        rep = next((x for x in repeat_settings(fields) if data.get(x[0])), None)
        if not rep:
            bad("This record doesn't repeat, or has no checkbox to complete it")
        rule_id, done, dates = rep[0], {rep[1]: True}, rep[2]
    rule = data.get(rule_id)
    anchor = next((f for f in dates if f and data.get(f)), None)
    if not rule or not anchor:
        bad("This record doesn't repeat from a date")
    target = recur.parse_value_dt(occurrence)
    current = recur.parse_value_dt(data[anchor])
    if target is None:
        bad("Which date?")
    if recur.rid_key(occurrence) == recur.rid_key(data[anchor]) or (target and current and recur.delta_between(data[anchor], target).total_seconds() <= 0):
        return update_record(conn, record_id, done, user_id, source)   # the current one: moves on as usual
    delta = recur.delta_between(data[anchor], target)
    copy = {**data, rule_id: None, **done}
    for f in dates:
        if f and data.get(f):
            copy[f] = recur.shift(data[f], delta)
    if table["kind"] in ("tasks", "mixed"):
        apply_task_auto(table, fields, {keys["status"]: data.get(keys["status"])}, copy, done)
        sync_done(table, keys, copy)
    h = (insert_record(conn, r["table_id"], copy, user_id=user_id, caldav_uid=new_uid(), sync_status="dirty")
         if table["kind"] in DAV_KINDS else insert_record(conn, r["table_id"], copy, user_id=user_id))
    _sync_links(conn, fields, h["id"], r["table_id"], {}, copy)
    audit.note(conn, r["table_id"], h["id"], "created")
    _changed(conn, table, "record.created", h["id"], source)
    rule_v, ex = recur.parse(rule)
    update_record(conn, record_id, {rule_id: recur.compose(rule_v, ex + [copy[anchor]], recur.overrides(rule))}, user_id, source)
    return h, table


def delete_record(conn, record_id, user_id=None, source="app"):
    r = conn.execute("SELECT * FROM records WHERE id = %s AND NOT deleted", (record_id,)).fetchone() or not_found("Record not found")
    table = get_table(conn, r["table_id"], user_id)
    # a copy goes to the trash (30 days), so the record can be restored
    conn.execute(
        "INSERT INTO record_trash (id, table_id, data, seq, caldav_uid, caldav_raw, created_at, created_by, deleted_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, deleted_at = now()",
        (record_id, r["table_id"], Jsonb(r["data"]), r["seq"], r["caldav_uid"], r["caldav_raw"], r["created_at"], r["created_by"], user_id))
    # Remove this record from link fields pointing at it.
    for f in conn.execute("SELECT id FROM fields WHERE type = 'link' AND options->>'foreignTableId' = %s",
                          (str(r["table_id"]),)).fetchall():
        fid = str(f["id"])
        for row in conn.execute("SELECT id FROM records WHERE data -> %s @> %s", (fid, Jsonb([str(record_id)]))).fetchall():
            _remove_link(conn, row["id"], fid, record_id)
    if r["caldav_href"] and not is_served(table):  # mirrors something elsewhere: kept until it's deleted there too
        conn.execute("UPDATE records SET deleted = true, updated_at = now() WHERE id = %s", (record_id,))
    else:
        conn.execute("DELETE FROM records WHERE id = %s", (record_id,))
    audit.note(conn, r["table_id"], record_id, "deleted")
    _changed(conn, table, "record.deleted", record_id, source, dict(r["data"]))
    return table


def restore_record(conn, record_id, user_id=None):
    """Bring a record back from the trash (with its links). A synced one goes back to the calendar / address book."""
    t = conn.execute("SELECT * FROM record_trash WHERE id = %s", (record_id,)).fetchone() or not_found("Not in the trash")
    table = get_table(conn, t["table_id"], user_id)
    fields = get_fields(conn, t["table_id"])
    live = conn.execute("SELECT * FROM records WHERE id = %s", (record_id,)).fetchone()
    if live:  # deleted here but not yet on the other server: just un-delete it
        conn.execute("UPDATE records SET deleted = false, sync_status = %s, updated_at = now() WHERE id = %s",
                     ("dirty" if table["kind"] in DAV_KINDS else "synced", record_id))
    else:
        known = {str(f["id"]) for f in fields}
        data = {k: v for k, v in t["data"].items() if k in known}
        # links only to records that still exist
        for f in fields:
            fid = str(f["id"])
            if f["type"] == "link" and isinstance(data.get(fid), list):
                ok = {str(x["id"]) for x in conn.execute("SELECT id FROM records WHERE id = ANY(%s::uuid[]) AND NOT deleted",
                                                         ([str(x) for x in data[fid]],)).fetchall()}
                data[fid] = [x for x in data[fid] if str(x) in ok]
        synced = table["kind"] in DAV_KINDS
        conn.execute(
            "INSERT INTO records (id, table_id, data, seq, caldav_uid, sync_status, created_at, created_by, updated_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (record_id, t["table_id"], Jsonb(data), t["seq"], t["caldav_uid"] if synced else None, "dirty" if synced else "synced",
             t["created_at"], t["created_by"], user_id))
        _sync_links(conn, fields, record_id, t["table_id"], {}, data)
    conn.execute("DELETE FROM record_trash WHERE id = %s", (record_id,))
    conn.execute("DELETE FROM record_tombstones WHERE record_id = %s", (record_id,))  # it's back: no longer "deleted"
    audit.note(conn, t["table_id"], record_id, "restored")
    _changed(conn, table, "record.created", record_id)
    return table


def list_records(conn, table_id, engine=None):
    eng = engine or Engine(conn)
    return [clean_record(r) for r in eng.records(table_id)]


def computed_record(conn, record_id, table_id):
    """One record with its computed fields filled in."""
    r = Engine(conn).record(table_id, record_id)
    return clean_record(r) if r else None


def delete_field_cleanup(conn, f):
    """Remove a field's stored values; for link fields also drop the counterpart field."""
    conn.execute("UPDATE records SET data = data - %s WHERE table_id = %s", (str(f["id"]), f["table_id"]))
    sym = f["options"].get("symmetricFieldId") if f["type"] == "link" else None
    if sym:
        s = conn.execute("SELECT * FROM fields WHERE id = %s", (sym,)).fetchone()
        if s and not s["is_primary"]:
            conn.execute("UPDATE records SET data = data - %s WHERE table_id = %s", (str(s["id"]), s["table_id"]))
            conn.execute("DELETE FROM fields WHERE id = %s", (s["id"],))
