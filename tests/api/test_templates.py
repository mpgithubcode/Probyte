"""Templates: tables and views as portable JSON, saved, downloaded, uploaded and used."""
import json

from conftest import API


def test_table_template_round_trip(user):
    t = user.table(name="Jobs")
    F = user.fields(t["id"])
    hours = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Hours", "type": "number", "required": True, "default_value": 1})
    rate = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Rate", "type": "number"})
    user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Total", "type": "formula", "options": {"expression": "{Hours} * {Rate}"}})
    other = user.table(name="Clients")
    user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Client", "type": "link", "options": {"foreignTableId": other["id"]}})
    user.j("POST", f"/tables/{t['id']}/views", json={"name": "Board", "type": "kanban", "config": {"groupBy": F["Status"], "hidden": [F["Notes"]]}})
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Panel", F["Status"]: "Doing", hours["id"]: 3, rate["id"]: 90}})
    # download (not saved): fields by name, the link left out
    r = user.post(API + "/templates/export", json={"kind": "table", "table_id": t["id"], "include_records": True})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    tpl = r.json()
    assert tpl["kind"] == "table" and "Client" in tpl["skipped"] and tpl["records"][0]["Name"] == "Panel"
    board = next(v for v in tpl["views"] if v["name"] == "Board")
    assert board["config"]["groupBy"] == "{{field:Status}}" and board["config"]["hidden"] == ["{{field:Notes}}"]
    assert next(f for f in tpl["fields"] if f["name"] == "Total")["options"]["expression"] == "{Hours} * {Rate}"
    assert F["Status"] not in json.dumps(tpl)                              # no ids of this table
    # a new table from the file's contents
    new = user.j("POST", "/tables/from-template", json={"body": tpl, "base_id": user.base_id, "name": "Jobs 2"})
    N = user.fields(new["id"])
    info = user.j("GET", f"/tables/{new['id']}")
    assert [f["name"] for f in info["fields"]] == ["Name", "Notes", "Status", "Hours", "Rate", "Total",
                                                     "Created time", "Last modified time", "Created by", "Last modified by", "Record ID"]
    assert next(f for f in info["fields"] if f["name"] == "Hours")["required"]
    b2 = next(v for v in info["views"] if v["name"] == "Board")
    assert b2["config"]["groupBy"] == N["Status"] and b2["config"]["hidden"] == [N["Notes"]]
    rec = info["records"][0]["data"]
    assert rec[N["Name"]] == "Panel" and rec[N["Total"]] == 270
    # saved, listed, downloaded, deleted
    s = user.j("POST", "/templates", json={"kind": "table", "table_id": t["id"], "name": "My jobs"})
    listed = user.j("GET", "/templates", params={"kind": "table"})
    assert any(x["id"] == s["id"] and x["summary"]["fields"] == 6 for x in listed)
    assert user.get(API + f"/templates/{s['id']}/download").json()["name"] == "My jobs"
    t3 = user.j("POST", "/tables/from-template", json={"template_id": s["id"], "base_id": user.base_id, "include_records": False})
    assert t3["name"] == "My jobs" and user.records(t3["id"]) == []
    user.j("DELETE", f"/templates/{s['id']}")
    assert user.get(API + f"/templates/{s['id']}/download").status_code == 404


def test_builtin_templates(user):
    listed = user.j("GET", "/templates")
    names = {x["name"] for x in listed if x["builtin"]}
    assert {"Job tracker", "Project tracker", "Clients", "Inventory"} <= names
    for b in [x for x in listed if x["builtin"]]:
        t = user.j("POST", "/tables/from-template", json={"template_id": b["id"], "base_id": user.base_id})
        info = user.j("GET", f"/tables/{t['id']}")
        assert len(info["views"]) >= 3 and info["records"], b["name"]
        assert not any(f["options"].get("_error") for f in info["fields"]), b["name"]
    jobs = next(x for x in listed if x["name"] == "Job tracker")
    t = user.j("POST", "/tables/from-template", json={"template_id": jobs["id"], "base_id": user.base_id})
    J = user.fields(t["id"])
    panel = next(r for r in user.records(t["id"]) if r["data"][J["Job"]] == "Panel upgrade to 200A")
    assert panel["data"][J["Invoice total"]] == 6 * 95 + 1450


def test_view_template_matches_fields_by_name(user, other_user):
    a = user.table(name="A")
    FA = user.fields(a["id"])
    due = user.j("POST", f"/tables/{a['id']}/fields", json={"name": "Due", "type": "date"})
    v = user.j("POST", f"/tables/{a['id']}/views", json={"name": "Week", "type": "calendar", "config": {
        "dateField": due["id"], "colorField": FA["Status"],
        "filter": {"conjunction": "and", "items": [{"field": FA["Status"], "op": "is_not", "value": "Done"},
                                                    {"field": FA["Notes"], "op": "not_empty"}]}}})
    tpl = user.post(API + "/templates/export", json={"kind": "view", "view_id": v["id"]}).json()
    assert tpl["config"]["dateField"] == "{{field:Due}}"
    # another table: has Due and Status but no Notes -> that condition is dropped
    b = user.table(name="B")
    FB = user.fields(b["id"])
    user.j("DELETE", f"/fields/{FB['Notes']}")
    due_b = user.j("POST", f"/tables/{b['id']}/fields", json={"name": "due", "type": "date"})
    nv = user.j("POST", "/views/from-template", json={"body": tpl, "table_id": b["id"]})
    assert nv["type"] == "calendar" and nv["config"]["dateField"] == due_b["id"] and nv["config"]["colorField"] == FB["Status"]
    assert [i["field"] for i in nv["config"]["filter"]["items"]] == [FB["Status"]]
    # someone else's table: no
    assert other_user.request("POST", API + "/views/from-template", json={"body": tpl, "table_id": b["id"]}).status_code == 404


def test_bad_template_files(user):
    r = user.post(API + "/templates/upload", files={"file": ("x.json", b"not json", "application/json")})
    assert r.status_code == 400 and "template" in r.text
    r = user.post(API + "/templates/upload", files={"file": ("x.json", json.dumps({"probyte_template": 1, "kind": "table", "name": "X",
                  "fields": [{"name": "A", "type": "nonsense"}]}).encode(), "application/json")})
    assert r.status_code == 400
    ok = user.post(API + "/templates/upload", files={"file": ("x.json", json.dumps({"probyte_template": 1, "kind": "view", "name": "V",
                   "type": "kanban", "config": {}}).encode(), "application/json")}).json()
    assert ok["kind"] == "view" and ok["summary"]["type"] == "kanban"
