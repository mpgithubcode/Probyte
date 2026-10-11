"""Record history: who changed what, from the app, the API, a public link and phone sync."""
import requests

from conftest import API, BASE


def test_history_names_who_changed_what(user, other_user):
    t = user.table(name="Tracked")
    F = user.fields(t["id"])
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Lamp", F["Status"]: "Todo"}})
    user.j("PATCH", f"/records/{r['id']}", json={"data": {F["Status"]: "Done", F["Notes"]: "bright"}})
    user.j("PATCH", f"/records/{r['id']}", json={"data": {F["Status"]: "Done"}})          # no change: no entry
    key = user.j("POST", "/keys", json={"name": "Zapier", "scope": "write"})["key"]
    requests.patch(f"{BASE}/api/v1/tables/{t['id']}/records/{r['id']}", headers={"Authorization": f"Bearer {key}"},
                   json={"fields": {"Notes": "dim"}}).raise_for_status()
    view = user.j("GET", f"/tables/{t['id']}")["views"][0]
    s = user.j("POST", "/shares", json={"name": "Team", "base_id": user.base_id, "permissions": {"edit": True},
                                       "items": [{"kind": "table", "id": t["id"]}]})
    requests.patch(API + f"/public/{s['token']}/views/{view['id']}/records/{r['id']}", json={"data": {F["Name"]: "Desk lamp"}}).raise_for_status()
    user.j("DELETE", f"/records/{r['id']}")
    user.j("POST", "/trash/records/restore", json={"ids": [r["id"]]})
    h = user.j("GET", f"/records/{r['id']}/history")["entries"]
    seen = [(e["actor"], e["action"], {c["field"]: [c["old"], c["new"]] for c in e["changes"]}) for e in reversed(h)]
    assert seen[0] == (user.username, "created", {})
    assert seen[1] == (user.username, "updated", {"Notes": [None, "bright"], "Status": ["Todo", "Done"]})
    assert seen[2] == (f"{user.username} via API key “Zapier”", "updated", {"Notes": ["bright", "dim"]})
    assert seen[3] == ("Public link “Team”", "updated", {"Name": ["Lamp", "Desk lamp"]})
    assert [x[1] for x in seen[4:]] == ["deleted", "restored"]
    assert other_user.get(API + f"/records/{r['id']}/history").status_code == 404


def test_history_of_phone_edits(user):
    t = user.table("tasks", "Synced")
    F = user.fields(t["id"])
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Call bank"}})
    user.sync(t["id"])
    href, body = user.server_item(t["calendar_url"], "Call bank")
    user.put_item(href, body.replace("SUMMARY:Call bank", "SUMMARY:Call the bank"))
    user.sync(t["id"])
    last = user.j("GET", f"/records/{r['id']}/history")["entries"][0]
    assert last["actor"] == "Phone / calendar sync" and {c["field"] for c in last["changes"]} == {"Title"}
