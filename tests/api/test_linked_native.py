"""A linked table (a public link added as a table) behaves like the kind of table it links to."""
import os
import uuid

import requests

from conftest import API, BASE, S3_URL

S3 = {"kind": "s3", "endpoint": S3_URL, "bucket": "test-bucket", "username": "testkey", "secret": "testsecret12345"}
ROOT = API.replace("/api", "")


def share(user, t, **perms):
    view = user.j("GET", f"/tables/{t['id']}")["views"][0]
    s = user.j("POST", "/shares", json={"name": "Link", "base_id": user.base_id, "permissions": perms,
                                       "items": [{"kind": "table", "id": t["id"], "views": [view["id"]]}]})
    return s, view


def link(viewer, s, view, name="Linked"):
    return viewer.j("POST", "/tables", json={"name": name, "kind": "remote", "base_id": viewer.base_id,
                                             "remote": {"url": f"{BASE}/s/{s['token']}", "view_id": view["id"]}})


def over_the_network(table_id):
    """Make a linked table use its link's public address (as if it were on another server)."""
    import psycopg
    try:
        conn = psycopg.connect(os.environ.get("BENCH_DB", "postgresql://probyte:probyte-test@db:5432/probyte"))
    except Exception:
        return False
    with conn:
        conn.execute("UPDATE tables SET remote = remote || '{\"local\": false}' WHERE id = %s", (table_id,))
    return True


def test_linked_files_table(user, other_user):
    t = user.table("files", "Drive", storage=S3, storage_prefix=f"p{uuid.uuid4().hex[:8]}")
    user.post(API + f"/tables/{t['id']}/files", files=[("files", ("a.txt", b"first", "text/plain"))])
    s, view = share(user, t, add=True, edit=True, delete=True)
    lt = link(other_user, s, view)
    for network in (False, True):
        if network and not over_the_network(lt["id"]):
            return
        info = other_user.j("GET", f"/tables/{lt['id']}")
        assert info["table"]["remote"]["source_kind"] == "files"
        keys = {f.get("system_key") for f in info["fields"]}
        assert {"name", "file", "folder", "kind"} <= keys                     # the file's own fields keep their roles
        name = f"up-{int(network)}.txt"
        # upload through the link: lands in the source table
        r = other_user.post(API + f"/remote/{lt['id']}/files", files=[("files", (name, b"hello", "text/plain"))])
        assert r.status_code == 200, r.text
        F = user.fields(t["id"])
        src = next(x for x in user.records(t["id"]) if x["data"][F["name"]] == name)
        # edit its text and replace it, through the link
        fid = next(f["id"] for f in other_user.j("GET", f"/tables/{lt['id']}")["fields"] if f.get("system_key") == "file")
        item = next(x["data"][fid][0] for x in other_user.records(lt["id"]) if x["data"].get(fid) and x["data"][fid][0]["name"] == name)
        assert other_user.request("PUT", ROOT + item["url"] + "/text", json={"text": "edited"}).status_code == 200
        assert user.get(API + f"/files/{src['id']}/content").content == b"edited"
        r = other_user.request("PUT", ROOT + item["url"] + "/file", files={"file": ("x.txt", b"replaced", "text/plain")})
        assert r.status_code == 200 and user.get(API + f"/files/{src['id']}/content").content == b"replaced"


def test_linked_files_table_respects_the_links_rights(user, other_user):
    t = user.table("files", "Read only", storage=S3, storage_prefix=f"p{uuid.uuid4().hex[:8]}")
    rec = user.post(API + f"/tables/{t['id']}/files", files=[("files", ("a.txt", b"x", "text/plain"))]).json()["records"][0]
    s, view = share(user, t)                                                     # view only
    lt = link(other_user, s, view)
    r = other_user.post(API + f"/remote/{lt['id']}/files", files=[("files", ("no.txt", b"x", "text/plain"))])
    assert r.status_code == 403
    assert other_user.request("PUT", API + f"/remote/{lt['id']}/files/{rec['id']}/text", json={"text": "no"}).status_code == 403
    # and directly on the public link
    assert requests.post(API + f"/public/{s['token']}/views/{view['id']}/files", files=[("files", ("no.txt", b"x", "text/plain"))]).status_code == 403
    assert user.get(API + f"/files/{rec['id']}/content").content == b"x"


def test_linked_contacts_table(user, other_user):
    t = user.table("contacts", "People")
    s, view = share(user, t, add=True)
    lt = link(other_user, s, view, "Their people")
    vcf = b"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Grace Hopper\r\nEMAIL:grace@example.com\r\nEND:VCARD\r\n"
    for network in (False, True):
        if network and not over_the_network(lt["id"]):
            return
        email = f"g{int(network)}@example.com"
        r = other_user.post(API + f"/remote/{lt['id']}/vcards", files=[("files", ("a.vcf", vcf.replace(b"grace@example.com", email.encode()), "text/vcard"))])
        assert r.status_code == 200 and r.json()["created"] == 1, r.text
        F = user.fields(t["id"])
        assert any(x["data"].get(F["email"]) == email for x in user.records(t["id"]))
        out = other_user.get(API + f"/remote/{lt['id']}/vcards")
        assert out.status_code == 200 and email in out.text
    info = other_user.j("GET", f"/tables/{lt['id']}")
    assert info["table"]["remote"]["source_kind"] == "contacts" and any(f.get("system_key") == "fn" for f in info["fields"])


def test_linked_tasks_table_keeps_field_roles_and_formats(user, other_user):
    t = user.table("mixed", "Agenda")
    F = user.fields(t["id"])
    price = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Price", "type": "number",
                   "options": {"numberFormat": "currency", "currency": "€", "precision": 2}, "description": "What it costs"})
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Call", F["component"]: "Task", price["id"]: 12}})
    s, view = share(user, t, edit=True)
    lt = link(other_user, s, view)
    info = other_user.j("GET", f"/tables/{lt['id']}")
    by_key = {f.get("system_key"): f for f in info["fields"]}
    assert info["table"]["remote"]["source_kind"] == "mixed"
    assert {"summary", "component", "status", "done", "dtstart"} <= set(by_key)   # the app knows tasks from events
    p = next(f for f in info["fields"] if f["name"] == "Price")
    assert p["options"]["numberFormat"] == "currency" and p["options"]["currency"] == "€" and p["description"] == "What it costs"
    # ticking Completed through the link: the source's task rules apply
    rid = info["records"][0]["id"]
    other_user.j("PATCH", f"/tables/{lt['id']}/records/{rid}", json={"data": {by_key["done"]["id"]: True}})
    assert user.records(t["id"])[0]["data"][F["status"]] == "Completed"


def test_linked_calendar_tables_line_up_and_keep_record_ids(user, other_user):
    """A linked calendar's fields line up with your own calendars in a combined database (by their role, as your own
    tables' do), and its records show the Record IDs they have in their source."""
    acct = lambda u: next(a for a in u.workspace()["accounts"] if a["is_builtin"])["id"]
    src = user.table("events", "Site visits")
    F = user.fields(src["id"])
    rec = user.j("POST", f"/tables/{src['id']}/records", json={"data": {F["summary"]: "Walkthrough", F["dtstart"]: "2026-10-20T09:00:00Z"}})
    s, view = share(user, src, edit=True)
    lt = link(other_user, s, view, "Their visits")
    mine = other_user.table("events", "My visits")
    M = other_user.fields(mine["id"])
    other_user.j("POST", f"/tables/{mine['id']}/records", json={"data": {M["summary"]: "Inspection", M["dtstart"]: "2026-10-21T13:00:00Z"}})
    # the linked table: its Record ID is the source's
    info = other_user.j("GET", f"/tables/{lt['id']}")
    rid_f = next(f for f in info["fields"] if f["type"] == "record_id")
    assert info["records"][0]["data"][rid_f["id"]] == rec["id"]
    # combined with my own calendar: one Title, one Start, one End… (not one per table)
    c = other_user.j("POST", "/combos", json={"name": "All visits", "base_id": other_user.base_id,
                                               "sources": [{"table_id": mine["id"]}, {"table_id": lt["id"]}]})
    combo = other_user.j("GET", f"/combos/{c['id']}")
    names = [f["name"] for f in combo["fields"]]
    for n in ("Title", "Start", "End", "Status", "Location"):
        assert sum(1 for x in names if x.split(" / ")[0] == n) == 1, (n, names)
    start = next(f for f in combo["fields"] if f["name"].startswith("Start"))
    assert len(start["mapping"]) == 2
    title = next(f["id"] for f in combo["fields"] if f["name"].startswith("Title"))
    got = {r["data"].get(title): r["data"]["__id"] for r in combo["records"]}
    assert got["Walkthrough"] == rec["id"]


def test_a_linked_tables_view_stays_in_step_with_the_source_unless_turned_off(user, other_user):
    t = user.table(name="Source view")
    s, view = share(user, t)
    lt = link(other_user, s, view)
    mirror = lambda: other_user.j("GET", f"/tables/{lt['id']}")["views"][0]  # noqa: E731
    assert other_user.j("GET", f"/tables/{lt['id']}")["table"]["remote"]["follow_views"] is True
    user.j("PATCH", f"/views/{view['id']}", json={"config": {"rowHeight": "tall"}})
    other_user.post(API + f"/tables/{lt['id']}/refresh")
    assert mirror()["config"].get("rowHeight") == "tall"
    other_user.j("PATCH", f"/tables/{lt['id']}/link", json={"follow_views": False})
    other_user.j("PATCH", f"/views/{mirror()['id']}", json={"config": {"rowHeight": "short"}})
    user.j("PATCH", f"/views/{view['id']}", json={"config": {"rowHeight": "medium"}})
    other_user.post(API + f"/tables/{lt['id']}/refresh")
    assert mirror()["config"].get("rowHeight") == "short"      # kept as the linked table's owner set it


def test_a_following_linked_tables_own_view_changes_last_until_the_source_changes(user, other_user):
    """Stay updated: what the linked table's owner changes in its view (hides a field, sorts) isn't put back on the next
    load; the next change to the source's view is followed again."""
    t = user.table(name="Followed")
    F = user.fields(t["id"])
    s, view = share(user, t)
    lt = link(other_user, s, view)
    mirror = lambda: next(v for v in other_user.j("GET", f"/tables/{lt['id']}")["views"] if v["id"] == mid)  # noqa: E731
    mid = other_user.j("GET", f"/tables/{lt['id']}")["views"][0]["id"]
    L = {f["name"]: f["id"] for f in other_user.j("GET", f"/tables/{lt['id']}")["fields"]}
    other_user.j("PATCH", f"/views/{mid}", json={"config": {**mirror()["config"], "hidden": [L["Notes"]], "sorts": [{"field": L["Name"], "dir": "desc"}]}})
    for _ in range(2):                                   # reloads (and a refresh from the source) keep it
        other_user.post(API + f"/tables/{lt['id']}/refresh")
        cfg = mirror()["config"]
        assert cfg.get("hidden") == [L["Notes"]] and cfg.get("sorts") == [{"field": L["Name"], "dir": "desc"}]
    # the source's view changes: followed (the owner's change replaces the local one)
    user.j("PATCH", f"/views/{view['id']}", json={"config": {"hidden": [F["Status"]]}})
    other_user.post(API + f"/tables/{lt['id']}/refresh")
    assert mirror()["config"].get("hidden") == [L["Status"]] and not mirror()["config"].get("sorts")


def test_a_linked_table_when_its_link_changes(user, other_user):
    """Turned off, a new address, a password, expired: the linked table says so and shows nothing; fixed: back."""
    t = user.table(name="Lifecycle")
    F = user.fields(t["id"])
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Row"}})
    s, view = share(user, t)
    lt = link(other_user, s, view)
    body = {"name": s["name"], "base_id": user.base_id, "items": [{"kind": "table", "id": t["id"], "views": [view["id"]]}]}

    def state():
        other_user.post(API + f"/tables/{lt['id']}/refresh")
        d = other_user.j("GET", f"/tables/{lt['id']}")
        return d["table"]["remote"]["error"], len(d["records"])
    assert state() == (None, 1)
    user.j("PATCH", f"/shares/{s['id']}", json={**body, "enabled": False})       # turned off
    err, n = state()
    assert err and n == 0
    user.j("PATCH", f"/shares/{s['id']}", json={**body, "enabled": True})
    assert state() == (None, 1)
    new = user.j("POST", f"/shares/{s['id']}/regenerate")                       # a new address: the old one stops
    err, n = state()
    assert err and n == 0
    other_user.j("POST", f"/tables/{lt['id']}/relink", json={"url": f"{BASE}/s/{new['token']}"})
    assert state() == (None, 1)
    user.j("PATCH", f"/shares/{s['id']}", json={**body, "password": "a-new-pass"})   # a password now
    err, n = state()
    assert "password" in err and n == 0
    other_user.j("POST", f"/tables/{lt['id']}/relink", json={"password": "a-new-pass"})
    assert state() == (None, 1)
    user.j("PATCH", f"/shares/{s['id']}", json={**body, "password": None, "expires_at": "2020-01-01T00:00:00Z"})  # expired
    err, n = state()
    assert err and n == 0


def test_one_table_from_a_whole_base_link_and_the_public_page(user, other_user):
    a, b = user.table(name="Alpha"), user.table(name="Bravo")
    s = user.j("POST", "/shares", json={"name": "All", "base_id": user.base_id, "whole_base": True})
    pub = requests.get(f"{API}/public/{s['token']}").json()
    assert sorted(i["name"] for i in pub["items"]) == ["Alpha", "Bravo"] and pub["whole_base"]
    user.table(name="Charlie")                                                  # also ones added later
    assert len(requests.get(f"{API}/public/{s['token']}").json()["items"]) == 3
    # just one of its tables, into an existing base
    bravo_view = user.j("GET", f"/tables/{b['id']}")["views"][0]
    lt = other_user.j("POST", "/tables", json={"name": "Just Bravo", "kind": "remote", "base_id": other_user.base_id,
                                               "remote": {"url": f"{BASE}/s/{s['token']}", "item_id": b["id"], "view_id": bravo_view["id"]}})
    assert other_user.j("GET", f"/tables/{lt['id']}")["table"]["remote"]["error"] is None
    # a trashed table drops out of the link
    user.j("DELETE", f"/tables/{a['id']}")
    assert "Alpha" not in [i["name"] for i in requests.get(f"{API}/public/{s['token']}").json()["items"]]


def _key(u):
    return {"Authorization": "Bearer " + u.j("POST", "/keys", json={"name": "k", "scope": "read"})["key"]}


def test_every_view_of_a_linked_table_comes_along_with_its_records(user, other_user):
    """A link that shares several views of a table: the linked table gets each of them (type, name, layout, the fields
    it shows) and each shows just its own records; views added, renamed or removed at the source follow; an edit to a
    record only one view shows goes through that view."""
    t = user.table(name="Bids")
    F = user.fields(t["id"])
    for n, st in (("Mill St", "Todo"), ("Oak Ave", "Doing"), ("Elm Rd", "Done")):
        user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: n, F["Status"]: st}})
    first = user.j("GET", f"/tables/{t['id']}")["views"][0]
    user.j("PATCH", f"/views/{first['id']}", json={"name": "Open", "config": {
        "filter": {"conjunction": "and", "items": [{"field": F["Status"], "op": "is_not", "value": "Done"}]}}})
    board = user.j("POST", f"/tables/{t['id']}/views", json={"name": "Board", "type": "kanban", "config": {"groupBy": F["Status"]}})
    done = user.j("POST", f"/tables/{t['id']}/views", json={"name": "Done", "type": "grid", "config": {
        "hidden": [F["Notes"]], "filter": {"conjunction": "and", "items": [{"field": F["Status"], "op": "is", "value": "Done"}]}}})
    s = user.j("POST", "/shares", json={"name": "Bids", "base_id": user.base_id, "permissions": {"edit": True},
                                       "items": [{"kind": "table", "id": t["id"], "views": None}]})
    lt = link(other_user, s, first)
    data = other_user.j("GET", f"/tables/{lt['id']}")
    views = {v["name"]: v for v in data["views"]}
    assert {"Open", "Board", "Done"} <= set(views) and views["Board"]["type"] == "kanban"
    L = {f["name"]: f["id"] for f in data["fields"]}
    assert views["Board"]["config"]["groupBy"] == L["Status"] and L["Notes"] in views["Done"]["config"]["hidden"]
    assert len(data["records"]) == 3                                   # every record any view shows
    H = _key(other_user)
    by_view = lambda name: sorted(r["fields"]["Name"] for r in requests.get(  # noqa: E731
        f"{ROOT}/api/v1/tables/{lt['id']}/records", headers=H, params={"view": name}).json()["records"])
    assert by_view("Open") == ["Mill St", "Oak Ave"] and by_view("Done") == ["Elm Rd"] and len(by_view("Board")) == 3
    # an edit to a record only "Done" shows: through that view
    elm = next(r for r in data["records"] if r["data"][L["Name"]] == "Elm Rd")
    other_user.j("PATCH", f"/tables/{lt['id']}/records/{elm['id']}", json={"data": {L["Name"]: "Elm Road"}})
    assert "Elm Road" in [r["data"][F["Name"]] for r in user.records(t["id"])]
    # at the source: a view added, one renamed, one removed
    user.j("POST", f"/tables/{t['id']}/views", json={"name": "Calendar", "type": "calendar", "config": {}})
    user.j("PATCH", f"/views/{board['id']}", json={"name": "Pipeline"})
    user.delete(API + f"/views/{done['id']}")
    other_user.post(API + f"/tables/{lt['id']}/refresh")
    names = sorted(v["name"] for v in other_user.j("GET", f"/tables/{lt['id']}")["views"])
    assert "Calendar" in names and "Pipeline" in names and "Board" not in names and "Done" not in names
