"""Whatever an API client, AI assistant or script sends, a field keeps holding what its type holds: a value of the
wrong shape is refused (never stored as it came), and select options stay a list of names — so the app can't be
broken by a write, and a type or a field's options never change because of one."""
import requests

from conftest import API, BASE


def _key(user):
    return {"Authorization": "Bearer " + user.j("POST", "/keys", json={"name": "ai", "scope": "write"})["key"]}


def _rpc(h, name, args):
    return requests.post(BASE + "/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                         "params": {"name": name, "arguments": args}}).json()["result"]


def test_wrong_shaped_values_are_refused_by_the_app_api(user):
    t = user.table(name="Bids")
    F = user.fields(t["id"])
    cat = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Category", "type": "single_select",
                                                           "options": {"choices": [{"name": "Opportunity"}]}})
    day = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Due", "type": "date"})
    num = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Amount", "type": "number"})
    H = _key(user)
    post = lambda data: requests.post(API + f"/tables/{t['id']}/records", headers=H, json={"data": data})
    assert post({F["Name"]: {"a": 1}}).status_code == 400
    assert post({cat["id"]: ["a", "b"]}).status_code == 400
    assert post({cat["id"]: {"x": 1}}).status_code == 400
    assert post({day["id"]: "next tuesday"}).status_code == 400
    assert post({num["id"]: "NaN"}).status_code == 400
    # written the way people mean it: kept as the type holds it
    ok = post({cat["id"]: {"name": "Not in Scope"}, F["Name"]: 42, day["id"]: "2026-10-16"})
    assert ok.status_code == 200, ok.text
    rec = ok.json()
    assert rec["data"][cat["id"]] == "Not in Scope" and rec["data"][F["Name"]] == "42"
    f = next(x for x in user.j("GET", f"/tables/{t['id']}")["fields"] if x["id"] == cat["id"])
    assert f["type"] == "single_select" and [c["name"] for c in f["options"]["choices"]] == ["Opportunity", "Not in Scope"]
    assert all(set(c) == {"name", "color"} for c in f["options"]["choices"])


def test_field_options_are_cleaned_or_refused(user):
    t = user.table(name="Opts")
    f = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Category", "type": "single_select", "options": {
        "choices": ["A", {"label": "B"}, {"name": "A"}, {"name": "C", "color": "red; x"}, " D  "]}})
    assert [c["name"] for c in f["options"]["choices"]] == ["A", "B", "C", "D"]
    assert all(c["color"].startswith("#") for c in f["options"]["choices"])
    r = user.patch(API + f"/fields/{f['id']}", json={"options": {"choices": "A,B"}})
    assert r.status_code == 400
    r = user.patch(API + f"/fields/{f['id']}", json={"options": {"choices": [[1, 2]]}})
    assert r.status_code == 400


def test_mcp_writes_keep_the_schema(user):
    t = user.table(name="Leads")
    user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Category", "type": "single_select", "options": {"choices": ["Hot"]}})
    H = _key(user)
    res = _rpc(H, "create_records", {"table": "Leads", "records": [{"Name": "x", "Category": {"weird": True}}]})
    assert res["isError"]
    res = _rpc(H, "create_records", {"table": "Leads", "records": [{"Name": "y", "Category": ["Hot", "Cold"]}]})
    assert res["isError"]
    res = _rpc(H, "add_field", {"table": "Leads", "name": "Tags", "type": "multi_select", "options": {"choices": [{"label": "a"}, "b"]}})
    assert not res["isError"]
    f = next(x for x in user.j("GET", f"/tables/{t['id']}")["fields"] if x["name"] == "Tags")
    assert [c["name"] for c in f["options"]["choices"]] == ["a", "b"]
    f = next(x for x in user.j("GET", f"/tables/{t['id']}")["fields"] if x["name"] == "Category")
    assert f["type"] == "single_select" and [c["name"] for c in f["options"]["choices"]] == ["Hot"]


def test_api_writes_dont_add_options_unless_asked(user):
    t = user.table(name="Grants")
    user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Category", "type": "single_select", "options": {"choices": ["Federal", "State"]}})
    H = _key(user)
    res = _rpc(H, "create_records", {"table": "Grants", "records": [{"Name": "a", "Category": "Local"}]})
    assert res["isError"] and "Federal" in res["content"][0]["text"]
    assert not _rpc(H, "create_records", {"table": "Grants", "records": [{"Name": "a", "Category": "State"}]})["isError"]
    assert not _rpc(H, "create_records", {"table": "Grants", "records": [{"Name": "b", "Category": "Local"}], "add_options": True})["isError"]
    f = next(x for x in user.j("GET", f"/tables/{t['id']}")["fields"] if x["name"] == "Category")
    assert [c["name"] for c in f["options"]["choices"]] == ["Federal", "State", "Local"]
