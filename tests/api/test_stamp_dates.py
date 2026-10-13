"""Started date / Completed date: dates that fill themselves in when a formula becomes true."""
from conftest import API


def test_started_and_completed_dates_fill_themselves_in(user):
    t = user.table(name="Work")
    F = user.fields(t["id"])
    done = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Done", "type": "checkbox"})
    early = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Already going", F["Status"]: "Doing"}})
    started = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Started date", "type": "date", "options": {
        "stampWhen": '{Status} = "Doing"', "stampKind": "started", "includeTime": True}})
    completed = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Completed date", "type": "date", "options": {
        "stampWhen": "{Done}", "stampKind": "completed", "stampClear": True}})
    assert started["options"]["displayStampWhen"] == '{Status} = "Doing"' and started["options"]["stampKind"] == "started"
    # a record it already applied to: filled in when the field was made
    assert user.record(t["id"], early["id"])["data"][started["id"]]
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Panel", F["Status"]: "Todo"}})
    assert not r["data"].get(started["id"])
    user.j("PATCH", f"/records/{r['id']}", json={"data": {F["Status"]: "Doing"}})
    first = user.record(t["id"], r["id"])["data"][started["id"]]
    assert first and "T" in first                                          # with the time
    user.j("PATCH", f"/records/{r['id']}", json={"data": {F["Status"]: "Done", done["id"]: True}})
    d = user.record(t["id"], r["id"])["data"]
    assert d[started["id"]] == first                                       # kept: no "clear"
    assert len(d[completed["id"]]) == 10                                   # a date, no time
    user.j("PATCH", f"/records/{r['id']}", json={"data": {done["id"]: False}})
    assert not user.record(t["id"], r["id"])["data"].get(completed["id"])  # cleared when undone
    # created already true
    r2 = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Quick", done["id"]: True}})
    assert user.record(t["id"], r2["id"])["data"][completed["id"]]
    # a formula that doesn't work is refused; an empty one makes it a plain date again
    assert user.patch(API + f"/fields/{completed['id']}", json={"options": {"stampWhen": "{Nope}"}}).status_code == 400
    plain = user.j("PATCH", f"/fields/{completed['id']}", json={"options": {"stampWhen": ""}})
    assert "stampWhen" not in plain["options"]
