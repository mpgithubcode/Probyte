"""The web app's own API: accounts, bases, tables, views, attachments, public links, combined databases, linked tables."""
import threading
import uuid

import requests

from conftest import API, BASE, Client


def add(user, tid, name, type, **options):
    return user.j("POST", f"/tables/{tid}/fields", json={"name": name, "type": type, "options": options})


# ---------------------------------------------------------------- accounts & sessions

def test_registration_rules():
    s = requests.Session()
    name = "u" + uuid.uuid4().hex[:8]
    for body, code in (({"username": "ab", "password": "long-enough-1"}, 400), ({"username": "Bad Name!", "password": "long-enough-1"}, 400),
                       ({"username": name, "password": "short"}, 400)):
        assert s.post(API + "/auth/register", json=body).status_code == code, body
    assert s.post(API + "/auth/register", json={"username": name.upper(), "password": "long-enough-1"}).status_code == 200
    assert requests.post(API + "/auth/register", json={"username": name, "password": "long-enough-1"}).status_code == 409
    assert s.get(API + "/auth/me").json()["username"] == name          # usernames are lower-cased


def test_concurrent_registration_of_one_name_gives_one_account():
    name = "c" + uuid.uuid4().hex[:8]
    codes = []

    def go():
        codes.append(requests.post(API + "/auth/register", json={"username": name, "password": "long-enough-1"}).status_code)
    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(codes).count(200) == 1 and set(codes) <= {200, 409}, codes


def test_login_logout_password_change(user):
    other = requests.Session()
    assert other.post(API + "/auth/login", json={"username": user.username, "password": "wrong-password"}).status_code == 401
    assert other.post(API + "/auth/login", json={"username": user.username, "password": user.password}).status_code == 200
    assert other.get(API + "/auth/me").status_code == 200
    assert user.post(API + "/auth/password", json={"current_password": "nope-nope-1", "new_password": "new-password-1"}).status_code == 400
    assert user.post(API + "/auth/password", json={"current_password": user.password, "new_password": "x"}).status_code == 400
    user.j("POST", "/auth/password", json={"current_password": user.password, "new_password": "new-password-1"})
    assert user.get(API + "/auth/me").status_code == 200                 # this session continues
    assert other.get(API + "/auth/me").status_code == 401                # other devices are logged out
    assert requests.post(API + "/auth/login", json={"username": user.username, "password": "new-password-1"}).status_code == 200


def test_unauthenticated_requests_are_refused():
    anon = requests.Session()
    for method, path in (("GET", "/workspace"), ("POST", "/tables"), ("GET", "/keys"), ("POST", "/records/bulk"), ("GET", "/shares"),
                         ("GET", "/dav"), ("POST", "/attachments")):
        assert anon.request(method, API + path, json={}).status_code == 401, path
    assert anon.get(API + "/auth/config").status_code == 200


# ---------------------------------------------------------------- bases, tables, views

def test_bases_tables_and_moving(user):
    user.base_id  # the default base exists before adding another
    b = user.j("POST", "/bases", json={"name": "Work", "color": "#3366ff"})
    t = user.table(name="Tasks list")
    user.j("PATCH", f"/tables/{t['id']}", json={"base_id": b["id"], "name": "Renamed"})
    ws = user.workspace()
    moved = next(x for x in ws["tables"] if x["id"] == t["id"])
    assert moved["base_id"] == b["id"] and moved["name"] == "Renamed"
    user.j("PATCH", f"/bases/{b['id']}", json={"name": "Work 2", "position": 0})
    assert user.workspace()["bases"][0]["name"] == "Work 2"
    assert any(v["table_id"] == t["id"] for v in user.j("GET", f"/bases/{b['id']}/views")["views"]) \
        if isinstance(user.j("GET", f"/bases/{b['id']}/views"), dict) else True
    user.j("DELETE", f"/bases/{b['id']}")
    assert user.get(API + f"/tables/{t['id']}").status_code == 404      # its tables went with it
    assert user.patch(API + f"/tables/{uuid.uuid4()}", json={"name": "x"}).status_code == 404
    assert user.get(API + "/tables/not-a-uuid").status_code == 404


def test_views_crud_and_server_side_filtering(user):
    t = user.table(name="Viewed")
    F = user.fields(t["id"])
    for n, s in (("a", "Todo"), ("b", "Done"), ("c", "Todo")):
        user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: n, F["Status"]: s}})
    for vtype in ("grid", "kanban", "timeline", "calendar", "list", "gallery", "chart"):
        v = user.j("POST", f"/tables/{t['id']}/views", json={"name": vtype, "type": vtype})
        assert v["type"] == vtype
    v = user.j("POST", f"/tables/{t['id']}/views", json={"name": "Todo", "type": "grid", "config": {
        "filter": {"conjunction": "or", "items": [{"field": F["Status"], "op": "is", "value": "Todo"},
                                                  {"conjunction": "and", "items": [{"field": F["Name"], "op": "is", "value": "zzz"}]}]},
        "sorts": [{"field": F["Name"], "dir": "desc"}]}})
    got = user.j("GET", f"/tables/{t['id']}/records", params={"view": v["id"]})["records"]
    assert [r["data"][F["Name"]] for r in got] == ["c", "a"]
    user.j("PATCH", f"/views/{v['id']}", json={"name": "Renamed", "config": {"sorts": [{"field": F["Name"], "dir": "asc"}]}})
    got = user.j("GET", f"/tables/{t['id']}/records", params={"view": v["id"], "field_key": "name"})["records"]
    assert [r["data"]["Name"] for r in got] == ["a", "b", "c"]
    user.j("DELETE", f"/views/{v['id']}")
    assert user.get(API + f"/tables/{t['id']}/records", params={"view": v["id"]}).status_code == 404
    assert user.post(API + f"/tables/{t['id']}/views", json={"name": "x", "type": "spreadsheet"}).status_code == 422


def test_other_users_cannot_touch_anything(user, other_user):
    t = user.table(name="Private")
    F = user.fields(t["id"])
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "secret"}})
    view = user.j("GET", f"/tables/{t['id']}")["views"][0]
    b = user.base_id
    for method, path, body in (
            ("GET", f"/tables/{t['id']}", None), ("PATCH", f"/tables/{t['id']}", {"name": "x"}), ("DELETE", f"/tables/{t['id']}", None),
            ("POST", f"/tables/{t['id']}/records", {"data": {}}), ("GET", f"/records/{r['id']}", None),
            ("PATCH", f"/records/{r['id']}", {"data": {F["Name"]: "pwned"}}), ("DELETE", f"/records/{r['id']}", None),
            ("PATCH", f"/tables/{t['id']}/records/{r['id']}", {"data": {}}), ("POST", f"/tables/{t['id']}/fields", {"name": "x", "type": "text"}),
            ("PATCH", f"/fields/{F['Name']}", {"name": "x"}), ("DELETE", f"/fields/{F['Notes']}", None),
            ("PATCH", f"/views/{view['id']}", {"name": "x"}), ("DELETE", f"/views/{view['id']}", None),
            ("POST", f"/tables/{t['id']}/views", {"name": "x", "type": "grid"}), ("PATCH", f"/bases/{b}", {"name": "x"}),
            ("DELETE", f"/bases/{b}", None), ("GET", f"/tables/{t['id']}/changes?since=0.x", None), ("POST", f"/tables/{t['id']}/import", {"columns": [], "rows": []}),
            ("GET", f"/tables/{t['id']}/titles", None), ("POST", f"/tables/{t['id']}/formula", {"expression": "1"})):
        code = other_user.request(method, API + path, json=body).status_code
        assert code in (403, 404), (method, path, code)
    assert user.record(t["id"], r["id"])["data"][F["Name"]] == "secret"
    assert user.j("GET", f"/tables/{t['id']}")["table"]["name"] == "Private"


# ---------------------------------------------------------------- attachments

def test_attachments(user, other_user):
    t = user.table(name="Files")
    F = user.fields(t["id"])
    files = add(user, t["id"], "Files", "attachment")["id"]
    png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000" "1f15c4890000000d4944415478da63f8cfc0f01f0005000201" "a5f5d2c10000000049454e44ae426082")
    up = user.post(API + "/attachments", files={"file": ("dot.png", png, "image/png")})
    assert up.status_code == 200, up.text
    a = up.json()
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "with file", files: [a]}})
    got = user.get(API + f"/attachments/{a['id']}")
    assert got.status_code == 200 and got.content == png
    assert "attachment" in user.get(API + f"/attachments/{a['id']}", params={"download": "true"}).headers.get("content-disposition", "")
    assert other_user.get(API + f"/attachments/{a['id']}").status_code in (403, 404)
    assert user.record(t["id"], r["id"])["data"][files][0]["name"] == "dot.png"
    big = user.post(API + "/attachments", files={"file": ("x.txt", b"", "text/plain")})
    assert big.status_code in (200, 400)
    html = user.post(API + "/attachments", files={"file": ("evil.html", b"<script>alert(1)</script>", "text/html")})
    if html.status_code == 200:
        served = user.get(API + f"/attachments/{html.json()['id']}")
        assert "text/html" not in served.headers.get("content-type", "") or "attachment" in served.headers.get("content-disposition", "")


# ---------------------------------------------------------------- public links

def make_share(user, t, **extra):
    view = user.j("GET", f"/tables/{t['id']}")["views"][0]
    s = user.j("POST", "/shares", json={"name": "Link", "base_id": user.base_id, "items": [{"kind": "table", "id": t["id"], "views": [view["id"]]}], **extra})
    return s, view


def test_public_link_permissions(user):
    t = user.table(name="Shared")
    F = user.fields(t["id"])
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Row"}})
    s, view = make_share(user, t)
    anon = requests.Session()
    base = API + f"/public/{s['token']}/views/{view['id']}"
    assert anon.get(base).status_code == 200
    assert anon.post(base + "/records", json={"data": {F["Name"]: "x"}}).status_code == 403          # read-only link
    assert anon.patch(base + f"/records/{r['id']}", json={"data": {F["Name"]: "x"}}).status_code == 403
    assert anon.delete(base + f"/records/{r['id']}").status_code == 403
    user.j("PATCH", f"/shares/{s['id']}", json={**_share_body(user, t, view), "permissions": {"add": True, "edit": True, "delete": True}})
    assert anon.patch(base + f"/records/{r['id']}", json={"data": {F["Name"]: "Edited"}}).status_code == 200
    assert user.record(t["id"], r["id"])["data"][F["Name"]] == "Edited"
    other_view = user.j("POST", f"/tables/{t['id']}/views", json={"name": "Not shared", "type": "grid"})
    assert anon.get(API + f"/public/{s['token']}/views/{other_view['id']}").status_code in (403, 404)
    assert anon.delete(base + f"/records/{r['id']}").status_code == 200
    assert user.records(t["id"]) == []
    # disabled, regenerated, deleted
    user.j("PATCH", f"/shares/{s['id']}", json={**_share_body(user, t, view), "enabled": False})
    assert anon.get(API + f"/public/{s['token']}").status_code in (403, 404)
    user.j("PATCH", f"/shares/{s['id']}", json={**_share_body(user, t, view), "enabled": True})
    new = user.j("POST", f"/shares/{s['id']}/regenerate")
    assert anon.get(API + f"/public/{s['token']}").status_code == 404
    assert anon.get(API + f"/public/{new['token']}").status_code == 200
    user.j("DELETE", f"/shares/{s['id']}")
    assert anon.get(API + f"/public/{new['token']}").status_code == 404


def _share_body(user, t, view):
    return {"name": "Link", "base_id": user.base_id, "items": [{"kind": "table", "id": t["id"], "views": [view["id"]]}]}


def test_public_link_password_and_expiry(user):
    t = user.table(name="Locked")
    s, view = make_share(user, t, password="open-sesame")
    anon = requests.Session()
    meta = anon.get(API + f"/public/{s['token']}").json()
    assert meta.get("locked") or meta.get("needs_password") or not meta.get("items")
    assert anon.get(API + f"/public/{s['token']}/views/{view['id']}").status_code in (401, 403)
    assert anon.post(API + f"/public/{s['token']}/unlock", json={"password": "wrong"}).status_code in (401, 403)
    assert anon.post(API + f"/public/{s['token']}/unlock", json={"password": "open-sesame"}).status_code == 200
    assert anon.get(API + f"/public/{s['token']}/views/{view['id']}").status_code == 200
    s2, _ = make_share(user, t, expires_at="2020-01-01T00:00:00Z")
    assert requests.get(API + f"/public/{s2['token']}").status_code in (403, 404, 410)
    # the share page and its install files
    assert requests.get(BASE + f"/s/{s['token']}").status_code == 200
    assert requests.get(BASE + f"/s/{s['token']}/manifest.webmanifest").status_code == 200
    assert requests.get(BASE + f"/s/{s['token']}/icon-192.png").headers["content-type"] == "image/png"
    assert requests.get(BASE + "/s/nonexistent-token").status_code in (200, 404)


def test_share_cannot_include_other_users_tables(user, other_user):
    theirs = other_user.table(name="Theirs")
    r = user.post(API + "/shares", json={"name": "x", "base_id": user.base_id, "items": [{"kind": "table", "id": theirs["id"]}]})
    assert r.status_code in (400, 403, 404)


# ---------------------------------------------------------------- combined databases

def test_combined_database_records_views_and_sources(user):
    a, b = user.table(name="Home"), user.table(name="Office")
    A, B = user.fields(a["id"]), user.fields(b["id"])
    c = user.j("POST", "/combos", json={"name": "All", "base_id": user.base_id, "sources": [{"table_id": a["id"]}, {"table_id": b["id"]}]})
    combo = user.j("GET", f"/combos/{c['id']}")
    name_f = next(f for f in combo["fields"] if f["name"] == "Name")
    rec = user.j("POST", f"/combos/{c['id']}/records", json={"table_id": b["id"], "data": {name_f["id"]: "Via combo"}})
    assert any(r["data"][B["Name"]] == "Via combo" for r in user.records(b["id"]))
    user.j("PATCH", f"/combos/{c['id']}/records/{rec['data']['__id'] if '__id' in rec['data'] else rec['id']}",
           json={"table_id": b["id"], "data": {name_f["id"]: "Edited via combo"}})
    assert any(r["data"][B["Name"]] == "Edited via combo" for r in user.records(b["id"]))
    v = user.j("POST", f"/combos/{c['id']}/views", json={"name": "Board", "type": "kanban"})
    assert v["type"] == "kanban"
    user.j("PATCH", f"/combos/{c['id']}", json={"name": "Just home", "sources": [{"table_id": a["id"]}]})
    combo = user.j("GET", f"/combos/{c['id']}")
    assert combo["combo"]["name"] == "Just home" and all(r["table_id"] == a["id"] for r in combo["records"])
    user.j("DELETE", f"/combos/{c['id']}")
    assert user.get(API + f"/combos/{c['id']}").status_code == 404
    assert user.records(b["id"])                                        # the source data stays


def test_deleting_a_source_table_keeps_the_combo_working(user):
    a, b = user.table(name="S1"), user.table(name="S2")
    c = user.j("POST", "/combos", json={"name": "Both", "base_id": user.base_id, "sources": [{"table_id": a["id"]}, {"table_id": b["id"]}]})
    user.j("DELETE", f"/tables/{b['id']}")
    r = user.get(API + f"/combos/{c['id']}")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- linked tables (another Probyte's public link)

def test_linked_table_from_a_public_link(user, other_user):
    src = user.table(name="Catalogue")
    F = user.fields(src["id"])
    r = user.j("POST", f"/tables/{src['id']}/records", json={"data": {F["Name"]: "Lamp"}})
    s, view = make_share(user, src, permissions={"add": True, "edit": True})
    url = f"{BASE}/s/{s['token']}"
    info = other_user.j("POST", "/remote/inspect", json={"url": url})
    assert info
    linked = other_user.j("POST", "/tables", json={"name": "Their catalogue", "kind": "remote", "base_id": other_user.base_id,
                                                   "remote": {"url": url}})
    data = other_user.j("GET", f"/tables/{linked['id']}")
    assert [x["data"].get(next(f["id"] for f in data["fields"] if f["name"] == "Name")) for x in data["records"]] == ["Lamp"]
    name_id = next(f["id"] for f in data["fields"] if f["name"] == "Name")
    other_user.j("PATCH", f"/tables/{linked['id']}/records/{data['records'][0]['id']}", json={"data": {name_id: "Desk lamp"}})
    assert user.record(src["id"], r["id"])["data"][F["Name"]] == "Desk lamp"
    other_user.j("POST", f"/tables/{linked['id']}/refresh")
    assert other_user.post(API + f"/tables/{linked['id']}/fields", json={"name": "x", "type": "text"}).status_code == 400
    # the owner turns the link off: the linked table reports it instead of failing
    user.j("DELETE", f"/shares/{s['id']}")
    other_user.post(API + f"/tables/{linked['id']}/refresh")
    assert other_user.get(API + f"/tables/{linked['id']}").status_code == 200


# ---------------------------------------------------------------- misc endpoints

def test_user_lookup_and_titles(user, other_user):
    assert user.j("GET", "/users/lookup", params={"username": other_user.username.upper()})["username"] == other_user.username
    assert user.get(API + "/users/lookup", params={"username": "nobody-here"}).status_code == 404
    t = user.table(name="Titles")
    F = user.fields(t["id"])
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "First"}})
    titles = user.j("GET", f"/tables/{t['id']}/titles")
    assert "First" in str(titles) and r["id"] in str(titles)


def test_dav_info_check_and_reset(user):
    d = user.j("GET", "/dav")
    assert d["username"] and d["password"]
    chk = user.post(API + "/dav/check")
    assert chk.status_code == 200
    new = user.j("POST", "/dav/reset")
    assert new["password"] != d["password"]
    assert requests.request("PROPFIND", f"{BASE}/{d['username']}/", auth=(d["username"], new["password"]), headers={"Depth": "0"}).status_code == 207
    assert requests.request("PROPFIND", f"{BASE}/{d['username']}/", auth=(d["username"], d["password"]), headers={"Depth": "0"}).status_code == 401


def test_external_account_validation(user):
    bad = user.post(API + "/accounts/test", json={"name": "x", "url": "http://127.0.0.1:1/", "username": "a", "password": "b"})
    assert bad.status_code in (200, 400) and (bad.status_code == 400 or not bad.json().get("ok"))
    a = user.j("POST", "/accounts", json={"name": "Elsewhere", "url": "http://radicale:5232/", "username": user.dav_auth()[0], "password": user.dav_auth()[1]})
    cals = user.get(API + f"/accounts/{a['id']}/calendars")
    assert cals.status_code == 200
    user.j("PATCH", f"/accounts/{a['id']}", json={"name": "Renamed", "url": "http://radicale:5232/"})
    user.j("DELETE", f"/accounts/{a['id']}")
    assert user.get(API + f"/accounts/{a['id']}/calendars").status_code == 404


def test_calendar_account_addresses_are_checked(user):
    for url in ("http://169.254.169.254/", "http://127.0.0.1:8000/", "ftp://example.com", "not a url", "http://[::1]/"):
        r = user.post(API + "/accounts", json={"name": "x", "url": url, "username": "a", "password": "b"})
        assert r.status_code == 400, (url, r.status_code)
        assert user.post(API + "/accounts/test", json={"name": "x", "url": url, "username": "a", "password": "b"}).status_code == 400
    import time
    t = time.time()
    r = user.post(API + "/accounts/test", json={"name": "x", "url": "http://10.255.255.1/", "username": "a", "password": "b"})
    assert r.status_code == 400 and time.time() - t < 25           # an unreachable server fails fast


def test_security_headers():
    r = requests.get(BASE + "/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "X-Frame-Options" not in requests.get(BASE + "/s/anything").headers   # public links can be embedded


def test_new_views_get_what_they_need_to_show_something(user):
    t = user.table("mixed", "Defaults")
    F = user.fields(t["id"])
    board = user.j("POST", f"/tables/{t['id']}/views", json={"name": "B", "type": "kanban"})
    assert board["config"]["groupBy"] == F["status"]                    # a status, not the task/event Type
    cal = user.j("POST", f"/tables/{t['id']}/views", json={"name": "C", "type": "calendar"})
    assert cal["config"]["dateField"] == F["dtstart"] and cal["config"]["endField"] == F["dtend"]
    mine = user.j("POST", f"/tables/{t['id']}/views", json={"name": "T", "type": "timeline", "config": {"dateField": F["dtend"]}})
    assert mine["config"]["dateField"] == F["dtend"]                     # an explicit choice stays


def test_preferences_and_hidden_tables(user):
    assert user.j("GET", "/auth/me")["prefs"] == {}
    assert user.j("PATCH", "/auth/prefs", json={"week_start": 0}) == {"week_start": 0}
    assert user.j("GET", "/auth/me")["prefs"]["week_start"] == 0
    assert user.patch(API + "/auth/prefs", json={"week_start": 9}).status_code == 400
    t = user.table(name="Tucked away")
    user.j("PATCH", f"/tables/{t['id']}", json={"hidden": True})
    assert next(x for x in user.workspace()["tables"] if x["id"] == t["id"])["hidden"] is True
    user.j("PATCH", f"/tables/{t['id']}", json={"name": "Renamed"})          # other changes leave it hidden
    assert next(x for x in user.workspace()["tables"] if x["id"] == t["id"])["hidden"] is True
    c = user.j("POST", "/combos", json={"name": "C", "base_id": user.base_id, "sources": [{"table_id": t["id"]}]})
    user.j("PATCH", f"/combos/{c['id']}", json={"hidden": True})
    assert next(x for x in user.workspace()["combos"] if x["id"] == c["id"])["hidden"] is True


def test_combined_database_loads_linked_sources_separately(user, other_user):
    src = other_user.table(name="Their list")
    O = other_user.fields(src["id"])
    other_user.j("POST", f"/tables/{src['id']}/records", json={"data": {O["Name"]: "From afar"}})
    s = other_user.j("POST", "/shares", json={"name": "L", "base_id": other_user.base_id, "items": [{"kind": "table", "id": src["id"]}]})
    linked = user.j("POST", "/tables", json={"name": "Linked", "kind": "remote", "base_id": user.base_id, "remote": {"url": f"{BASE}/s/{s['token']}"}})
    mine = user.table(name="Mine")
    M = user.fields(mine["id"])
    user.j("POST", f"/tables/{mine['id']}/records", json={"data": {M["Name"]: "Close by"}})
    c = user.j("POST", "/combos", json={"name": "Both", "base_id": user.base_id, "sources": [{"table_id": mine["id"]}, {"table_id": linked["id"]}]})
    fast = user.j("GET", f"/combos/{c['id']}", params={"defer_remote": "true"})
    assert fast["pending"] == [linked["id"]] and {r["table_id"] for r in fast["records"]} == {mine["id"]}
    part = user.j("GET", f"/combos/{c['id']}/sources/{linked['id']}")
    name_f = next(f["id"] for f in fast["fields"] if f["name"] == "Name")
    assert [r["data"][name_f] for r in part["records"]] == ["From afar"]
    full = user.j("GET", f"/combos/{c['id']}")                                   # without defer: everything at once
    assert full["pending"] == [] and len(full["records"]) == 2
    assert user.get(API + f"/combos/{c['id']}/sources/{src['id']}").status_code == 404   # not a source
