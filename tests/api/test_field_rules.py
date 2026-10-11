"""The field editor's settings: description, required, unique, default values, only-listed select options."""
from datetime import date

from conftest import API


def fail(user, method, path, **kw):
    r = user.request(method, API + path, **kw)
    assert r.status_code == 400, r.text
    return r.json()["detail"]


def test_required_unique_and_defaults(user):
    t = user.table(name="Rules")
    tid = t["id"]
    code = user.j("POST", f"/tables/{tid}/fields", json={"name": "Code", "type": "text", "required": True, "unique": True,
                                                          "description": "The product code"})
    assert code["required"] and code["unique_values"] and code["description"] == "The product code"
    qty = user.j("POST", f"/tables/{tid}/fields", json={"name": "Qty", "type": "number", "default_value": 1})
    day = user.j("POST", f"/tables/{tid}/fields", json={"name": "Added", "type": "date", "default_value": "today"})
    done = user.j("POST", f"/tables/{tid}/fields", json={"name": "Ok", "type": "checkbox", "default_value": True})
    who = user.j("POST", f"/tables/{tid}/fields", json={"name": "Owner", "type": "user", "default_value": ["me"]})

    assert "Code is required" in fail(user, "POST", f"/tables/{tid}/records", json={"data": {}})
    r = user.j("POST", f"/tables/{tid}/records", json={"data": {code["id"]: "A-1"}})
    d = r["data"]
    assert d[qty["id"]] == 1 and d[done["id"]] is True and d[who["id"]] and len(d[who["id"]]) == 1
    assert d[day["id"]][:4] == str(date.today().year)
    # an explicit value wins over the default
    r2 = user.j("POST", f"/tables/{tid}/records", json={"data": {code["id"]: "A-2", qty["id"]: 5, done["id"]: False}})
    assert r2["data"][qty["id"]] == 5 and r2["data"][done["id"]] is False
    # unique, ignoring case; required can't be cleared
    assert "unique" in fail(user, "POST", f"/tables/{tid}/records", json={"data": {code["id"]: "a-1"}})
    assert "unique" in fail(user, "PATCH", f"/records/{r2['id']}", json={"data": {code["id"]: "A-1"}})
    assert "required" in fail(user, "PATCH", f"/records/{r2['id']}", json={"data": {code["id"]: ""}})
    user.j("PATCH", f"/records/{r['id']}", json={"data": {code["id"]: "A-1"}})                 # same value: fine
    user.j("PATCH", f"/records/{r['id']}", json={"data": {qty["id"]: 9}})                      # other fields: fine
    # removing the default; turning unique on with duplicates is refused
    user.j("PATCH", f"/fields/{qty['id']}", json={"default_value": None})
    assert user.j("POST", f"/tables/{tid}/records", json={"data": {code["id"]: "A-3"}})["data"].get(qty["id"]) is None
    user.j("PATCH", f"/records/{r['id']}", json={"data": {qty["id"]: 5}})
    assert "more than once" in fail(user, "PATCH", f"/fields/{qty['id']}", json={"unique": True})
    # rules show in the API's field list
    key = user.j("POST", "/keys", json={"name": "k", "scope": "read"})["key"]
    fields = user.get(API.replace("/api", "") + f"/api/v1/tables/{tid}", headers={"Authorization": "Bearer " + key}).json()["fields"]
    c = next(f for f in fields if f["name"] == "Code")
    assert c["required"] and c["unique"] and c["description"] == "The product code"


def test_only_listed_options(user):
    t = user.table(name="Choices")
    s = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Size", "type": "single_select", "options": {
        "choices": [{"name": "S", "color": "#3b82f6"}, {"name": "M", "color": "#22c55e"}], "allowNew": False}, "default_value": "M"})
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {}})
    assert r["data"][s["id"]] == "M"
    assert "isn't one of the options" in fail(user, "PATCH", f"/records/{r['id']}", json={"data": {s["id"]: "XL"}})
    user.j("PATCH", f"/fields/{s['id']}", json={"options": {"choices": s["options"]["choices"]}})   # allowNew back on
    assert user.j("PATCH", f"/records/{r['id']}", json={"data": {s["id"]: "XL"}})["data"][s["id"]] == "XL"


def test_locked_fields_can_get_a_description(user):
    t = user.table("tasks", "Described")
    f = next(f for f in user.j("GET", f"/tables/{t['id']}")["fields"] if f.get("system_key") == "summary")
    out = user.j("PATCH", f"/fields/{f['id']}", json={"description": "What to do"})
    assert out["description"] == "What to do"
    assert "locked" in fail(user, "PATCH", f"/fields/{f['id']}", json={"name": "Other"})
