"""A link field can offer only some records in its picker: a view of the linked table, and / or conditions."""
from conftest import API


def test_the_picker_offers_the_records_the_link_says(user):
    agencies, people = user.table(name="Agencies"), user.table(name="People")
    A = user.fields(agencies["id"])
    for n, s in (("Worcester HA", "Doing"), ("Town of Upton", "Todo"), ("Mashpee HA", "Doing"), ("Closed HA", "Done")):
        user.j("POST", f"/tables/{agencies['id']}/records", json={"data": {A["Name"]: n, A["Status"]: s}})
    active = user.j("POST", f"/tables/{agencies['id']}/views", json={"name": "Active", "type": "grid", "config": {
        "filter": {"conjunction": "and", "items": [{"field": A["Status"], "op": "is_not", "value": "done"}]},
        "sorts": [{"field": A["Name"], "dir": "asc"}]}})
    link = user.j("POST", f"/tables/{people['id']}/fields", json={"name": "Agency", "type": "link", "options": {
        "foreignTableId": agencies["id"], "pickView": active["id"]}})
    titles = lambda: [x["title"] for x in user.j("GET", f"/tables/{agencies['id']}/titles", params={"field": link["id"]})]
    assert titles() == ["Mashpee HA", "Town of Upton", "Worcester HA"]          # the view's records, in its order
    # plus conditions of its own
    user.j("PATCH", f"/fields/{link['id']}", json={"options": {"foreignTableId": agencies["id"], "pickView": active["id"],
        "pickFilter": {"conjunction": "and", "items": [{"field": A["Name"], "op": "contains", "value": "ha"}]}}})
    assert titles() == ["Mashpee HA", "Worcester HA"]
    # without the field: every record
    assert len(user.j("GET", f"/tables/{agencies['id']}/titles")) == 4
    # a view or a field of another table is refused
    P = user.fields(people["id"])
    other_view = user.j("GET", f"/tables/{people['id']}")["views"][0]["id"]
    assert user.patch(API + f"/fields/{link['id']}", json={"options": {"foreignTableId": agencies["id"], "pickView": other_view}}).status_code == 400
    assert user.patch(API + f"/fields/{link['id']}", json={"options": {"foreignTableId": agencies["id"],
        "pickFilter": {"conjunction": "and", "items": [{"field": P["Name"], "op": "is", "value": "x"}]}}}).status_code == 400
