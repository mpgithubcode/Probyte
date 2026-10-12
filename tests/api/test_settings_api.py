"""Table and view settings: view order, duplicate, changing a view's kind, the table's field order, descriptions."""
from conftest import API


def test_views_order_duplicate_and_kind(user, other_user):
    t = user.table(name="Settings")
    F = user.fields(t["id"])
    first = user.j("GET", f"/tables/{t['id']}")["views"][0]
    b = user.j("POST", f"/tables/{t['id']}/views", json={"name": "B", "type": "grid", "config": {"hidden": [F["Notes"]]}})
    c = user.j("POST", f"/tables/{t['id']}/views", json={"name": "C", "type": "grid"})
    user.j("POST", "/views/order", json={"ids": [c["id"], first["id"], b["id"]]})
    assert [v["name"] for v in user.j("GET", f"/tables/{t['id']}")["views"]] == ["C", "Table", "B"]
    assert other_user.request("POST", API + "/views/order", json={"ids": [c["id"]]}).status_code == 404
    copy = user.j("POST", f"/views/{b['id']}/duplicate")
    assert copy["name"] == "B copy" and copy["config"]["hidden"] == [F["Notes"]]
    assert [v["name"] for v in user.j("GET", f"/tables/{t['id']}")["views"]][-2:] == ["B", "B copy"]
    board = user.j("PATCH", f"/views/{copy['id']}", json={"type": "kanban"})
    assert board["type"] == "kanban" and board["config"]["groupBy"] == F["Status"] and board["config"]["hidden"] == [F["Notes"]]


def test_field_order_and_description(user):
    t = user.table(name="Order")
    F = user.fields(t["id"])
    user.j("POST", f"/tables/{t['id']}/fields/order", json={"ids": [F["Status"], F["Name"], F["Notes"]]})
    names = [f["name"] for f in user.j("GET", f"/tables/{t['id']}")["fields"]]
    assert names[:3] == ["Status", "Name", "Notes"] and names[-1] == "Record ID"
    user.j("PATCH", f"/tables/{t['id']}", json={"description": "  What we sell  "})
    assert user.j("GET", f"/tables/{t['id']}")["table"]["description"] == "What we sell"
    user.j("PATCH", f"/tables/{t['id']}", json={"description": ""})
    assert user.j("GET", f"/tables/{t['id']}")["table"]["description"] is None
