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
        {"id": "t", "type": "field", "field": link["id"], "view": "table", "width": "half", "junk": 1},   # (0.9.7's widths)
        {"id": "d", "type": "divider", "width": "half"},
        {"id": "b", "type": "doc", "field": notes["id"], "span": 3, "height": 400.6},
        {"id": "p", "type": "properties", "span": 4},
    ]}
    out = user.j("PATCH", f"/tables/{projects['id']}", json={"page": page})
    assert out["page"] == {"enabled": True, "blocks": [
        {"id": "h", "type": "heading", "text": "Work"},
        {"id": "t", "type": "field", "field": link["id"], "view": "table", "span": 2},
        {"id": "d", "type": "divider"},                                  # (a divider has no width)
        {"id": "b", "type": "doc", "field": notes["id"], "span": 3, "height": 400},
        {"id": "p", "type": "properties"},                              # (4 columns: the whole width, the default)
    ]}
    assert user.j("GET", f"/tables/{projects['id']}")["table"]["page"]["enabled"] is True
    P = user.fields(projects["id"])
    bad = [
        {"blocks": [{"id": "x", "type": "doc", "field": P["Name"]}]},              # only a long text is a page body
        {"blocks": [{"id": "x", "type": "field", "field": "nope"}]},                # a field of this table
        {"blocks": [{"id": "x", "type": "field", "field": link["id"], "view": "sideways"}]},
        {"blocks": [{"id": "x", "type": "poem"}]},
        {"blocks": [{"id": "x", "type": "divider"}, {"id": "x", "type": "divider"}]},
        {"blocks": [{"id": "x", "type": "field", "field": link["id"], "span": 5}]},
        {"blocks": [{"id": "x", "type": "field", "field": link["id"], "height": 10}]},
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


def test_the_primary_field_can_be_changed(user, other_user):
    tasks, projects, link, notes, est, a, b, p = projects_and_tasks(user)
    T = user.fields(tasks["id"])
    user.j("POST", f"/tables/{tasks['id']}/primary", json={"field_id": est["id"]})
    info = user.j("GET", f"/tables/{tasks['id']}")
    assert [f["name"] for f in info["fields"] if f.get("is_primary")] == ["Hours"]
    assert info["fields"][0]["name"] == "Hours"                            # it comes first
    # titles follow it: the linked records are called by their hours now
    assert user.j("GET", f"/tables/{projects['id']}")["refs"]["records"][b["id"]] == "5"
    # not every kind of field can be one; not someone else's table
    roll = user.j("POST", f"/tables/{tasks['id']}/fields", json={"name": "Done", "type": "checkbox", "options": {}})
    assert user.request("POST", API + f"/tables/{tasks['id']}/primary", json={"field_id": roll["id"]}).status_code == 400
    assert other_user.request("POST", API + f"/tables/{tasks['id']}/primary", json={"field_id": T["Name"]}).status_code == 404
    user.j("POST", f"/tables/{tasks['id']}/primary", json={"field_id": T["Name"]})
    assert user.j("GET", f"/tables/{projects['id']}")["refs"]["records"][b["id"]] == "Pull cable"


def test_sections_tabs_and_the_other_blocks_are_kept_and_checked(user):
    tasks, projects, link, notes, est, a, b, p = projects_and_tasks(user)
    P = user.fields(projects["id"])
    att = user.j("POST", f"/tables/{projects['id']}/fields", json={"name": "Photos", "type": "attachment", "options": {}})
    est = user.j("POST", f"/tables/{projects['id']}/fields", json={"name": "Budget", "type": "number", "options": {}})
    when = {"conjunction": "and", "items": [{"field": P["Status"], "op": "is", "value": "Done"}]}
    page = {"enabled": True, "header": [P["Status"]], "outline": True, "template": "## Scope\n\n## Notes",
            "variants": {"field": P["Status"], "layouts": {"Done": [{"id": "v1", "type": "field", "field": P["Status"]}]}},
            "blocks": [
                {"id": "s", "type": "section", "text": "Money", "closed": True, "blocks": [{"id": "s1", "type": "stats", "items": [{"field": est["id"], "view": "bar"}]}]},
                {"id": "t", "type": "tabs", "tabs": [{"id": "t1", "text": "Files", "blocks": [{"id": "f1", "type": "files", "field": att["id"], "span": 2}]},
                                                     {"id": "t2", "text": "", "blocks": [{"id": "r1", "type": "related", "table": tasks["id"], "field": "x", "display": "cards"}]}]},
                {"id": "w", "type": "heading", "text": "Done!", "when": when},
            ]}
    out = user.j("PATCH", f"/tables/{projects['id']}", json={"page": page})["page"]
    assert out["header"] == [P["Status"]] and out["outline"] and out["template"].startswith("## Scope")
    assert out["blocks"][0]["blocks"][0]["items"] == [{"field": est["id"], "view": "bar"}] and out["blocks"][0]["closed"]
    assert out["blocks"][1]["tabs"][1]["text"] == "Tab" and out["blocks"][1]["tabs"][0]["blocks"][0]["span"] == 2
    assert out["blocks"][2]["when"] == when and out["variants"]["layouts"]["Done"][0]["field"] == P["Status"]
    bad = [
        {"blocks": [{"id": "s", "type": "section", "blocks": [{"id": "s", "type": "divider"}]}]},          # ids unique, nested too
        {"blocks": [{"id": "f", "type": "files", "field": est["id"]}]},                                       # files: attachments / links
        {"blocks": [{"id": "h", "type": "heading", "when": {"items": [{"field": "nope", "op": "is"}]}}]},     # conditions on this table
        {"blocks": [], "header": ["nope"]},
        {"blocks": [], "variants": {"field": est["id"], "layouts": {}}},                                        # by a select or checkbox
        {"blocks": [{"id": "t", "type": "tabs", "tabs": []}]},
    ]
    for x in bad:
        assert user.request("PATCH", API + f"/tables/{projects['id']}", json={"page": {"enabled": True, **x}}).status_code == 400, x


def test_new_records_start_with_the_page_template(user):
    _, projects, link, notes, _, _, _, _ = projects_and_tasks(user)
    P = user.fields(projects["id"])
    user.j("PATCH", f"/tables/{projects['id']}", json={"page": {"enabled": True, "blocks": [{"id": "b", "type": "doc", "field": notes["id"]}],
                                                               "template": "## Scope\n\n{{field:" + link["id"] + "}}"}})
    r = user.j("POST", f"/tables/{projects['id']}/records", json={"data": {P["Name"]: "New job"}})
    assert r["data"][notes["id"]] == "## Scope\n\n{{field:" + link["id"] + "}}"
    r = user.j("POST", f"/tables/{projects['id']}/records", json={"data": {P["Name"]: "Own text", notes["id"]: "Mine"}})
    assert r["data"][notes["id"]] == "Mine"                                          # what's given wins


def test_related_records_and_the_links_that_can_show_them(user, other_user):
    tasks, projects, link, notes, est, a, b, p = projects_and_tasks(user)
    # tasks linking to a project (a link in Tasks pointing at Projects)
    back = user.j("POST", f"/tables/{tasks['id']}/fields", json={"name": "Project", "type": "link", "options": {"foreignTableId": projects["id"], "symmetric": False}})
    user.j("PATCH", f"/records/{a['id']}", json={"data": {back["id"]: [p["id"]]}})
    user.j("PATCH", f"/records/{b['id']}", json={"data": {back["id"]: [p["id"]]}})
    inbound = user.j("GET", f"/tables/{projects['id']}/inbound")
    assert any(i["field_id"] == back["id"] and i["table_name"] == "Tasks" and i["views"] for i in inbound)
    out = user.j("POST", f"/tables/{tasks['id']}/records/related", json={"field": back["id"], "record": p["id"]})
    assert {r["id"] for r in out["records"]} == {a["id"], b["id"]} and out["total"] == 2
    out = user.j("POST", f"/tables/{tasks['id']}/records/related", json={"field": back["id"], "record": p["id"], "limit": 1})
    assert len(out["records"]) == 1 and out["total"] == 2
    assert other_user.request("POST", API + f"/tables/{tasks['id']}/records/related", json={"field": back["id"], "record": p["id"]}).status_code == 404


def test_a_link_to_one_page(user):
    import requests
    from conftest import BASE
    tasks, projects, link, notes, est, a, b, p = projects_and_tasks(user)
    P = user.fields(projects["id"])
    other = user.j("POST", f"/tables/{projects['id']}/records", json={"data": {P["Name"]: "Private job"}})
    user.j("PATCH", f"/tables/{projects['id']}", json={"page": {"enabled": True, "template": "secret", "blocks": [
        {"id": "r", "type": "related", "table": tasks["id"], "field": link["id"]}, {"id": "n", "type": "field", "field": P["Name"]}]}})
    s = user.j("POST", "/shares", json={"name": "Mill St", "base_id": user.base_id, "items": [{"kind": "table", "id": projects["id"], "record": p["id"]}],
                                        "permissions": {"edit": True, "add": True, "delete": True}})
    meta = requests.get(BASE + f"/api/public/{s['token']}").json()
    view = meta["items"][0]["views"][0]["id"]
    assert meta["items"][0]["record"] == p["id"]
    got = requests.get(BASE + f"/api/public/{s['token']}/views/{view}").json()
    assert [r["id"] for r in got["records"]] == [p["id"]] and got["record"] == p["id"]
    assert [x["type"] for x in got["page"]["blocks"]] == ["field"] and "template" not in got["page"]      # other tables stay private
    assert requests.patch(BASE + f"/api/public/{s['token']}/views/{view}/records/{p['id']}", json={"data": {P["Name"]: "Mill Street"}}).status_code == 200
    assert user.record(projects["id"], p["id"])["data"][P["Name"]] == "Mill Street"
    assert requests.patch(BASE + f"/api/public/{s['token']}/views/{view}/records/{other['id']}", json={"data": {P["Name"]: "x"}}).status_code == 404
    assert requests.post(BASE + f"/api/public/{s['token']}/views/{view}/records", json={"data": {}}).status_code == 403
    assert requests.delete(BASE + f"/api/public/{s['token']}/views/{view}/records/{p['id']}").status_code == 403
    # a record of another table can't be shared through this one
    assert user.request("POST", API + "/shares", json={"name": "x", "base_id": user.base_id,
                        "items": [{"kind": "table", "id": projects["id"], "record": a["id"]}]}).status_code == 400


def test_a_combined_databases_records_as_pages(user):
    import requests
    from conftest import BASE
    a, b = user.table(name="Jobs A"), user.table(name="Jobs B")
    A, B = user.fields(a["id"]), user.fields(b["id"])
    c = user.j("POST", "/combos", json={"name": "All jobs", "base_id": user.base_id, "sources": [{"table_id": a["id"]}, {"table_id": b["id"]}]})
    info = user.j("GET", f"/combos/{c['id']}")
    F = {f["name"]: f["id"] for f in info["fields"]}
    out = user.j("PATCH", f"/combos/{c['id']}", json={"page": {"enabled": True, "template": "## Scope", "blocks": [
        {"id": "n", "type": "field", "field": F["Status"], "span": 2}, {"id": "d", "type": "doc", "field": F["Notes"]}]}})
    assert out["combo"]["page"]["enabled"] and user.j("GET", f"/combos/{c['id']}")["combo"]["page"]["blocks"][0]["span"] == 2
    assert user.request("PATCH", API + f"/combos/{c['id']}", json={"page": {"blocks": [{"id": "x", "type": "field", "field": "nope"}]}}).status_code == 400
    # a record added through it starts with the template (in its source table)
    r = user.j("POST", f"/combos/{c['id']}/records", json={"table_id": b["id"], "data": {F["Name"]: "From the combo"}})
    assert next(x for x in user.records(b["id"]) if x["id"] == r["id"])["data"][B["Notes"]] == "## Scope"
    # a link to one of its pages
    s = user.j("POST", "/shares", json={"name": "One", "base_id": user.base_id, "items": [{"kind": "combo", "id": c["id"], "record": r["id"]}]})
    meta = requests.get(BASE + f"/api/public/{s['token']}").json()
    got = requests.get(BASE + f"/api/public/{s['token']}/views/{meta['items'][0]['views'][0]['id']}").json()
    assert [x["id"] for x in got["records"]] == [r["id"]] and got["page"]["enabled"] and "template" not in got["page"]
    other = user.j("POST", f"/tables/{a['id']}/records", json={"data": {A["Name"]: "x"}})
    assert user.request("POST", API + "/shares", json={"name": "x", "base_id": user.base_id,
                        "items": [{"kind": "combo", "id": c["id"], "record": user.j("POST", f"/tables/{user.table(name='Elsewhere')['id']}/records", json={"data": {}})["id"]}]}).status_code == 400
    assert other["id"]


def test_table_templates_carry_the_page(user):
    tasks, projects, link, notes, est, a, b, p = projects_and_tasks(user)
    P = user.fields(projects["id"])
    budget = user.j("POST", f"/tables/{projects['id']}/fields", json={"name": "Budget", "type": "number", "options": {}})
    when = {"conjunction": "and", "items": [{"field": P["Status"], "op": "is", "value": "Done"}]}
    user.j("PATCH", f"/tables/{projects['id']}", json={"page": {"enabled": True, "header": [P["Status"]], "template": "## Scope\n\n{{field:" + budget["id"] + "}}",
        "variants": {"field": P["Status"], "layouts": {"Done": [{"id": "v", "type": "field", "field": budget["id"]}]}},
        "blocks": [{"id": "s", "type": "stats", "items": [{"field": budget["id"], "view": "bar"}]},
                   {"id": "w", "type": "heading", "text": "Done", "when": when},
                   {"id": "r", "type": "related", "table": tasks["id"], "field": link["id"]},
                   {"id": "d", "type": "doc", "field": notes["id"]}]}})
    tpl = user.post(API + "/templates/export", json={"kind": "table", "table_id": projects["id"]}).json()
    assert tpl["page"]["blocks"][0]["items"][0]["field"] == "{{field:Budget}}"                  # by name
    assert not any(b["type"] == "related" for b in tpl["page"]["blocks"])                         # (other tables aren't in templates)
    assert "{{field:Budget}}" in tpl["page"]["template"]
    t2 = user.j("POST", "/tables/from-template", json={"body": tpl, "base_id": user.base_id, "name": "Projects 2"})
    N = user.fields(t2["id"])
    page = user.j("GET", f"/tables/{t2['id']}")["table"]["page"]
    assert page["enabled"] and page["header"] == [N["Status"]]
    assert page["blocks"][0]["items"][0]["field"] == N["Budget"] and page["blocks"][1]["when"]["items"][0]["field"] == N["Status"]
    assert page["blocks"][2] == {"id": "d", "type": "doc", "field": N["Brief"]}
    assert page["template"] == "## Scope\n\n{{field:" + N["Budget"] + "}}" and page["variants"]["layouts"]["Done"][0]["field"] == N["Budget"]


def test_the_api_reads_pages_with_their_conditions(user):
    import requests
    from conftest import BASE
    V1 = BASE + "/api/v1"
    tasks, projects, link, notes, est, a, b, p = projects_and_tasks(user)
    P = user.fields(projects["id"])
    back = user.j("POST", f"/tables/{tasks['id']}/fields", json={"name": "Project", "type": "link", "options": {"foreignTableId": projects["id"], "symmetric": False}})
    user.j("PATCH", f"/records/{a['id']}", json={"data": {back["id"]: [p["id"]]}})
    budget = user.j("POST", f"/tables/{projects['id']}/fields", json={"name": "Budget", "type": "number", "options": {"views": {"record": "bar"}}})
    user.j("PATCH", f"/records/{p['id']}", json={"data": {budget["id"]: 40, notes["id"]: "Intro\n\n{{field:" + budget["id"] + "}}"}})
    done = {"conjunction": "and", "items": [{"field": P["Status"], "op": "is", "value": "Done"}]}
    user.j("PATCH", f"/tables/{projects['id']}", json={"page": {"enabled": True, "header": [budget["id"]], "outline": True,
        "variants": {"field": P["Status"], "layouts": {"Done": [{"id": "x", "type": "heading", "text": "Finished"}]}},
        "blocks": [{"id": "f", "type": "field", "field": budget["id"]},
                   {"id": "g", "type": "field", "field": link["id"], "view": "cards"},
                   {"id": "s", "type": "section", "text": "Later", "when": done, "blocks": [{"id": "in", "type": "field", "field": P["Status"]}]},
                   {"id": "r", "type": "related", "table": tasks["id"], "field": back["id"], "text": "Tasks"},
                   {"id": "d", "type": "doc", "field": notes["id"]}]}})
    W = {"Authorization": "Bearer " + user.j("POST", "/keys", json={"name": "pages", "scope": "read"})["key"]}
    layout = requests.get(V1 + f"/tables/{projects['id']}/page", headers=W).json()
    assert layout["enabled"] and layout["laid_out"] and layout["header"][0]["name"] == "Budget" and layout["outline"]
    blocks = {x["id"]: x for x in layout["layout"]}
    assert blocks["f"]["view"] == "bar" and blocks["f"]["view_from"] == "field"        # the field's own way
    assert blocks["g"]["view"] == "cards" and blocks["g"]["view_from"] == "block"      # the block's
    assert blocks["s"]["condition"]["items"][0]["field"]["name"] == "Status" and blocks["s"]["blocks"][0]["field"]["name"] == "Status"
    assert blocks["r"]["related"]["table"]["name"] == "Tasks" and blocks["r"]["related"]["link_field"]["name"] == "Project"
    assert layout["layouts_by"]["field"]["name"] == "Status" and "Done" in layout["layouts_by"]["layouts"]
    pg = requests.get(V1 + f"/tables/{projects['id']}/records/{p['id']}/page", headers=W).json()
    assert pg["record"]["title"] == "Mill St" and pg["layout_used"]["layout"] == "default" and pg["layout_used"]["by"]["name"] == "Status"
    got = {x["id"]: x for x in pg["blocks"]}
    assert got["f"]["value"] == 40 and got["f"]["view"] == "bar"
    cond = got["s"]["condition"]
    assert got["s"]["shown"] is False and cond["met"] is False and cond["items"][0]["met"] is False and cond["items"][0]["actual"] is None
    assert got["s"]["blocks"][0]["shown"] is False                                       # inside a hidden section
    assert got["r"]["related"]["total"] == 1 and got["r"]["related"]["records"][0]["title"] == "Wire panel"
    assert [x["name"] for x in got["d"]["fields_in_text"]] == ["Budget"] and pg["header"][0]["value"] == 40
    # a finished project: its condition is met, and finished projects have a layout of their own
    user.j("PATCH", f"/records/{p['id']}", json={"data": {P["Status"]: "Done"}})
    pg = requests.get(V1 + f"/tables/{projects['id']}/records/{p['id']}/page", headers=W).json()
    assert pg["layout_used"]["layout"] == "Done" and [x["id"] for x in pg["blocks"]] == ["x"]
    assert requests.get(V1 + f"/tables/{projects['id']}/records/{tasks['id']}/page", headers=W).status_code == 404


def test_assistants_read_pages_over_mcp(user):
    from test_mcp_full import call, key
    tasks, projects, link, notes, est, a, b, p = projects_and_tasks(user)
    P = user.fields(projects["id"])
    user.j("PATCH", f"/tables/{projects['id']}", json={"page": {"enabled": True, "blocks": [
        {"id": "h", "type": "heading", "text": "Done!", "when": {"conjunction": "and", "items": [{"field": P["Status"], "op": "is", "value": "Done"}]}}]}})
    R = key(user, "read")
    err, layout = call(R, "describe_page", table="Projects")
    assert not err and layout["layout"][0]["condition"]["items"][0]["field"]["name"] == "Status"
    err, pg = call(R, "get_page", table="Projects", record_id=p["id"])
    assert not err and pg["blocks"][0]["shown"] is False and pg["blocks"][0]["condition"]["met"] is False
