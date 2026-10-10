from conftest import API, Client
import requests


def test_register_login_and_isolation(user, other_user):
    me = user.j("GET", "/auth/me")
    assert me["username"] == user.username
    t = user.table("generic", "Mine")
    # another user can't see or touch it
    assert other_user.get(API + f"/tables/{t['id']}").status_code == 404
    assert all(x["id"] != t["id"] for x in other_user.workspace()["tables"])
    # logging out ends the session
    user.post(API + "/auth/logout")
    assert user.get(API + "/auth/me").status_code == 401


def test_generic_table_records_and_record_id(user):
    t = user.table("generic", "People")
    F = user.fields(t["id"])
    assert {"Name", "Notes", "Status", "Record ID"} <= set(F)
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Ada", F["Status"]: "Doing"}})
    assert r["data"][F["Record ID"]] == r["id"]
    user.j("PATCH", f"/tables/{t['id']}/records/{r['id']}", json={"data": {F["Notes"]: "hello"}})
    assert user.record(t["id"], r["id"])["data"][F["Notes"]] == "hello"
    user.j("DELETE", f"/tables/{t['id']}/records/{r['id']}")
    assert user.records(t["id"]) == []


def test_import_converts_values_and_creates_fields(user):
    t = user.table("generic", "Imported")
    F = user.fields(t["id"])
    cols = [{"target": F["Name"]}, {"new": {"name": "Price", "type": "number"}}, {"new": {"name": "When", "type": "date"}},
            {"target": F["Status"]}, {"new": {"name": "Done", "type": "checkbox"}}, {"target": None}]
    rows = [["Apple", "1,200", "2026-10-12T09:30:00Z", "Brand new", "yes", "skip me"], ["", None, None, None, None, "only skipped"]]
    res = user.j("POST", f"/tables/{t['id']}/import", json={"columns": cols, "rows": rows})
    assert res["created"] == 1 and len(res["targets"]) == 6
    F = user.fields(t["id"])
    rec = user.records(t["id"])[0]["data"]
    assert rec[F["Price"]] == 1200 and rec[F["When"]] == "2026-10-12T09:30:00Z" and rec[F["Done"]] is True
    assert rec[F["Status"]] == "Brand new"   # new select options are added


def test_bulk_update_and_delete(user, other_user):
    t = user.table("generic", "Bulk")
    F = user.fields(t["id"])
    ids = [user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: n}})["id"] for n in "abc"]
    items = [{"id": i, "table_id": t["id"]} for i in ids]
    assert user.j("POST", "/records/bulk", json={"action": "update", "items": items, "data": {F["Status"]: "Done"}})["done"] == 3
    assert {r["data"][F["Status"]] for r in user.records(t["id"])} == {"Done"}
    # another user can't
    res = other_user.j("POST", "/records/bulk", json={"action": "delete", "items": items[:1]})
    assert res["done"] == 0
    assert user.j("POST", "/records/bulk", json={"action": "delete", "items": items})["done"] == 3


def test_combined_database_and_filter_groups(user):
    a, b = user.table("generic", "A"), user.table("generic", "B")
    for t, names in ((a, ["x1", "x2"]), (b, ["y1"])):
        F = user.fields(t["id"])
        for n in names:
            user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: n, F["Status"]: "Todo"}})
    c = user.j("POST", "/combos", json={"name": "Both", "sources": [{"table_id": a["id"], "color": "#f00"}, {"table_id": b["id"], "color": "#0f0"}], "base_id": user.base_id})
    combo = user.j("GET", f"/combos/{c['id']}")
    assert len(combo["records"]) == 3 and any(f["id"] == "__id" for f in combo["fields"])
