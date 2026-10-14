"""A whole base shared by a public link, added by someone else as a base of linked tables that keeps in step."""
from conftest import API, BASE, Client


def test_a_whole_base_shared_by_link_and_added_as_a_base(user, other_user):
    jobs = user.table(name="Jobs")
    J = user.fields(jobs["id"])
    user.j("POST", f"/tables/{jobs['id']}/records", json={"data": {J["Name"]: "Mill St"}})
    user.table(name="Clients")
    s = user.j("POST", "/shares", json={"name": "Our base", "base_id": user.base_id, "whole_base": True, "permissions": {"add": True, "edit": True}})
    assert s["whole_base"] and s["items"] == []
    url = f"{BASE}/s/{s['token']}"
    b = other_user.j("POST", "/bases/from-link", json={"url": url})
    names = sorted(t["name"] for t in b["tables"])
    assert names == ["Clients", "Jobs"] and b["linked"]["follow_views"] is True
    ws = other_user.j("GET", "/workspace")
    mine = next(x for x in ws["bases"] if x["id"] == b["id"])
    assert mine["role"] == "owner" and mine["linked"]["url"] == url and "cookie" not in mine["linked"]
    lj = next(t for t in ws["tables"] if t["base_id"] == b["id"] and t["name"] == "Jobs")
    data = other_user.j("GET", f"/tables/{lj['id']}")
    LJ = {f["name"]: f["id"] for f in data["fields"]}
    assert [r["data"][LJ["Name"]] for r in data["records"]] == ["Mill St"]
    # it writes through the link, as the link allows
    other_user.j("POST", f"/tables/{lj['id']}/records", json={"data": {LJ["Name"]: "Oak Ave"}})
    assert sorted(r["data"][J["Name"]] for r in user.records(jobs["id"])) == ["Mill St", "Oak Ave"]
    # a table added at the source: there after the next check
    user.table(name="Invoices")
    r = other_user.j("POST", f"/bases/{b['id']}/link/sync")
    assert r["added"] == ["Invoices"] and r["linked"]["tables"] == 3
    assert other_user.j("POST", f"/bases/{b['id']}/link/sync")["added"] == []     # (once)
    # the base's view setting reaches all its tables
    other_user.j("PATCH", f"/bases/{b['id']}/link", json={"follow_views": False})
    for t in [t for t in other_user.j("GET", "/workspace")["tables"] if t["base_id"] == b["id"]]:
        assert other_user.j("GET", f"/tables/{t['id']}")["table"]["remote"]["follow_views"] is False
    # turned off at the source: the base says so
    user.j("PATCH", f"/shares/{s['id']}", json={"name": "Our base", "base_id": user.base_id, "whole_base": True, "enabled": False})
    assert other_user.j("POST", f"/bases/{b['id']}/link/sync")["error"]


def test_a_password_link_and_personal_views_stay_private(user, other_user):
    t = user.table(name="Private-ish")
    member = Client()
    user.j("POST", f"/bases/{user.base_id}/members", json={"username": member.username, "role": "viewer"})
    member.j("POST", f"/tables/{t['id']}/views", json={"name": "Member's own", "type": "grid"})   # a viewer's views are their own
    s = user.j("POST", "/shares", json={"name": "Locked", "base_id": user.base_id, "whole_base": True, "password": "s3cret-pass"})
    url = f"{BASE}/s/{s['token']}"
    assert other_user.post(API + "/bases/from-link", json={"url": url}).status_code == 401
    b = other_user.j("POST", "/bases/from-link", json={"url": url, "password": "s3cret-pass", "name": "Theirs"})
    assert b["name"] == "Theirs" and [x["name"] for x in b["tables"]] == ["Private-ish"]
    meta = other_user.j("POST", "/remote/inspect", json={"url": url, "password": "s3cret-pass"})
    views = [v["name"] for i in meta["items"] for v in i["views"]]
    assert "Member's own" not in views


def test_a_linked_base_checks_its_link_over_the_network(user, other_user):
    """As when the base comes from another server: the link is read at its public address."""
    import os
    import psycopg
    user.table(name="Far away")
    s = user.j("POST", "/shares", json={"name": "Remote", "base_id": user.base_id, "whole_base": True})
    b = other_user.j("POST", "/bases/from-link", json={"url": f"{BASE}/s/{s['token']}"})
    try:
        conn = psycopg.connect(os.environ.get("BENCH_DB", "postgresql://probyte:probyte-test@db:5432/probyte"))
    except Exception:
        return
    with conn:
        conn.execute("UPDATE bases SET linked = linked || '{\"local\": false}' WHERE id = %s", (b["id"],))
    user.table(name="Added later")
    r = other_user.j("POST", f"/bases/{b['id']}/link/sync")
    assert r["error"] is None and r["added"] == ["Added later"]


def test_a_linked_base_checks_its_link_by_itself(user, other_user):
    import os
    import time
    import psycopg
    user.table(name="First")
    s = user.j("POST", "/shares", json={"name": "Auto", "base_id": user.base_id, "whole_base": True})
    b = other_user.j("POST", "/bases/from-link", json={"url": f"{BASE}/s/{s['token']}"})
    user.table(name="Second")
    try:
        conn = psycopg.connect(os.environ.get("BENCH_DB", "postgresql://probyte:probyte-test@db:5432/probyte"))
    except Exception:
        return
    with conn:   # as if the last check were long ago
        conn.execute("UPDATE bases SET linked = linked || '{\"checked_at\": 0}' WHERE id = %s", (b["id"],))
    other_user.j("GET", "/workspace")            # opening Probyte queues a check
    for _ in range(40):
        if any(t["name"] == "Second" and t["base_id"] == b["id"] for t in other_user.j("GET", "/workspace")["tables"]):
            break
        time.sleep(0.25)
    assert any(t["name"] == "Second" and t["base_id"] == b["id"] for t in other_user.j("GET", "/workspace")["tables"])
