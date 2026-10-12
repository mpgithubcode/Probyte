"""Probyte's own CalDAV/CardDAV server, spoken to the way phones and calendar apps do: discovery, incremental sync
(sync-collection), preconditions, collections made and changed on a device, and the paths devices set up for the
bundled Radicale still use."""
import re
import uuid

import requests

from conftest import API, BASE, Client

XML = {"Content-Type": "application/xml; charset=utf-8"}


def dav(user, method, url, body=None, **headers):
    return requests.request(method, url if url.startswith("http") else BASE + url, data=body, auth=user.dav_auth(),
                            headers={**(XML if body and body.lstrip().startswith("<") else {}), **headers})


def todo(uid, summary, extra=""):
    return (f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Phone//EN\r\nBEGIN:VTODO\r\nUID:{uid}\r\nDTSTAMP:20261001T000000Z\r\n"
            f"SUMMARY:{summary}\r\n{extra}END:VTODO\r\nEND:VCALENDAR\r\n")


def token_of(text):
    return re.search(r"<d:sync-token>([^<]+)</d:sync-token>", text).group(1)


SYNC = """<?xml version="1.0"?><d:sync-collection xmlns:d="DAV:"><d:sync-token>{}</d:sync-token><d:sync-level>1</d:sync-level>
<d:prop><d:getetag/></d:prop></d:sync-collection>"""


def test_discovery_like_a_phone(user):
    login, _ = user.dav_auth()
    r = requests.request("PROPFIND", BASE + "/.well-known/caldav", allow_redirects=False)
    assert r.status_code == 301 and r.headers["Location"] == "/dav/"
    assert requests.request("PROPFIND", BASE + "/dav/").status_code == 401                       # asks for the login
    assert requests.request("PROPFIND", BASE + "/dav/", auth=(login, "wrong")).status_code == 401
    r = dav(user, "PROPFIND", "/dav/", '<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>', Depth="0")
    assert r.status_code == 207 and f"<d:href>/dav/{login}/</d:href>" in r.text
    r = dav(user, "PROPFIND", f"/dav/{login}/", '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" '
            'xmlns:card="urn:ietf:params:xml:ns:carddav"><d:prop><c:calendar-home-set/><card:addressbook-home-set/>'
            '<c:schedule-inbox-URL/></d:prop></d:propfind>', Depth="0")
    assert r.text.count(f"<d:href>/dav/{login}/</d:href>") == 3                                # itself, and both homes
    assert "404 Not Found" in r.text and "schedule-inbox-URL" in r.text                          # unknown: answered as missing
    o = requests.options(BASE + "/dav/")
    assert "calendar-access" in o.headers["DAV"] and "addressbook" in o.headers["DAV"]


def test_edits_in_probyte_reach_the_phone_at_once_and_sync_collection_says_what_changed(user):
    t = user.table("tasks", "Live")
    F = user.fields(t["id"])
    first = dav(user, "REPORT", t["calendar_url"], SYNC.format(""), Depth="1")
    assert first.status_code == 207
    token = token_of(first.text)
    a = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "From the app"}})
    b = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "To delete"}})
    r = dav(user, "REPORT", t["calendar_url"], SYNC.format(token), Depth="1")
    assert r.text.count("<d:response>") == 2 and token_of(r.text) != token
    token = token_of(r.text)
    nothing = dav(user, "REPORT", t["calendar_url"], SYNC.format(token), Depth="1")
    assert "<d:response>" not in nothing.text and token_of(nothing.text) == token                # nothing new: nothing sent
    href = next(h for h, body in user.server_items(t["calendar_url"]) if "To delete" in body)
    old_etag = dav(user, "HEAD", href).headers["ETag"]
    user.j("PATCH", f"/records/{a['id']}", json={"data": {F["summary"]: "Renamed in the app"}})
    user.j("DELETE", f"/records/{b['id']}")
    r = dav(user, "REPORT", t["calendar_url"], SYNC.format(token), Depth="1")
    assert "404 Not Found" in r.text and href in r.text                                          # the deletion
    assert r.text.count("<d:getetag>") == 1                                                      # the rename
    assert "Renamed in the app" in user.server_item(t["calendar_url"], "Renamed")[1]
    assert old_etag  # (the deleted item's ETag was there before)
    bad = dav(user, "REPORT", t["calendar_url"], SYNC.format("http://radicale.org/ns/sync/abc"), Depth="1")
    assert bad.status_code == 403 and "valid-sync-token" in bad.text                             # an old server's token: start over


def test_preconditions_etags_and_conflicts(user):
    t = user.table("tasks", "Preconditions")
    url = t["calendar_url"] + "item-1.ics"
    r = dav(user, "PUT", url, todo("uid-1", "First"), **{"If-None-Match": "*", "Content-Type": "text/calendar"})
    assert r.status_code == 201 and r.headers["ETag"]
    etag = r.headers["ETag"]
    assert dav(user, "PUT", url, todo("uid-1", "Again"), **{"If-None-Match": "*"}).status_code == 412   # exists already
    assert dav(user, "PUT", url, todo("uid-1", "Stale"), **{"If-Match": '"nope"'}).status_code == 412
    g = dav(user, "GET", url)
    assert g.status_code == 200 and g.headers["ETag"] == etag and "SUMMARY:First" in g.text
    assert dav(user, "GET", url, **{"If-None-Match": etag}).status_code == 304
    r = dav(user, "PUT", url, todo("uid-1", "Second"), **{"If-Match": etag})
    assert r.status_code == 204 and r.headers["ETag"] != etag
    assert dav(user, "PUT", t["calendar_url"] + "other-name.ics", todo("uid-1", "Same UID")).status_code == 409   # one UID, one item
    event = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:e1\r\nDTSTART:20261001T100000Z\r\nSUMMARY:x\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    r = dav(user, "PUT", t["calendar_url"] + "e1.ics", event)
    assert r.status_code == 403 and "supported-calendar-component" in r.text                     # a tasks list takes tasks
    assert dav(user, "PUT", t["calendar_url"] + "junk.ics", "hello").status_code in (400, 415)
    assert dav(user, "DELETE", url, **{"If-Match": '"nope"'}).status_code == 412
    assert dav(user, "DELETE", url).status_code == 204
    assert dav(user, "GET", url).status_code == 404
    assert len(user.records(t["id"])) == 0 and len(user.j("GET", "/trash")["records"]) == 1      # deleted on a phone: in the trash


def test_calendars_made_renamed_and_deleted_on_a_device(user):
    login, _ = user.dav_auth()
    url = f"/dav/{login}/phone-list/"
    body = ('<?xml version="1.0"?><c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:set><d:prop>'
            '<d:displayname>Groceries</d:displayname><c:supported-calendar-component-set><c:comp name="VTODO"/>'
            '</c:supported-calendar-component-set></d:prop></d:set></c:mkcalendar>')
    assert dav(user, "MKCALENDAR", url, body).status_code == 201
    assert dav(user, "MKCALENDAR", url, body).status_code == 405
    t = next(x for x in user.workspace()["tables"] if x["name"] == "Groceries")
    assert t["kind"] == "tasks"
    patch = ('<?xml version="1.0"?><d:propertyupdate xmlns:d="DAV:" xmlns:a="http://apple.com/ns/ical/"><d:set><d:prop>'
             '<d:displayname>Shopping</d:displayname><a:calendar-color>#FF2968FF</a:calendar-color></d:prop></d:set></d:propertyupdate>')
    assert dav(user, "PROPPATCH", url, patch).status_code == 207
    r = dav(user, "PROPFIND", url, '<d:propfind xmlns:d="DAV:" xmlns:a="http://apple.com/ns/ical/"><d:prop><d:displayname/>'
            '<a:calendar-color/></d:prop></d:propfind>', Depth="0")
    assert "Shopping" in r.text and "#FF2968FF" in r.text
    assert user.j("GET", f"/tables/{t['id']}")["table"]["name"] == "Shopping"
    book = ('<?xml version="1.0"?><d:mkcol xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav"><d:set><d:prop>'
            '<d:resourcetype><d:collection/><card:addressbook/></d:resourcetype><d:displayname>Work contacts</d:displayname>'
            '</d:prop></d:set></d:mkcol>')
    assert dav(user, "MKCOL", f"/dav/{login}/work/", book).status_code == 201
    assert any(x["name"] == "Work contacts" and x["kind"] == "contacts" for x in user.workspace()["tables"])
    assert dav(user, "DELETE", url).status_code == 204                                           # to the trash
    assert any(x["id"] == t["id"] for x in user.j("GET", "/trash")["tables"])


def test_only_your_own_calendars_and_the_old_addresses(user, other_user):
    t = user.table("events", "Mine")
    login, _ = user.dav_auth()
    assert requests.request("PROPFIND", t["calendar_url"], auth=other_user.dav_auth(), headers={"Depth": "0"}).status_code == 404
    assert requests.request("PROPFIND", f"{BASE}/dav/{login}/", auth=other_user.dav_auth(), headers={"Depth": "0"}).status_code == 403
    # devices set up while calendars were in Radicale use the same paths without /dav
    old = t["calendar_url"].replace("/dav/", "/", 1)
    r = requests.request("PROPFIND", old, auth=user.dav_auth(), headers={"Depth": "1"})
    assert r.status_code == 207 and f"<d:href>/{login}/" in r.text
    r = requests.request("PROPFIND", BASE + "/", auth=user.dav_auth(), headers={"Depth": "0"},
                         data='<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>')
    assert f"<d:href>/{login}/</d:href>" in r.text
    assert requests.get(BASE + "/", headers={"Accept": "text/html"}).status_code == 200           # the web app is still there


def test_mixed_calendars_answer_queries_by_component_and_move_items(user):
    t = user.table("mixed", "Both")
    F = user.fields(t["id"])
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "A task", F["component"]: "Task"}})
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "An event", F["component"]: "Event",
                                                                F["dtstart"]: "2026-10-20T09:00:00Z", F["dtend"]: "2026-10-20T10:00:00Z"}})
    q = ('<?xml version="1.0"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:getetag/>'
         '<c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VTODO"/></c:comp-filter>'
         '</c:filter></c:calendar-query>')
    r = dav(user, "REPORT", t["calendar_url"], q, Depth="1")
    assert "A task" in r.text and "An event" not in r.text
    other = user.table("mixed", "Elsewhere")
    href = next(h for h, b in user.server_items(t["calendar_url"]) if "A task" in b)
    r = dav(user, "MOVE", href, Destination=other["calendar_url"] + "moved.ics")
    assert r.status_code in (201, 204)
    assert [x["data"][F["summary"]] for x in user.records(t["id"])] == ["An event"]
    F2 = user.fields(other["id"])
    assert [x["data"][F2["summary"]] for x in user.records(other["id"])] == ["A task"]


def test_a_new_device_password_locks_out_the_old_one():
    u = Client()
    old = u.dav_auth()
    t = u.table("tasks", "Locked")
    assert requests.request("PROPFIND", t["calendar_url"], auth=old, headers={"Depth": "0"}).status_code == 207
    u.j("POST", "/dav/reset")
    assert requests.request("PROPFIND", t["calendar_url"], auth=old, headers={"Depth": "0"}).status_code == 401
    assert requests.request("PROPFIND", t["calendar_url"], auth=u.dav_auth(), headers={"Depth": "0"}).status_code == 207


def test_a_phone_cant_overwrite_an_edit_it_hasnt_seen(user):
    """The phone's If-Match names the item it read; an edit made in Probyte since makes it stale: 412, and the phone
    reads the item again (instead of silently undoing the edit)."""
    t = user.table("tasks", "Stale")
    F = user.fields(t["id"])
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Original"}})
    href, body = user.server_item(t["calendar_url"], "Original")
    etag = dav(user, "HEAD", href).headers["ETag"]
    user.j("PATCH", f"/records/{r['id']}", json={"data": {F["summary"]: "Edited in Probyte"}})
    assert dav(user, "PUT", href, body.replace("SUMMARY:Original", "SUMMARY:Phone"), **{"If-Match": etag}).status_code == 412
    assert user.record(t["id"], r["id"])["data"][F["summary"]] == "Edited in Probyte"
