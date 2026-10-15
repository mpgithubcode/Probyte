"""Pages (a table's records laid out like documents), how fields holding several records or values are shown, and the
linked records those views fetch."""
from conftest import API


def projects_and_tasks(user):
    tasks = user.table(name="Tasks")
    T = user.fields(tasks["id"])
    est = user.j("POST", f"/tables/{tasks['id']}/fields", json={"name": "Hours", "type": "number", "options": {}})
    projects = user.table(name="Projects")
    link = user.j("POST", f"/tables/{projects['id']}/fields", json={"name": "Tasks", "type": "link",
                                                                      "options": {"foreignTableId": tasks["id"], "symmetric": False}})
    notes = user.j("POST", f"/tables/{projects['id']}/fields", json={"name": "Brief", "type": "long_text", "options": {}})
    a = user.j("POST", f"/tables/{tasks['id']}/records", json={"data": {T["Name"]: "Wire panel", est["id"]: 3}})
    b = user.j("POST", f"/tables/{tasks['id']}/records", json={"data": {T["Name"]: "Pull cable", est["id"]: 5}})
    P = user.fields(projects["id"])
    p = user.j("POST", f"/tables/{projects['id']}/records", json={"data": {P["Name"]: "Mill St", link["id"]: [b["id"], a["id"]]}})
    return tasks, projects, link, notes, est, a, b, p


def test_a_tables_page_is_kept_checked_and_removed(user):
    _, projects, link, notes, _, _, _, _ = projects_and_tasks(user)
    page = {"enabled": True, "blocks": [
        {"id": "h", "type": "heading", "text": "  Work  "},
        {"id": "t", "type": "field", "field": link["id"], "view": "table", "width": "half", "junk": 1},
        {"id": "d", "type": "divider", "width": "half"},
        {"id": "b", "type": "doc", "field": notes["id"]},
        {"id": "p", "type": "properties"},
    ]}
    out = user.j("PATCH", f"/tables/{projects['id']}", json={"page": page})
    assert out["page"] == {"enabled": True, "blocks": [
        {"id": "h", "type": "heading", "text": "Work"},
        {"id": "t", "type": "field", "field": link["id"], "view": "table", "width": "half"},
        {"id": "d", "type": "divider"},                                  # (a divider has no width)
        {"id": "b", "type": "doc", "field": notes["id"]},
        {"id": "p", "type": "properties"},
    ]}
    assert user.j("GET", f"/tables/{projects['id']}")["table"]["page"]["enabled"] is True
    P = user.fields(projects["id"])
    bad = [
        {"blocks": [{"id": "x", "type": "doc", "field": P["Name"]}]},              # only a long text is a page body
        {"blocks": [{"id": "x", "type": "field", "field": "nope"}]},                # a field of this table
        {"blocks": [{"id": "x", "type": "field", "field": link["id"], "view": "sideways"}]},
        {"blocks": [{"id": "x", "type": "poem"}]},
        {"blocks": [{"id": "x", "type": "divider"}, {"id": "x", "type": "divider"}]},
    ]
    for b in bad:
        assert user.request("PATCH", API + f"/tables/{projects['id']}", json={"page": {"enabled": True, **b}}).status_code == 400, b
    # {} takes the page away
    assert user.j("PATCH", f"/tables/{projects['id']}", json={"page": {}})["page"] is None


def test_how_a_field_is_shown_is_kept_on_the_field(user):
    tasks, projects, link, _, est, _, _, _ = projects_and_tasks(user)
    views = {"cell": "count", "record": "cards", "fields": [est["id"]]}
    f = user.j("PATCH", f"/fields/{link['id']}", json={"options": {"foreignTableId": tasks["id"], "views": views}})
    assert f["options"]["views"] == views
    got = next(x for x in user.j("GET", f"/tables/{projects['id']}")["fields"] if x["id"] == link["id"])
    assert got["options"]["views"] == views
    r = user.request("PATCH", API + f"/fields/{link['id']}", json={"options": {"foreignTableId": tasks["id"], "views": {"cell": "sideways"}}})
    assert r.status_code == 400
    roll = user.j("POST", f"/tables/{projects['id']}/fields", json={"name": "Total", "type": "rollup", "options": {
        "linkFieldId": link["id"], "lookupFieldId": est["id"], "func": "sum", "views": {"cell": "bar", "max": 10}}})
    assert roll["options"]["views"] == {"cell": "bar", "max": 10}
    # no choice: nothing kept
    f = user.j("PATCH", f"/fields/{link['id']}", json={"options": {"foreignTableId": tasks["id"], "views": {}}})
    assert "views" not in f["options"]


def test_linked_records_are_fetched_with_every_field_in_the_order_asked(user, other_user):
    tasks, _, _, _, est, a, b, _ = projects_and_tasks(user)
    out = user.j("POST", f"/tables/{tasks['id']}/records/lookup", json={"ids": [b["id"], a["id"], b["id"], "not-a-record"]})
    assert [r["id"] for r in out["records"]] == [b["id"], a["id"]]
    assert [r["data"][est["id"]] for r in out["records"]] == [5, 3]
    assert {f["name"] for f in out["fields"]} >= {"Name", "Hours"} and "records" in out["refs"]
    # someone else's table: not found
    assert other_user.request("POST", API + f"/tables/{tasks['id']}/records/lookup", json={"ids": [a["id"]]}).status_code == 404
