"""Sharing a base with a link: whoever opens it while signed in joins with the link's role and view setting."""
from conftest import API, Client


def test_joining_a_base_with_an_invite_link(user):
    t = user.table(name="Jobs")
    link = user.j("POST", f"/bases/{user.base_id}/invites", json={"role": "editor", "follow_views": False})
    assert link["role"] == "editor" and link["enabled"] and link["uses"] == 0
    other = Client()
    info = other.j("GET", f"/join/{link['token']}")
    assert info["base_id"] == user.base_id and info["owner"] == user.username and info["already"] is None
    assert other.j("POST", f"/join/{link['token']}")["role"] == "editor"
    assert any(b["id"] == user.base_id and b["role"] == "editor" for b in other.j("GET", "/workspace")["bases"])
    assert other.get(API + f"/tables/{t['id']}").status_code == 200
    members = user.j("GET", f"/bases/{user.base_id}/members")["members"]
    assert [(m["username"], m["role"], m["follow_views"]) for m in members] == [(other.username, "editor", False)]
    assert user.j("GET", f"/bases/{user.base_id}/invites")[0]["uses"] == 1
    # joining again changes nothing; the owner opening it is told it's theirs
    assert other.j("POST", f"/join/{link['token']}")["role"] == "editor"
    assert user.j("GET", f"/join/{link['token']}")["already"] == "owner"
    # an editor can't make or see links; only the owner makes admin links
    assert other.post(API + f"/bases/{user.base_id}/invites", json={"role": "viewer"}).status_code == 403
    user.j("PATCH", f"/bases/{user.base_id}/members/{members[0]['id']}", json={"role": "admin"})
    assert other.post(API + f"/bases/{user.base_id}/invites", json={"role": "admin"}).status_code == 403
    assert other.post(API + f"/bases/{user.base_id}/invites", json={"role": "viewer"}).status_code == 200
    # turned off, or deleted: the link doesn't work
    user.j("PATCH", f"/bases/{user.base_id}/invites/{link['id']}", json={"enabled": False})
    third = Client()
    assert third.get(API + f"/join/{link['token']}").status_code == 404
    assert third.post(API + f"/join/{link['token']}").status_code == 404
    user.j("DELETE", f"/bases/{user.base_id}/invites/{link['id']}")
    assert third.get(API + f"/join/{link['token']}").status_code == 404
    assert third.get(API + f"/tables/{t['id']}").status_code == 404
