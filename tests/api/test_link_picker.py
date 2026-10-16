"""Link fields: the picker lists the linked table's records as a table (title and chosen columns, searched on the
server, only what the field offers), and the chips' look (fields shown in them, a colour, a picture) comes with the
table wherever it's loaded."""
from conftest import API


def _setup(user):
    jobs, crew = user.table(name="Jobs"), user.table(name="Crew")
    J, C = user.fields(jobs["id"]), user.fields(crew["id"])
    role = user.j("POST", f"/tables/{crew['id']}/fields", json={"name": "Role", "type": "single_select",
                                                               "options": {"choices": [{"name": "Electrician", "color": "#f59e0b"}, {"name": "Helper", "color": "#3b82f6"}]}})
    phone = user.j("POST", f"/tables/{crew['id']}/fields", json={"name": "Phone", "type": "phone"})
    for n, r, p in (("Ada", "Electrician", "5551230001"), ("Bob", "Helper", "5551230002"), ("Cy", "Electrician", "5551230003")):
        user.j("POST", f"/tables/{crew['id']}/records", json={"data": {C["Name"]: n, role["id"]: r, phone["id"]: p}})
    link = user.j("POST", f"/tables/{jobs['id']}/fields", json={"name": "Crew", "type": "link", "options": {"foreignTableId": crew["id"]}})
    return jobs, crew, J, C, role, phone, link


def test_the_picker_is_a_table_searched_on_the_server(user):
    jobs, crew, J, C, role, phone, link = _setup(user)
    p = user.j("GET", f"/tables/{crew['id']}/pick", params={"field": link["id"]})
    assert p["total"] == 3 and p["can_add"] and p["primary"]["name"] == "Name"
    assert "Role" in [f["name"] for f in p["fields"]] and "Name" not in [f["name"] for f in p["fields"]]   # the first few, not the title
    # its own columns, and a search over them
    user.j("PATCH", f"/fields/{link['id']}", json={"options": {**link["options"], "pickFields": [role["id"], phone["id"]]}})
    p = user.j("GET", f"/tables/{crew['id']}/pick", params={"field": link["id"], "q": "electrician"})
    assert [f["name"] for f in p["fields"]] == ["Role", "Phone"]
    assert sorted(r["title"] for r in p["records"]) == ["Ada", "Cy"] and p["records"][0]["data"][role["id"]] == "Electrician"
    # only what the field offers
    user.j("PATCH", f"/fields/{link['id']}", json={"options": {**link["options"], "pickFields": [role["id"]],
                                                                "pickFilter": {"conjunction": "and", "items": [{"field": role["id"], "op": "is", "value": "Helper"}]}}})
    assert [r["title"] for r in user.j("GET", f"/tables/{crew['id']}/pick", params={"field": link["id"]})["records"]] == ["Bob"]


def test_chips_look_comes_with_the_table(user):
    jobs, crew, J, C, role, phone, link = _setup(user)
    f = user.j("PATCH", f"/fields/{link['id']}", json={"options": {**link["options"], "chip": {
        "style": "details", "fields": [phone["id"], "nonsense"], "colorField": role["id"], "imageField": phone["id"]}}})
    assert f["options"]["chip"] == {"style": "details", "fields": [phone["id"]], "colorField": role["id"], "imageField": None}
    ada = next(r for r in user.records(crew["id"]) if r["data"][C["Name"]] == "Ada")
    user.j("POST", f"/tables/{jobs['id']}/records", json={"data": {J["Name"]: "Mill St", link["id"]: [ada["id"]]}})
    refs = user.j("GET", f"/tables/{jobs['id']}")["refs"]
    chips = refs["chips"][link["id"]]
    assert {x["name"] for x in chips["fields"]} == {"Phone", "Role"}
    assert chips["values"][ada["id"]][role["id"]] == "Electrician"
    # a style that isn't one is the ordinary chip; nothing set: no chip settings kept
    f = user.j("PATCH", f"/fields/{link['id']}", json={"options": {**link["options"], "chip": {"style": "huge"}}})
    assert "chip" not in f["options"]
