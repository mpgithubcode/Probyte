"""Bases shared with other users of the server: viewers read, editors change records and views, admins change fields,
tables and who it's shared with. A member sees the base as its owner does: its views stay the base's, or become
their own copies."""
import requests

from conftest import API, BASE, Client


def share(owner, member, role="editor", base_id=None):
    return owner.j("POST", f"/bases/{base_id or owner.base_id}/members", json={"username": member.username, "role": role})


def view_of(user, table_id):
    return user.j("GET", f"/tables/{table_id}")["views"][0]


def test_a_shared_base_looks_like_the_owners(user, other_user):
    t = user.table(name="Jobs")
    F = user.fields(t["id"])
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Panel upgrade"}})
    assert other_user.get(API + f"/tables/{t['id']}").status_code == 404           # not shared yet
    share(user, other_user, "viewer")
    ws = other_user.workspace()
    shared = next(b for b in ws["bases"] if b["id"] == user.base_id)
    assert shared["role"] == "viewer" and shared["owner"] == user.username
    assert any(x["id"] == t["id"] for x in ws["tables"])
    got = other_user.j("GET", f"/tables/{t['id']}")
    assert [r["data"][F["Name"]] for r in got["records"]] == ["Panel upgrade"]
    assert got["access"] == {"role": "viewer", "follow_views": True, "owner": user.username}
    assert [v["name"] for v in got["views"]] == [v["name"] for v in user.j("GET", f"/tables/{t['id']}")["views"]]
    members = other_user.j("GET", f"/bases/{user.base_id}/members")
    assert members["owner"]["username"] == user.username and members["members"][0]["role"] == "viewer"


def test_what_each_role_may_do(user, other_user):
    t = user.table(name="Roles")
    F = user.fields(t["id"])
    share(user, other_user, "viewer")
    assert other_user.post(API + f"/tables/{t['id']}/records", json={"data": {F["Name"]: "x"}}).status_code == 403
    share(user, other_user, "editor")
    r = other_user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "By the editor"}})
    other_user.j("PATCH", f"/records/{r['id']}", json={"data": {F["Name"]: "Changed by the editor"}})
    assert other_user.post(API + f"/tables/{t['id']}/fields", json={"name": "Cost", "type": "number"}).status_code == 403
    assert other_user.post(API + "/tables", json={"name": "New", "base_id": user.base_id}).status_code == 403
    assert other_user.post(API + f"/bases/{user.base_id}/members", json={"username": "x", "role": "viewer"}).status_code == 403
    share(user, other_user, "admin")
    other_user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Cost", "type": "number"})
    made = other_user.j("POST", "/tables", json={"name": "Made by the admin", "base_id": user.base_id})
    assert any(x["id"] == made["id"] for x in user.workspace()["tables"])           # in the owner's base, theirs
    assert other_user.delete(API + f"/bases/{user.base_id}").status_code == 403      # only the owner deletes the base
    hist = user.j("GET", f"/records/{r['id']}/history")["entries"]
    assert hist[0]["actor"] == other_user.username


def test_views_follow_the_base_or_are_the_members_own(user, other_user):
    t = user.table(name="Views")
    v = view_of(user, t["id"])
    share(user, other_user, "viewer")
    # a viewer's own sort is theirs, until the view is changed for everyone
    other_user.j("PATCH", f"/views/{v['id']}", json={"config": {"rowHeight": "tall"}})
    assert view_of(other_user, t["id"])["config"].get("rowHeight") == "tall"
    assert view_of(user, t["id"])["config"].get("rowHeight") != "tall"
    user.j("PATCH", f"/views/{v['id']}", json={"config": {"rowHeight": "short"}})
    assert view_of(other_user, t["id"])["config"].get("rowHeight") == "short"
    # an editor following the base changes the view for everyone
    share(user, other_user, "editor")
    other_user.j("PATCH", f"/views/{v['id']}", json={"config": {"rowHeight": "medium"}})
    assert view_of(user, t["id"])["config"].get("rowHeight") == "medium"
    # their own copies: the owner's later changes don't reach them, and theirs don't reach the owner
    other_user.j("PATCH", f"/bases/{user.base_id}/me", json={"follow_views": False})
    user.j("PATCH", f"/views/{v['id']}", json={"config": {"rowHeight": "tall"}})
    assert view_of(other_user, t["id"])["config"].get("rowHeight") == "medium"
    other_user.j("PATCH", f"/views/{v['id']}", json={"config": {"rowHeight": "short"}})
    assert view_of(user, t["id"])["config"].get("rowHeight") == "tall"
    assert other_user.delete(API + f"/views/{v['id']}").status_code == 403
    # back to the base's views
    other_user.j("PATCH", f"/bases/{user.base_id}/me", json={"follow_views": True})
    assert view_of(other_user, t["id"])["config"].get("rowHeight") == "tall"


def test_a_members_own_views_and_sidebar(user, other_user):
    t = user.table(name="Own")
    share(user, other_user, "viewer")
    mine = other_user.j("POST", f"/tables/{t['id']}/views", json={"name": "Just mine", "type": "grid"})
    assert any(v["id"] == mine["id"] for v in other_user.j("GET", f"/tables/{t['id']}")["views"])
    assert all(v["id"] != mine["id"] for v in user.j("GET", f"/tables/{t['id']}")["views"])
    other_user.j("PATCH", f"/tables/{t['id']}", json={"hidden": True})
    assert next(x for x in other_user.workspace()["tables"] if x["id"] == t["id"])["hidden"] is True
    assert not next(x for x in user.workspace()["tables"] if x["id"] == t["id"]).get("hidden")


def test_stopping_sharing_and_leaving(user, other_user):
    t = user.table(name="Leaving")
    share(user, other_user, "editor")
    user.j("DELETE", f"/bases/{user.base_id}/members/{other_user.j('GET', '/auth/me')['id']}")
    assert other_user.get(API + f"/tables/{t['id']}").status_code == 404
    share(user, other_user, "viewer")
    other_user.j("DELETE", f"/bases/{user.base_id}/members/{other_user.j('GET', '/auth/me')['id']}")   # leaves
    assert all(b["id"] != user.base_id for b in other_user.workspace()["bases"])
    assert user.post(API + f"/bases/{user.base_id}/members", json={"username": "nobody-here-xyz", "role": "viewer"}).status_code == 404


def test_shared_calendars_reach_the_members_phone(user, other_user):
    t = user.table("tasks", "Crew tasks")
    share(user, other_user, "viewer")
    login, _ = other_user.dav_auth()
    listing = requests.request("PROPFIND", f"{BASE}/dav/{login}/", auth=other_user.dav_auth(), headers={"Depth": "1"}).text
    assert "Crew tasks" in listing and f"/dav/{login}/{t['id']}/" in listing
    item = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VTODO\r\nUID:crew-1\r\nSUMMARY:Pull wire\r\nEND:VTODO\r\nEND:VCALENDAR\r\n")
    url = f"{BASE}/dav/{login}/{t['id']}/crew-1.ics"
    assert requests.put(url, data=item, auth=other_user.dav_auth()).status_code == 403   # a viewer's phone: read-only
    share(user, other_user, "editor")
    assert requests.put(url, data=item, auth=other_user.dav_auth()).status_code == 201
    F = user.fields(t["id"])
    assert [r["data"][F["summary"]] for r in user.records(t["id"])] == ["Pull wire"]


def test_shared_bases_through_the_api_and_attachments(user, other_user):
    t = user.table(name="Api share")
    F = user.fields(t["id"])
    share(user, other_user, "editor")
    key = other_user.j("POST", "/keys", json={"name": "k", "scope": "write"})["key"]
    h = {"Authorization": f"Bearer {key}"}
    bases = requests.get(f"{BASE}/api/v1/bases", headers=h).json()["bases"]
    assert any(b["id"] == user.base_id and b["role"] == "editor" for b in bases)
    requests.post(f"{BASE}/api/v1/tables/{t['id']}/records", headers=h, json={"fields": {"Name": "Via API"}}).raise_for_status()
    # a file a member attaches can be opened by the owner
    up = other_user.post(API + "/attachments", files={"file": ("plan.txt", b"plan", "text/plain")}).json()
    f = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Files", "type": "attachment"})
    rec = user.records(t["id"])[0]
    other_user.j("PATCH", f"/records/{rec['id']}", json={"data": {f["id"]: [up]}})
    assert user.get(API + f"/attachments/{up['id']}").content == b"plan"
    assert Client().get(API + f"/attachments/{up['id']}").status_code == 404
    assert F
