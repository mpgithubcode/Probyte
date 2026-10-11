"""REST API v1 in depth: schema, every record endpoint, filters/sort/paging/search, formats, keys and their limits, webhooks."""
import json

import requests

from conftest import API, BASE

V1 = BASE + "/api/v1"


def key(user, scope="write", **extra):
    return user.j("POST", "/keys", json={"name": f"k {scope}", "scope": scope, **extra})


def hdr(k):
    return {"Authorization": "Bearer " + k["key"]}


def setup_leads(user):
    W = hdr(key(user))
    t = requests.post(V1 + "/bases/My Base/tables", headers=W, json={"name": "Leads", "fields": [
        {"name": "Name"}, {"name": "Stage", "type": "single_select", "options": {"choices": ["New", "Won", "Lost"]}},
        {"name": "Value", "type": "number"}, {"name": "Due", "type": "date"}, {"name": "Tags", "type": "multi_select"}]})
    assert t.status_code == 201, t.text
    recs = [{"fields": {"Name": n, "Stage": s, "Value": v, "Due": d, "Tags": tags}} for n, s, v, d, tags in (
        ("Acme", "New", 100, "2026-10-01", ["hot"]), ("Beta", "Won", 500, "2026-11-01", []), ("Gamma", "Lost", 50, None, ["cold", "hot"]),
        ("Delta", "New", 900, "2026-12-01", []), ("Epsilon", "Won", 300, "2026-10-15", ["hot"]))]
    r = requests.post(V1 + "/tables/Leads/records", headers=W, json={"records": recs})
    assert r.status_code == 201, r.text
    return W, t.json(), r.json()["records"]


def test_schema_endpoints(user):
    W, t, _ = setup_leads(user)
    me = requests.get(V1 + "/me", headers=W).json()
    assert me["username"] == user.username and me["access"] == "write" and me["bases"] == "all"
    bases = requests.get(V1 + "/bases", headers=W).json()["bases"]
    assert any(tb["name"] == "Leads" for b in bases for tb in b["tables"])
    schema = requests.get(V1 + "/tables/Leads", headers=W).json()
    assert [f["name"] for f in schema["fields"]][:5] == ["Name", "Stage", "Value", "Due", "Tags"]
    f = requests.post(V1 + "/tables/Leads/fields", headers=W, json={"name": "Double", "type": "formula", "options": {"expression": "{Value} * 2"}})
    assert f.status_code == 201, f.text
    assert requests.post(V1 + "/tables/Leads/fields", headers=W, json={"name": "Bad", "type": "formula", "options": {"expression": "{Nope}"}}).status_code == 400
    assert requests.get(V1 + "/tables/Nope", headers=W).status_code == 404
    link = requests.post(V1 + "/tables/Leads/fields", headers=W, json={"name": "Related", "type": "link", "options": {"linked_table": "Leads"}})
    assert link.status_code == 201, link.text


def test_filters_sort_paging_search(user):
    W, _, _ = setup_leads(user)

    def q(**params):
        r = requests.get(V1 + "/tables/Leads/records", headers=W, params=params)
        assert r.status_code == 200, r.text
        return r.json()

    names = lambda res: [r["fields"]["Name"] for r in res["records"]]
    assert q(filter=json.dumps({"Stage": "Won"}))["total"] == 2
    assert names(q(filter=json.dumps([{"field": "Value", "op": "gte", "value": 300}]), sort="-Value")) == ["Delta", "Beta", "Epsilon"]
    assert q(filter=json.dumps([{"field": "Due", "op": "empty"}]))["total"] == 1
    assert q(filter=json.dumps([{"field": "Tags", "op": "contains", "value": "hot"}]))["total"] == 3
    assert q(filter=json.dumps([{"field": "Due", "op": "lt", "value": "2026-11-01"}]))["total"] == 2
    p1 = q(sort="Name", limit=2)
    assert names(p1) == ["Acme", "Beta"] and p1["next_offset"] == 2
    p3 = q(sort="Name", limit=2, offset=4)
    assert names(p3) == ["Gamma"] and p3["next_offset"] is None
    assert names(q(search="eps")) == ["Epsilon"]
    only = q(fields="Name,Value", limit=1)["records"][0]["fields"]
    assert set(only) <= {"Name", "Value"}
    text = q(cell_format="text", search="Gamma")["records"][0]["fields"]
    assert isinstance(text["Tags"], str)
    by_id = q(field_key="id", limit=1)["records"][0]["fields"]
    assert all(len(k) == 36 for k in by_id)
    for bad in ({"filter": "not json"}, {"filter": json.dumps([{"field": "Nope", "op": "is", "value": 1}])},
                {"filter": json.dumps([{"field": "Value", "op": "weird", "value": 1}])}, {"sort": "Nope"}, {"limit": 0}):
        assert requests.get(V1 + "/tables/Leads/records", headers=W, params=bad).status_code in (400, 422), bad
    found = requests.get(V1 + "/search", headers=W, params={"q": "delta"}).json()["results"]
    assert found and found[0]["title"] == "Delta" and found[0]["table"] == "Leads"


def test_record_crud_and_batches(user):
    W, _, recs = setup_leads(user)
    rid = recs[0]["id"]
    one = requests.get(f"{V1}/tables/Leads/records/{rid}", headers=W).json()
    assert one["fields"]["Name"] == "Acme"
    up = requests.patch(f"{V1}/tables/Leads/records/{rid}", headers=W, json={"fields": {"Value": "1,000", "Stage": "Won"}})
    assert up.status_code == 200 and up.json()["fields"]["Value"] == 1000
    batch = requests.patch(f"{V1}/tables/Leads/records", headers=W, json={"records": [
        {"id": recs[1]["id"], "fields": {"Value": 1}}, {"id": recs[2]["id"], "fields": {"Value": 2}}]})
    assert [r["fields"]["Value"] for r in batch.json()["records"]] == [1, 2]
    # all-or-nothing: one bad record and nothing changes
    bad = requests.patch(f"{V1}/tables/Leads/records", headers=W, json={"records": [
        {"id": recs[1]["id"], "fields": {"Value": 77}}, {"id": recs[2]["id"], "fields": {"Value": "lots"}}]})
    assert bad.status_code == 400
    assert requests.get(f"{V1}/tables/Leads/records/{recs[1]['id']}", headers=W).json()["fields"]["Value"] == 1
    bad = requests.post(f"{V1}/tables/Leads/records", headers=W, json={"records": [{"fields": {"Name": "ok"}}, {"fields": {"Value": "x"}}]})
    assert bad.status_code == 400
    assert requests.get(V1 + "/tables/Leads/records", headers=W).json()["total"] == 5
    d = requests.post(f"{V1}/tables/Leads/records/delete", headers=W, json={"ids": [recs[3]["id"], recs[4]["id"]]})
    assert d.status_code == 200 and len(d.json()["deleted"]) == 2
    assert requests.delete(f"{V1}/tables/Leads/records/{rid}", headers=W).status_code == 200
    assert requests.get(f"{V1}/tables/Leads/records/{rid}", headers=W).status_code == 404
    assert requests.get(V1 + "/tables/Leads/records", headers=W).json()["total"] == 2


def test_invalid_json_and_bad_values(user):
    W, _, _ = setup_leads(user)
    r = requests.post(f"{V1}/tables/Leads/records", headers={**W, "Content-Type": "application/json"}, data="{nope")
    assert r.status_code in (400, 422)
    for fields in ({"Value": "abc"}, {"Due": "next tuesday-ish"}, {"Nope": 1}, "not an object"):
        r = requests.post(f"{V1}/tables/Leads/records", headers=W, json={"fields": fields})
        assert r.status_code == 400, (fields, r.status_code, r.text)
        assert isinstance(r.json()["detail"], str)


def test_keys_scopes_base_limits_and_revocation(user, other_user):
    W, _, _ = setup_leads(user)
    other_base = user.j("POST", "/bases", json={"name": "Private"})
    hidden = user.j("POST", "/tables", json={"name": "Secret", "base_id": other_base["id"]})
    limited = key(user, "write", base_ids=[user.base_id])
    L = hdr(limited)
    assert requests.get(V1 + "/tables/Leads", headers=L).status_code == 200
    assert requests.get(V1 + f"/tables/{hidden['id']}", headers=L).status_code in (403, 404)
    assert all(b["id"] != other_base["id"] for b in requests.get(V1 + "/bases", headers=L).json()["bases"])
    # keys never reach another user's data
    O = hdr(key(other_user))
    assert requests.get(V1 + "/tables/Leads", headers=O).status_code == 404
    # bad / revoked keys
    assert requests.get(V1 + "/me", headers={"Authorization": "Bearer pbk_nope"}).status_code == 401
    keys = user.j("GET", "/keys")
    k = next(x for x in (keys if isinstance(keys, list) else keys["keys"]) if x["id"] == limited["id"])
    assert "key" not in k or not k["key"]          # the secret isn't shown again
    user.j("PATCH", f"/keys/{limited['id']}", json={"name": "renamed", "scope": "read"})
    assert requests.post(V1 + "/tables/Leads/records", headers=L, json={"fields": {"Name": "x"}}).status_code == 403
    user.j("DELETE", f"/keys/{limited['id']}")
    assert requests.get(V1 + "/me", headers=L).status_code == 401
    # a key can't manage keys or log in to the app's own endpoints that need a session
    assert requests.post(API + "/keys", headers=W, json={"name": "x", "scope": "write"}).status_code in (401, 403)


def test_webhook_management(user):
    W, _, _ = setup_leads(user)
    R = hdr(key(user, "read"))
    assert requests.post(V1 + "/webhooks", headers=W, json={"url": "ftp://example.com"}).status_code == 400
    assert requests.post(V1 + "/webhooks", headers=R, json={"url": "https://example.com/h"}).status_code == 403
    h = requests.post(V1 + "/webhooks", headers=W, json={"url": "https://example.com/h", "tables": ["Leads"], "events": ["record.created"]})
    assert h.status_code == 201 and h.json()["secret"].startswith("whsec_")
    hid = h.json()["id"]
    listed = requests.get(V1 + "/webhooks", headers=W).json()["webhooks"]
    assert listed[0]["id"] == hid and "secret" not in listed[0]           # API keys don't see secrets
    p = requests.patch(f"{V1}/webhooks/{hid}", headers=W, json={"enabled": False, "all_tables": True}).json()
    assert p["enabled"] is False and p["tables"] is None
    assert requests.post(f"{V1}/webhooks/{hid}/test", headers=R).status_code == 403   # read keys don't send anything
    assert requests.delete(f"{V1}/webhooks/{hid}", headers=R).status_code == 403
    assert requests.delete(f"{V1}/webhooks/{hid}", headers=W).status_code == 200
    assert requests.get(V1 + "/webhooks", headers=W).json()["webhooks"] == []


def test_openapi_and_docs():
    spec = requests.get(V1 + "/openapi.json").json()
    paths = set(spec["paths"])
    assert {"/tables/{table}/records", "/tables/{table}/records/upsert", "/webhooks", "/search"} <= paths
    assert requests.get(V1 + "/docs").status_code == 200
