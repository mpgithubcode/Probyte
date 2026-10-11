"""Calendar subscription feeds, table icons, and keying API fields by id (Teable's fieldKeyType)."""
import json

import requests

from conftest import API, BASE


def test_calendar_feed(user, other_user):
    t = user.table("mixed", "Agenda")
    F = user.fields(t["id"])
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Dentist", F["component"]: "Event",
        F["dtstart"]: "2026-10-20T09:00:00Z", F["dtend"]: "2026-10-20T10:00:00Z", F["location"]: "Main St"}})
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Holiday", F["component"]: "Event",
        F["dtstart"]: "2026-12-24", F["dtend"]: "2026-12-26"}})
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Gym", F["component"]: "Event",
        F["dtstart"]: "2026-10-05T07:00:00Z", F["dtend"]: "2026-10-05T08:00:00Z", F["rrule"]: "FREQ=WEEKLY\nEXDATE:2026-10-12T07:00:00Z"}})
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Someday", F["component"]: "Task"}})   # no date
    f = user.j("POST", "/feeds", json={"table_id": t["id"]})
    assert f["url"].startswith("http") and f["webcal"].startswith("webcal://")
    path = f["url"].split("/ics/")[1]
    body = requests.get(f"{BASE}/ics/{path}").text                     # no login needed: the link is the key
    assert "BEGIN:VCALENDAR" in body and "X-WR-CALNAME:Agenda" in body
    assert "SUMMARY:Dentist" in body and "DTSTART:20261020T090000Z" in body and "LOCATION:Main St" in body
    assert "DTSTART;VALUE=DATE:20261224" in body and "DTEND;VALUE=DATE:20261227" in body   # all-day: end is exclusive
    assert "RRULE:FREQ=WEEKLY" in body and "EXDATE:20261012T070000Z" in body
    assert "Someday" not in body
    # a feed that follows a view (its filters)
    v = user.j("POST", f"/tables/{t['id']}/views", json={"name": "Dentist only", "type": "calendar", "config": {
        "filter": {"conjunction": "and", "items": [{"field": F["summary"], "op": "contains", "value": "dent"}]}}})
    fv = user.j("POST", "/feeds", json={"table_id": t["id"], "view_id": v["id"]})
    body = requests.get(fv["url"].replace(fv["url"].split("/ics/")[0], BASE)).text
    assert "Dentist" in body and "Holiday" not in body
    assert other_user.j("GET", "/feeds", params={"table_id": t["id"]})["feeds"] == []
    assert len(user.j("GET", "/feeds", params={"table_id": t["id"]})["feeds"]) == 2
    new = user.j("POST", f"/feeds/{f['id']}/regenerate")
    assert requests.get(f"{BASE}/ics/{path}").status_code == 404 and requests.get(f"{BASE}/ics/{new['url'].split('/ics/')[1]}").status_code == 200
    user.j("DELETE", f"/feeds/{f['id']}")
    assert requests.get(f"{BASE}/ics/{new['url'].split('/ics/')[1]}").status_code == 404


def test_table_icon(user):
    t = user.table(name="Iconic")
    user.j("PATCH", f"/tables/{t['id']}", json={"icon": "🎯"})
    assert next(x for x in user.workspace()["tables"] if x["id"] == t["id"])["icon"] == "🎯"
    assert user.j("GET", f"/tables/{t['id']}")["table"]["icon"] == "🎯"
    user.j("PATCH", f"/tables/{t['id']}", json={"icon": ""})
    assert next(x for x in user.workspace()["tables"] if x["id"] == t["id"])["icon"] is None


def test_field_key_type(user):
    key = user.j("POST", "/keys", json={"name": "k", "scope": "write"})["key"]
    H = {"Authorization": "Bearer " + key}
    t = user.table(name="Keyed")
    F = user.fields(t["id"])
    r = requests.post(f"{BASE}/api/v1/tables/{t['id']}/records", headers=H, params={"fieldKeyType": "id"},
                      json={"fields": {F["Name"]: "by id", "Notes": "by name in, ids out"}}).json()
    assert r["fields"][F["Name"]] == "by id" and r["fields"][F["Notes"]] == "by name in, ids out"
    listed = requests.get(f"{BASE}/api/v1/tables/{t['id']}/records", headers=H, params={"fieldKeyType": "id"}).json()
    assert F["Name"] in listed["records"][0]["fields"]
    assert "Name" in requests.get(f"{BASE}/api/v1/tables/{t['id']}/records", headers=H).json()["records"][0]["fields"]
    mcp = requests.post(BASE + "/mcp", headers=H, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "list_records", "arguments": {"table": t["id"], "field_key": "id"}}}).json()
    assert F["Name"] in json.loads(mcp["result"]["content"][0]["text"])["records"][0]["fields"]
