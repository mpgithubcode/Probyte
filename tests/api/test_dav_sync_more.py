"""More calendar cases: deletions both ways, conflicts, items made on phones, kind changes, other servers."""
import uuid

import requests

from conftest import API, BASE


def ics(uid, body):
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Phone//EN\r\n{body}\r\nEND:VCALENDAR\r\n".replace("UID_HERE", uid)


def put(user, t, uid, body):
    path = t["calendar_url"] + f"{uid}.ics"
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
    assert requests.delete(BASE + href, auth=user.dav_auth()).status_code in (200, 204)
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


def test_each_table_is_its_own_calendar_on_the_phone(user):
    first, second = user.table("tasks", "First"), user.table("contacts", "Second")
    listing = requests.request("PROPFIND", f"{BASE}/dav/{user.dav_auth()[0]}/", auth=user.dav_auth(), headers={"Depth": "1"}).text
    assert "<d:displayname>First</d:displayname>" in listing and "<d:displayname>Second</d:displayname>" in listing
    assert "VTODO" in listing and "addressbook" in listing
    assert user.j("GET", f"/accounts/{user.builtin['id']}/calendars") == []   # nothing to link: a new table is a new calendar
    user.j("DELETE", f"/tables/{first['id']}")                                  # in the trash: gone from the phone
    assert requests.request("PROPFIND", first["calendar_url"], auth=user.dav_auth(), headers={"Depth": "0"}).status_code == 404
    assert requests.request("PROPFIND", second["calendar_url"], auth=user.dav_auth(), headers={"Depth": "0"}).status_code == 207


def external(user, kind="events", name="External"):
    """A table synced with another CalDAV server: here Probyte's own, through an account added by hand, so the
    sync talks to a real server (and the "phone" side is a table of its own)."""
    login, pw = user.dav_auth()
    acct = user.j("POST", "/accounts", json={"name": "Other server", "url": f"{BASE}/dav/", "username": login, "password": pw})
    t = user.j("POST", "/tables", json={"name": name, "kind": kind, "base_id": user.base_id, "account_id": acct["id"],
                                        "new_calendar_name": name})
    return acct, t


def test_external_account_sync(user):
    acct, t = external(user)
    F = user.fields(t["id"])
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Remote meeting", F["dtstart"]: "2026-10-12T09:00:00Z",
                                                                F["dtend"]: "2026-10-12T10:00:00Z"}})
    user.sync(t["id"])
    assert "SUMMARY:Remote meeting" in user.server_item(t["calendar_url"], "Remote meeting")[1]
    # changed on the other server: pulled in by the next sync, which opening the table starts when it's a while ago
    href, body = user.server_item(t["calendar_url"], "Remote meeting")
    user.put_item(href, body.replace("SUMMARY:Remote meeting", "SUMMARY:Moved meeting"))
    user.sync(t["id"])
    assert [r["data"][F["summary"]] for r in user.records(t["id"])] == ["Moved meeting"]
    # only what changed comes back (sync-collection): an item added there, and one deleted there
    put(user, t, str(uuid.uuid4()), "BEGIN:VEVENT\r\nUID:UID_HERE\r\nDTSTAMP:20261001T000000Z\r\nSUMMARY:Added there\r\n"
                                    "DTSTART:20261013T090000Z\r\nDTEND:20261013T100000Z\r\nEND:VEVENT")
    assert requests.delete(BASE + href, auth=user.dav_auth()).status_code == 204
    stats = user.sync(t["id"])
    assert stats["fetched"] == 1 and stats["removed"] == 1, stats
    assert [r["data"][F["summary"]] for r in user.records(t["id"])] == ["Added there"]
    assert user.sync(t["id"])["skipped"]                                             # nothing new: one request
    assert user.delete(API + f"/accounts/{acct['id']}").status_code in (200, 400)   # in use: refused, or tables keep working


def test_sync_status_is_reported(user):
    _, t = external(user, "tasks", "Status")
    user.sync(t["id"])
    info = user.j("GET", f"/tables/{t['id']}")["table"]
    assert info["last_synced_at"] and not info.get("last_sync_error")
    # a calendar deleted behind Probyte's back shows as an error instead of breaking the table
    requests.request("DELETE", t["calendar_url"], auth=user.dav_auth())
    user.post(API + f"/tables/{t['id']}/sync")
    info = user.j("GET", f"/tables/{t['id']}")["table"]
    assert info.get("last_sync_error")


def test_reminders_sync_both_ways(user):
    t = user.table("mixed", "Reminders")
    F = user.fields(t["id"])
    ev = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Dentist", F["component"]: "Event",
        F["dtstart"]: "2026-10-20T09:00:00Z", F["dtend"]: "2026-10-20T10:00:00Z", F["alarm"]: "15 minutes before"}})
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Pay rent", F["component"]: "Task",
        F["dtend"]: "2026-10-21T12:00:00Z", F["alarm"]: "1 hour before"}})
    user.sync(t["id"])
    body = user.server_item(t["calendar_url"], "Dentist")[1]
    assert "BEGIN:VALARM" in body and "TRIGGER:-PT15M" in body and "ACTION:DISPLAY" in body
    task = user.server_item(t["calendar_url"], "Pay rent")[1]
    assert "TRIGGER;VALUE=DATE-TIME:20261021T110000Z" in task                  # an hour before it's due
    # a phone changes the event's alert to 10 minutes and adds a second one: it's read, and kept on our next edit
    href, body = user.server_item(t["calendar_url"], "Dentist")
    phone = body.replace("TRIGGER:-PT15M", "TRIGGER:-PT10M").replace("END:VEVENT",
        "BEGIN:VALARM\r\nACTION:AUDIO\r\nTRIGGER:-P1D\r\nEND:VALARM\r\nEND:VEVENT")
    user.put_item(href, phone)
    user.sync(t["id"])
    assert user.record(t["id"], ev["id"])["data"][F["alarm"]] == "10 minutes before"
    user.j("PATCH", f"/records/{ev['id']}", json={"data": {F["location"]: "Main street"}})
    user.sync(t["id"])
    body = user.server_item(t["calendar_url"], "Dentist")[1]
    assert "TRIGGER:-PT10M" in body and "TRIGGER:-P1D" in body                # both alerts untouched
    # clearing the Reminder removes the alerts
    user.j("PATCH", f"/records/{ev['id']}", json={"data": {F["alarm"]: None}})
    user.sync(t["id"])
    assert "VALARM" not in user.server_item(t["calendar_url"], "Dentist")[1]
