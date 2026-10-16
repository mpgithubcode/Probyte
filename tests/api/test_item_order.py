"""A base's tables and combined databases in the order its owner or admins put them (one list), for everyone who
sees the base; new ones come at the end."""
from conftest import API


def _order(u, base_id):
    ws = u.j("GET", "/workspace")
    items = [("table", t["name"], t.get("position")) for t in ws["tables"] if t["base_id"] == base_id] + \
            [("combo", c["name"], c.get("position")) for c in ws["combos"] if c["base_id"] == base_id]
    return [n for _, n, _ in sorted(items, key=lambda x: (x[2] is None, x[2] if x[2] is not None else 0))]


def test_tables_and_combined_databases_are_reordered(user, other_user):
    a, b, c = (user.table(name=n) for n in ("Alpha", "Bravo", "Charlie"))
    combo = user.j("POST", "/combos", json={"name": "Everything", "base_id": user.base_id, "sources": [{"table_id": a["id"]}, {"table_id": b["id"]}]})
    before = [n for n in _order(user, user.base_id) if n in ("Alpha", "Bravo", "Charlie", "Everything")]
    assert before == ["Alpha", "Bravo", "Charlie", "Everything"]
    user.j("PUT", f"/bases/{user.base_id}/order", json={"items": [
        {"kind": "table", "id": c["id"]}, {"kind": "combo", "id": combo["id"]}, {"kind": "table", "id": a["id"]}]})
    got = [n for n in _order(user, user.base_id) if n in ("Alpha", "Bravo", "Charlie", "Everything")]
    assert got == ["Charlie", "Everything", "Alpha", "Bravo"]           # those left out come after, in their order
    user.table(name="Delta")                                             # a new one: at the end
    assert _order(user, user.base_id)[-1] == "Delta"
    # someone else's base: not theirs to reorder; a viewer of it can't either
    assert other_user.put(API + f"/bases/{user.base_id}/order", json={"items": []}).status_code in (403, 404)
    user.j("POST", f"/bases/{user.base_id}/members", json={"username": other_user.username, "role": "viewer"})
    assert other_user.put(API + f"/bases/{user.base_id}/order", json={"items": []}).status_code == 403
    assert [n for n in _order(other_user, user.base_id) if n in ("Alpha", "Charlie")] == ["Charlie", "Alpha"]   # they see the order
