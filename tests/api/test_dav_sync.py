"""Calendars and contacts on Probyte's own CalDAV/CardDAV server, as a phone sees them: tasks, events, repeats,
statuses, contacts. (user.sync() is a no-op for these tables: there is nothing to sync.)"""
import re
import time

import requests

from conftest import API, BASE


def test_push_pull_and_phone_edits(user):
    t = user.table("mixed", "Personal")
    F = user.fields(t["id"])
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Pick up parcel", F["component"]: "Task",
                                                                     F["dtend"]: "2026-10-20T15:00:00Z", F["location"]: "Post office"}})
    user.sync(t["id"])
    href, body = user.server_item(t["calendar_url"], "Pick up parcel")
    assert "LOCATION:Post office" in body and "DUE:20261020T150000Z" in body
    # a phone completes it and (like iOS) writes the due time into COMPLETED
    phone = body.replace("STATUS:NEEDS-ACTION", "STATUS:COMPLETED\r\nCOMPLETED:20261020T150000Z")
    phone = re.sub(r"LAST-MODIFIED:\S+", "LAST-MODIFIED:20261010T181500Z", phone)
    user.put_item(href, phone)
    user.sync(t["id"])
    d = user.record(t["id"], r["id"])["data"]
    assert d[F["status"]] == "Completed" and d[F["done"]] is True
    assert d[F["completed"]] == "2026-10-10T18:15:00Z"   # when it was done, not the due time


def test_completed_checkbox_drives_status(user):
    t = user.table("tasks", "Todo")
    F = user.fields(t["id"])
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Water plants"}})
    d = user.j("PATCH", f"/records/{r['id']}", json={"data": {F["done"]: True}})["data"]
    assert d[F["status"]] == "Completed" and d[F["completed"]]
    user.sync(t["id"])
    assert "STATUS:COMPLETED" in user.server_item(t["calendar_url"], "Water plants")[1]
    d = user.j("PATCH", f"/records/{r['id']}", json={"data": {F["done"]: False}})["data"]
    assert d[F["status"]] == "To Do" and not d[F["completed"]]
    d = user.j("PATCH", f"/records/{r['id']}", json={"data": {F["status"]: "Completed"}})["data"]
    assert d[F["done"]] is True


def test_repeating_task_and_single_occurrence_changes(user):
    t = user.table("mixed", "Repeats")
    F = user.fields(t["id"])
    task = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Bins", F["component"]: "Task",
                                                                        F["dtend"]: "2026-10-12", F["rrule"]: "FREQ=WEEKLY;COUNT=3"}})
    d = user.j("PATCH", f"/records/{task['id']}", json={"data": {F["done"]: True}})["data"]
    assert d[F["dtend"]] == "2026-10-19" and d[F["status"]] == "To Do" and "COUNT=2" in d[F["rrule"]]
    assert any(r["data"].get(F["summary"]) == "Bins" and r["data"].get(F["status"]) == "Completed" for r in user.records(t["id"]))
    # an event series with one moved occurrence
    value = ('FREQ=WEEKLY;BYDAY=MO\nOVERRIDE:{"rid":"2026-10-12T13:00:00Z","set":{"dtstart":"2026-10-13T17:00:00Z",'
             '"dtend":"2026-10-13T18:00:00Z","summary":"Gym (moved)"}}')
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Gym", F["component"]: "Event", F["dtstart"]: "2026-10-05T13:00:00Z",
                                                                F["dtend"]: "2026-10-05T14:00:00Z", F["rrule"]: value}})
    user.sync(t["id"])
    body = user.server_item(t["calendar_url"], "SUMMARY:Gym")[1]
    assert "RRULE:FREQ=WEEKLY;BYDAY=MO" in body and "RECURRENCE-ID:20261012T130000Z" in body and "Gym (moved)" in body


def test_event_outcome_statuses(user):
    t = user.table("events", "Events")
    F = user.fields(t["id"])
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Dentist", F["dtstart"]: "2026-10-08T14:00:00Z",
                                                                     F["dtend"]: "2026-10-08T15:00:00Z", F["status"]: "Attended"}})
    user.sync(t["id"])
    href, body = user.server_item(t["calendar_url"], "Dentist")
    assert "STATUS:CONFIRMED" in body and "X-PROBYTE-STATUS:ATTENDED" in body
    user.put_item(href, body.replace("X-PROBYTE-STATUS:ATTENDED", "X-PROBYTE-STATUS:MISSED"))
    user.sync(t["id"])
    d = user.record(t["id"], r["id"])["data"]
    assert d[F["status"]] == "Missed" and d[F["done"]] is True   # past events are completed


def test_synced_fields_locked_and_table_delete_removes_calendar(user):
    t = user.table("tasks", "Locked")
    F = user.fields(t["id"])
    assert user.patch(API + f"/fields/{F['status']}", json={"name": "State"}).status_code == 400
    assert user.delete(API + f"/fields/{F['due']}").status_code == 400
    res = user.j("DELETE", f"/tables/{t['id']}", params={"forever": "true"})   # (normally it goes to the trash first)
    assert res["server"] == "deleted the calendar"
    import requests
    assert requests.request("PROPFIND", t["calendar_url"], auth=user.dav_auth(), headers={"Depth": "0"}).status_code == 404


def test_contacts_round_trip_keeps_unknown_properties(user):
    t = user.table("contacts", "People")
    F = user.fields(t["id"])
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["fn"]: "Ada Lovelace", F["email"]: "ada@example.org"}})
    user.sync(t["id"])
    assert "EMAIL;TYPE=INTERNET:ada@example.org" in user.server_item(t["calendar_url"], "Ada Lovelace")[1]
    card = ("BEGIN:VCARD\r\nVERSION:3.0\r\nN:Turing;Alan;;;\r\nFN:Alan Turing\r\nTEL;type=CELL:+44 1\r\nTEL;type=HOME:+44 2\r\n"
            "PHOTO;ENCODING=b;TYPE=JPEG:AAAA\r\nUID:PHONE-1\r\nEND:VCARD\r\n")
    import requests
    path = t["calendar_url"] + "PHONE-1.vcf"
    assert requests.put(path, data=card, auth=user.dav_auth(), headers={"Content-Type": "text/vcard"}).status_code == 201
    user.sync(t["id"])
    alan = next(r for r in user.records(t["id"]) if r["data"].get(F["fn"]) == "Alan Turing")
    user.j("PATCH", f"/records/{alan['id']}", json={"data": {F["org"]: "Bletchley Park"}})
    user.sync(t["id"])
    body = requests.get(path, auth=user.dav_auth()).text
    assert "ORG:Bletchley Park" in body and "PHOTO" in body and "+44 2" in body


def test_edits_reach_the_server_without_sync_now(user):
    """What's written in Probyte (the app, the API) is what a phone gets next: there's no push to wait for."""
    t = user.table("tasks", "Auto push")
    F = user.fields(t["id"])
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Pushed from the app"}})
    key = user.j("POST", "/keys", json={"name": "push", "scope": "write"})["key"]
    requests.post(f"{BASE}/api/v1/tables/{t['id']}/records", headers={"Authorization": f"Bearer {key}"},
                  json={"fields": {"Title": "Pushed via API"}}).raise_for_status()
    deadline = time.time() + 2
    while time.time() < deadline:
        bodies = " ".join(b for _, b in user.server_items(t["calendar_url"]))
        if "Pushed from the app" in bodies and "Pushed via API" in bodies:
            return
        time.sleep(1)
    raise AssertionError("edits were not on the DAV server")
