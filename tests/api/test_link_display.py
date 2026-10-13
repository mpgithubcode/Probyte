"""A link field can show another field of its records than their title: a formula, a lookup, their files."""
from conftest import API


def test_a_link_shows_a_formula_or_files_of_its_records(user):
    clients, jobs = user.table(name="Clients"), user.table(name="Jobs")
    C = user.fields(clients["id"])
    code = user.j("POST", f"/tables/{clients['id']}/fields", json={"name": "Code", "type": "formula",
                                                                   "options": {"expression": 'UPPER({Name}) & "-1"'}})
    files = user.j("POST", f"/tables/{clients['id']}/fields", json={"name": "Logo", "type": "attachment"})
    up = user.post(API + "/attachments", files={"file": ("logo.png", b"\x89PNG\r\n\x1a\n", "image/png")}).json()
    acme = user.j("POST", f"/tables/{clients['id']}/records", json={"data": {C["Name"]: "acme", files["id"]: [up]}})
    link = user.j("POST", f"/tables/{jobs['id']}/fields", json={"name": "Client", "type": "link",
                                                                "options": {"foreignTableId": clients["id"], "displayFieldId": code["id"]}})
    assert link["options"]["displayFieldId"] == code["id"] and link["options"]["displayFiles"] is True
    J = user.fields(jobs["id"])
    user.j("POST", f"/tables/{jobs['id']}/records", json={"data": {J["Name"]: "Panel", link["id"]: [acme["id"]]}})
    got = user.j("GET", f"/tables/{jobs['id']}")
    shown = got["refs"]["display"][link["id"]]
    assert shown["field"]["name"] == "Code" and shown["values"][acme["id"]] == "ACME-1"
    assert got["refs"]["records"][acme["id"]] == "acme"                    # the title is still there (search, pickers)
    # its files: shown as previews (or by name, if turned off)
    user.j("PATCH", f"/fields/{link['id']}", json={"options": {**link["options"], "displayFieldId": files["id"], "displayFiles": False}})
    got = user.j("GET", f"/tables/{jobs['id']}")
    assert got["refs"]["display"][link["id"]]["values"][acme["id"]][0]["name"] == "logo.png"
    assert next(f for f in got["fields"] if f["id"] == link["id"])["options"]["displayFiles"] is False
    # a field of another table can't be chosen
    bad = user.patch(API + f"/fields/{link['id']}", json={"options": {**link["options"], "displayFieldId": J["Notes"]}})
    assert bad.status_code == 400
    # and in a combined database, under its field
    combo = user.j("POST", "/combos", json={"name": "All", "base_id": user.base_id, "sources": [{"table_id": jobs["id"]}]})
    cid = combo.get("id") or combo["combo"]["id"]
    data = user.j("GET", f"/combos/{cid}")
    cf = next(f for f in data["fields"] if f["mapping"].get(jobs["id"]) == link["id"])
    assert data["refs"]["display"][cf["id"]]["values"][acme["id"]][0]["name"] == "logo.png"
