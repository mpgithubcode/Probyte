"""More two-way sync cases: deletions both ways, conflicts, items made on phones, kind changes, shared calendars."""
import uuid

import requests

from conftest import API, RADICALE


def ics(uid, body):
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Phone//EN\r\n{body}\r\nEND:VCALENDAR\r\n".replace("UID_HERE", uid)


def put(user, t, uid, body):
    path = t["calendar_url"].replace(RADICALE, "") + f"{uid}.ics"
    user.put_item(path, ics(uid, body))
    return path


def by_title(user, t, F, title):
    return next((r for r in user.records(t["id"]) if r["data"].get(F["summary"]) == title), None)


def test_deletions_both_ways(user):
    t = user.table("tasks", "Deletes")
    F = user.fields(t["id"])
    a = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Delete me here"}})
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Delete me on the phone"}})
    user.sync(t["id"])
    user.j("DELETE", f"/records/{a['id']}")
    user.sync(t["id"])
    bodies = " ".join(b for _, b in user.server_items(t["calendar_url"]))
    assert "Delete me here" not in bodies and "Delete me on the phone" in bodies
    href, _ = user.server_item(t["calendar_url"], "Delete me on the phone")
    assert requests.delete(RADICALE + href, auth=user.dav_auth()).status_code in (200, 204)
    user.sync(t["id"])
    assert user.records(t["id"]) == []


def test_local_edit_wins_a_conflict_and_server_wins_otherwise(user):
    t = user.table("tasks", "Conflicts")
    F = user.fields(t["id"])
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Original"}})
    user.sync(t["id"])
    href, body = user.server_item(t["calendar_url"], "Original")
    # the phone renames it; nothing changed here: the phone's version arrives
    user.put_item(href, body.replace("SUMMARY:Original", "SUMMARY:Phone name"))
    user.sync(t["id"])
    assert user.record(t["id"], r["id"])["data"][F["summary"]] == "Phone name"
    # both change it before the next sync: the local edit wins and is pushed
    _, body = user.server_item(t["calendar_url"], "Phone name")
    user.put_item(href, body.replace("SUMMARY:Phone name", "SUMMARY:Phone again"))
    user.j("PATCH", f"/records/{r['id']}", json={"data": {F["summary"]: "Edited here"}})
    user.sync(t["id"])
    assert user.record(t["id"], r["id"])["data"][F["summary"]] == "Edited here"
    assert "SUMMARY:Edited here" in user.server_item(t["calendar_url"], "Edited here")[1]


def test_items_made_on_a_phone_are_imported(user):
    t = user.table("mixed", "From phone")
    F = user.fields(t["id"])
    uid = str(uuid.uuid4())
    put(user, t, uid, "BEGIN:VEVENT\r\nUID:UID_HERE\r\nDTSTAMP:20261001T000000Z\r\nSUMMARY:Holiday\r\nDTSTART;VALUE=DATE:20261020\r\n"
                      "DTEND;VALUE=DATE:20261023\r\nLOCATION:Lisbon\r\nBEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT1H\r\n"
                      "DESCRIPTION:Reminder\r\nEND:VALARM\r\nEND:VEVENT")
    put(user, t, str(uuid.uuid4()),
        "BEGIN:VTIMEZONE\r\nTZID:Europe/Berlin\r\nBEGIN:STANDARD\r\nDTSTART:19701025T030000\r\nTZOFFSETFROM:+0200\r\nTZOFFSETTO:+0100\r\n"
        "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU\r\nEND:STANDARD\r\nBEGIN:DAYLIGHT\r\nDTSTART:19700329T020000\r\nTZOFFSETFROM:+0100\r\n"
        "TZOFFSETTO:+0200\r\nRRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU\r\nEND:DAYLIGHT\r\nEND:VTIMEZONE\r\n"
        "BEGIN:VEVENT\r\nUID:UID_HERE\r\nDTSTAMP:20261001T000000Z\r\nSUMMARY:Berlin call\r\nDTSTART;TZID=Europe/Berlin:20261012T100000\r\n"
        "DTEND;TZID=Europe/Berlin:20261012T110000\r\nEND:VEVENT")
    put(user, t, str(uuid.uuid4()), "BEGIN:VTODO\r\nUID:UID_HERE\r\nDTSTAMP:20261001T000000Z\r\nSUMMARY:Phone task\r\nPRIORITY:1\r\n"
                                    "DUE;VALUE=DATE:20261015\r\nSTATUS:IN-PROCESS\r\nCATEGORIES:home,errands\r\nEND:VTODO")
    user.sync(t["id"])
    hol = by_title(user, t, F, "Holiday")["data"]
    assert hol[F["component"]] == "Event" and hol[F["dtstart"]] == "2026-10-20" and hol[F["location"]] == "Lisbon"
    assert hol[F["dtend"]] in ("2026-10-22", "2026-10-23")
    call = by_title(user, t, F, "Berlin call")["data"]
    assert call[F["dtstart"]] == "2026-10-12T08:00:00Z"                 # 10:00 Berlin summer time
    task = by_title(user, t, F, "Phone task")["data"]
    assert task[F["component"]] == "Task" and task[F["status"]] == "In Progress" and task[F["dtend"]] == "2026-10-15"
    assert task[F["priority"]] == "High" and set(task[F["categories"]]) == {"home", "errands"}
    # editing it here keeps what Probyte doesn't manage (the alarm)
    user.j("PATCH", f"/records/{by_title(user, t, F, 'Holiday')['id']}", json={"data": {F["location"]: "Porto"}})
    user.sync(t["id"])
    body = user.server_item(t["calendar_url"], "Holiday")[1]
    assert "LOCATION:Porto" in body and "BEGIN:VALARM" in body and "TRIGGER:-PT1H" in body


def test_changing_type_turns_a_task_into_an_event(user):
    t = user.table("mixed", "Switch")
    F = user.fields(t["id"])
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Flexible", F["component"]: "Task", F["dtend"]: "2026-10-15"}})
    user.sync(t["id"])
    assert "BEGIN:VTODO" in user.server_item(t["calendar_url"], "Flexible")[1]
    user.j("PATCH", f"/records/{r['id']}", json={"data": {F["component"]: "Event", F["dtstart"]: "2026-10-15T09:00:00Z", F["dtend"]: "2026-10-15T10:00:00Z"}})
    user.sync(t["id"])
    items = [b for _, b in user.server_items(t["calendar_url"]) if "Flexible" in b]
    assert len(items) == 1 and "BEGIN:VEVENT" in items[0] and "BEGIN:VTODO" not in items[0]


def test_two_tables_on_one_calendar_and_linking_an_existing_one(user):
    t = user.table("tasks", "First")
    F = user.fields(t["id"])
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Shared task"}})
    user.sync(t["id"])
    second = user.j("POST", "/tables", json={"name": "Second", "kind": "tasks", "base_id": user.base_id,
                                             "account_id": user.builtin["id"], "calendar_url": t["calendar_url"]})
    user.sync(second["id"])
    F2 = user.fields(second["id"])
    assert [r["data"][F2["summary"]] for r in user.records(second["id"])] == ["Shared task"]
    res = user.j("DELETE", f"/tables/{second['id']}")
    assert "kept" in str(res).lower() or res.get("server") != "deleted the calendar"
    assert requests.request("PROPFIND", t["calendar_url"], auth=user.dav_auth(), headers={"Depth": "0"}).status_code == 207
    cals = user.j("GET", f"/accounts/{user.builtin['id']}/calendars")
    assert any(c["url"].rstrip("/") == t["calendar_url"].rstrip("/") for c in cals)


def test_external_account_sync(user):
    """An account added by hand (here: the test Radicale with this user's login) syncs like the built-in one."""
    name, pw = user.dav_auth()
    acct = user.j("POST", "/accounts", json={"name": "Other server", "url": f"{RADICALE}/", "username": name, "password": pw})
    t = user.j("POST", "/tables", json={"name": "External", "kind": "events", "base_id": user.base_id, "account_id": acct["id"],
                                        "new_calendar_name": "External"})
    F = user.fields(t["id"])
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Remote meeting", F["dtstart"]: "2026-10-12T09:00:00Z",
                                                                F["dtend"]: "2026-10-12T10:00:00Z"}})
    user.sync(t["id"])
    assert "SUMMARY:Remote meeting" in user.server_item(t["calendar_url"], "Remote meeting")[1]
    assert user.delete(API + f"/accounts/{acct['id']}").status_code in (200, 400)   # in use: refused, or tables keep working


def test_sync_status_is_reported(user):
    t = user.table("tasks", "Status")
    user.sync(t["id"])
    info = user.j("GET", f"/tables/{t['id']}")["table"]
    assert info["last_synced_at"] and not info.get("last_sync_error")
    # a calendar deleted behind Probyte's back shows as an error instead of breaking the table
    requests.request("DELETE", t["calendar_url"], auth=user.dav_auth())
    user.post(API + f"/tables/{t['id']}/sync")
    info = user.j("GET", f"/tables/{t['id']}")["table"]
    assert info.get("last_sync_error")
