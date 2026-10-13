"""Date filters with periods worked out now (today, current week, 3 days ago…), on the server: views read through the
API, conditional lookups, and the same rules as in the browser."""
from datetime import date, timedelta

import requests

from conftest import BASE

V1 = BASE + "/api/v1"


def test_relative_date_filters(user):
    t = user.table(name="Due dates")
    F = user.fields(t["id"])
    due = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Due", "type": "date"})
    now = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Now", "type": "formula", "options": {"expression": "TODAY()"}})
    probe = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "probe"}})
    today = date.fromisoformat(user.record(t["id"], probe["id"])["data"][now["id"]][:10])   # the server's today
    user.j("DELETE", f"/records/{probe['id']}")
    for name, days in (("past", -10), ("yesterday", -1), ("today", 0), ("soon", 3), ("later", 40)):
        user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: name, due["id"]: (today + timedelta(days=days)).isoformat()}})
    W = {"Authorization": "Bearer " + user.j("POST", "/keys", json={"name": "k", "scope": "write"})["key"]}

    def names(op, value):
        v = user.j("POST", f"/tables/{t['id']}/views", json={"name": f"{op} {value}", "type": "grid", "config": {
            "filter": {"conjunction": "and", "items": [{"field": due["id"], "op": op, "value": value}]}}})
        r = requests.get(f"{V1}/tables/{t['id']}/records", headers=W, params={"view": v["id"]})
        assert r.status_code == 200, r.text
        return sorted(x["fields"]["Name"] for x in r.json()["records"])

    assert names("is", "today") == ["today"]
    assert names("is", "today()") == ["today"]
    assert names("is", "yesterday") == ["yesterday"]
    assert names("is", "10 days ago") == ["past"]
    assert names("is", "3 days from now") == ["soon"] == names("is", "in 3 days") == names("is", "today() + 3")
    assert names("is", "past 7 days") == ["today", "yesterday"]
    assert names("is", "next 7 days") == ["soon", "today"]
    assert names("lt", "today") == ["past", "yesterday"]                  # before
    assert names("gt", "today") == ["later", "soon"]                      # after
    assert names("is_not", "today") == ["later", "past", "soon", "yesterday"]
    lo, hi = today - timedelta(days=11), today + timedelta(days=4)
    assert names("is", f"{lo}..{hi}") == ["past", "soon", "today", "yesterday"]   # a custom range
    week = names("is", "current week")
    assert "today" in week and "later" not in week and "past" not in week
    assert "later" in names("is", "next 60 days")
    # a conditional lookup: the records due in the past 7 days
    look = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Recent", "type": "conditional_lookup", "options": {
        "foreignTableId": t["id"], "lookupFieldId": F["Name"], "filters": [{"field": due["id"], "op": "is", "value": "past 7 days"}]}})
    any_rec = user.records(t["id"])[0]
    assert sorted(user.record(t["id"], any_rec["id"])["data"][look["id"]]) == ["today", "yesterday"]
