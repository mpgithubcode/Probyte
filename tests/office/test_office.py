"""Editing files in ONLYOFFICE, against a stand-in Document Server (tests/fakeoffice: the same API, JWT signed,
with a textarea for an editor). test_real_onlyoffice.py runs the same flow against the real one when there is one."""
import base64
import hashlib
import hmac
import json
import os
import time
import uuid

import pytest
import requests
from playwright.sync_api import expect, sync_playwright

from conftest import API, BASE, Client, S3_URL

FAKE = os.environ.get("TEST_FAKEOFFICE_URL", "http://fakeoffice:8000")  # the stand-in Document Server
SECRET = "fake-office-secret-123"
S3 = {"kind": "s3", "endpoint": S3_URL, "bucket": "test-bucket", "username": "testkey", "secret": "testsecret12345"}


def sign(payload, secret=SECRET):
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    head, body = b64(b'{"alg":"HS256","typ":"JWT"}'), b64(json.dumps(payload).encode())
    return f"{head}.{body}.{b64(hmac.new(secret.encode(), f'{head}.{body}'.encode(), hashlib.sha256).digest())}"


def claims(token):
    head, body, sig = token.split(".")
    good = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
    assert sig == good, "not signed with the Document Server's secret"
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def wait(check, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        out = check()
        if out:
            return out
        time.sleep(0.3)
    raise AssertionError("timed out")


@pytest.fixture(scope="module")
def admin():
    name, pw = os.environ["PROBYTE_ADMIN"].split(":")
    a = Client(name, pw)
    a.j("PUT", "/office/settings", json={"url": FAKE, "secret": SECRET, "probyte_url": BASE, "server_url": ""})
    yield a
    a.j("PUT", "/office/settings", json={"url": "", "secret": "", "probyte_url": "", "server_url": ""})  # the other tests run without


@pytest.fixture
def doc(admin):
    """A text file in an S3 files table: (owner, table, record)."""
    t = admin.table("files", f"Office {uuid.uuid4().hex[:4]}", storage=S3, storage_prefix=f"of{uuid.uuid4().hex[:6]}")
    rec = admin.post(API + f"/tables/{t['id']}/files", files=[("files", ("notes.txt", b"first draft", "text/plain"))]).json()["records"][0]
    return admin, t, rec


def content(u, rec):
    return u.get(API + f"/files/{rec['id']}/content").text


def editor(session):
    """What the browser's editor does with the settings: the Document Server opens the file (and calls back)."""
    r = requests.post(FAKE + "/fake/open", json={"config": session["config"]}).json()
    assert "text" in r, r
    return r["text"]


def edit(session, text):
    c = session["config"]
    requests.post(FAKE + "/fake/edit", json={"key": c["document"]["key"], "user": c["editorConfig"]["user"]["id"], "text": text})


def close_editor(session):
    c = session["config"]
    requests.post(FAKE + "/fake/close", json={"key": c["document"]["key"], "user": c["editorConfig"]["user"]["id"]})


def fake_log(key):
    return [e for e in requests.get(FAKE + "/fake/log").json() if e["key"] == key]


def test_settings_are_for_the_admin_and_are_checked(admin):
    other = Client()
    assert other.get(API + "/office/settings").status_code == 403
    assert other.put(API + "/office/settings", json={"url": "http://evil"}).status_code == 403
    s = admin.j("GET", "/office/settings")
    assert s["url"] == FAKE and s["has_secret"] and "secret" not in s
    assert admin.j("POST", "/office/settings/test") == {"ok": True, "version": "fake-8.2"}
    # a wrong secret: the Document Server refuses the signature
    admin.j("PUT", "/office/settings", json={"url": FAKE, "secret": "wrong-secret-0000", "probyte_url": BASE})
    r = admin.post(API + "/office/settings/test")
    assert r.status_code == 400 and "secret" in r.json()["detail"]
    admin.j("PUT", "/office/settings", json={"url": FAKE, "secret": None, "probyte_url": BASE})  # None: keep it
    r = admin.post(API + "/office/settings/test")
    assert r.status_code == 400  # still the wrong one
    admin.j("PUT", "/office/settings", json={"url": FAKE, "secret": SECRET, "probyte_url": BASE})
    assert admin.j("POST", "/office/settings/test")["ok"]
    assert admin.put(API + "/office/settings", json={"url": "javascript:alert(1)"}).status_code == 400
    st = other.j("GET", "/office/status")
    assert st["enabled"] and "docx" in st["editable"] and "pdf" in st["viewable"] and "pdf" not in st["editable"]


def test_sessions_are_signed_and_only_for_the_owner(doc):
    u, t, rec = doc
    s = u.j("POST", "/office/sessions", json={"record_id": rec["id"], "mode": "edit"})
    c = s["config"]
    # browsers load the editor through Probyte (the relay), wherever the Document Server is
    assert s["mode"] == "edit" and s["api_url"] == BASE + "/office-ds/web-apps/apps/api/documents/api.js"
    assert "DocsAPI" in requests.get(s["api_url"]).text
    assert c["documentType"] == "word" and c["document"]["fileType"] == "txt" and c["editorConfig"]["mode"] == "edit"
    signed = claims(c["token"])
    assert signed["document"]["url"] == c["document"]["url"] and signed["editorConfig"]["callbackUrl"] == c["editorConfig"]["callbackUrl"]
    assert c["document"]["url"].startswith(BASE + "/api/office/file/")
    # the file address works without a login (the Document Server has none) but only with the session's token
    assert requests.get(c["document"]["url"]).content == b"first draft"
    assert requests.get(c["document"]["url"][:-4] + "abcd").status_code == 404
    # someone else can't open, save or discard it
    other = Client()
    assert other.post(API + "/office/sessions", json={"record_id": rec["id"]}).status_code == 404
    assert other.post(API + f"/office/sessions/{s['session']}/save").status_code == 404
    assert other.post(API + f"/office/sessions/{s['session']}/discard").status_code == 404
    # read-only
    v = u.j("POST", "/office/sessions", json={"record_id": rec["id"], "mode": "view"})
    assert v["mode"] == "view" and v["config"]["editorConfig"]["mode"] == "view" and not v["config"]["document"]["permissions"]["edit"]
    # not a document
    img = u.post(API + f"/tables/{t['id']}/files", files=[("files", ("a.zip", b"PK\x05\x06" + b"\0" * 18, "application/zip"))]).json()["records"][0]
    assert u.post(API + "/office/sessions", json={"record_id": img["id"]}).status_code == 400


def test_relay_and_direct_mode(admin, doc):
    u, t, rec = doc
    relay = BASE + "/office-ds"
    assert requests.get(relay + "/healthcheck").text == "true"
    # the Document Server's service worker is replaced by one that removes itself (a stale one kept the editor from loading)
    sw = requests.get(relay + "/document_editor_service_worker.js")
    assert sw.headers["content-type"].startswith("application/javascript") and "unregister" in sw.text
    # what browsers never need isn't relayed
    for path in ("/coauthoring/CommandService.ashx", "/ConvertService.ashx", "/command", "/info/info.json", "/../api/office/status"):
        assert requests.post(relay + path, json={"c": "version"}).status_code == 404, path
    # directly: browsers load it from its own address, and the relay is off
    try:
        admin.j("PUT", "/office/settings", json={"url": FAKE, "secret": None, "probyte_url": BASE, "via_probyte": False})
        assert admin.j("GET", "/office/settings")["via_probyte"] is False
        s = u.j("POST", "/office/sessions", json={"record_id": rec["id"]})
        assert s["api_url"] == FAKE + "/web-apps/apps/api/documents/api.js"
        assert requests.get(relay + "/healthcheck").status_code == 404
    finally:
        admin.j("PUT", "/office/settings", json={"url": FAKE, "secret": None, "probyte_url": BASE, "via_probyte": True})
    # the settings test checks that the Document Server can download from Probyte, too
    admin.j("PUT", "/office/settings", json={"url": FAKE, "secret": None, "probyte_url": "http://nowhere.invalid:8000"})
    r = admin.post(API + "/office/settings/test")
    assert r.status_code == 400 and "can't download files from Probyte" in r.json()["detail"]
    admin.j("PUT", "/office/settings", json={"url": FAKE, "secret": None, "probyte_url": BASE})
    assert admin.j("POST", "/office/settings/test")["ok"]


def test_callbacks_must_be_signed_and_for_the_document(doc):
    u, t, rec = doc
    s = u.j("POST", "/office/sessions", json={"record_id": rec["id"]})
    cb, key = s["config"]["editorConfig"]["callbackUrl"], s["config"]["document"]["key"]
    assert requests.post(cb, json={"key": key, "status": 1}).status_code == 403                           # unsigned
    assert requests.post(cb, json={"token": sign({"key": key, "status": 1}, "not-the-secret")}).status_code == 403
    assert requests.post(cb, json={"token": sign({"key": "another-doc", "status": 1})}).status_code == 400
    assert requests.post(cb, json={"token": sign({"key": key, "status": 1})}).json() == {"error": 0}
    assert requests.post(cb, headers={"Authorization": "Bearer " + sign({"payload": {"key": key, "status": 4}})}, json={"key": key, "status": 4}).json() == {"error": 0}
    # a "saved version" somewhere other than the Document Server is only ever fetched from the Document Server
    r = requests.post(cb, json={"token": sign({"key": key, "status": 6, "url": "http://s3:9000/test-bucket/whatever"})}).json()
    assert r == {"error": 1}
    assert content(u, rec) == "first draft"
    assert "HTTP 404" in u.j("GET", f"/office/sessions/{s['session']}")["error"]


def test_save_writes_a_new_version_to_the_storage(doc):
    u, t, rec = doc
    s = u.j("POST", "/office/sessions", json={"record_id": rec["id"]})
    assert editor(s) == "first draft"
    assert u.j("POST", f"/office/sessions/{s['session']}/save") == {"saving": False, "unchanged": True}
    edit(s, "second draft")
    assert u.j("POST", f"/office/sessions/{s['session']}/save") == {"saving": True}
    wait(lambda: u.j("GET", f"/office/sessions/{s['session']}")["saves"] == 1)
    assert content(u, rec) == "second draft"
    r = u.record(t["id"], rec["id"])
    assert r["data"][u.fields(t["id"])["size"]] == len("second draft")
    # closing with the changes saved: the Document Server says nothing changed (4)
    close_editor(s)
    wait(lambda: any(e["status"] == 4 for e in fake_log(s["config"]["document"]["key"])))
    # the next editor starts from the new version (a new key)
    s2 = u.j("POST", "/office/sessions", json={"record_id": rec["id"]})
    assert s2["config"]["document"]["key"] != s["config"]["document"]["key"]
    assert editor(s2) == "second draft"
    close_editor(s2)


def test_closing_without_saving_saves_and_discard_does_not(doc):
    u, t, rec = doc
    # closed with changes not saved (the browser went away): the Document Server's final save is kept
    s = u.j("POST", "/office/sessions", json={"record_id": rec["id"]})
    editor(s)
    edit(s, "kept on close")
    close_editor(s)
    wait(lambda: content(u, rec) == "kept on close")
    # Discard: the final save is ignored, and the next editor starts again from the file in the storage
    s = u.j("POST", "/office/sessions", json={"record_id": rec["id"]})
    editor(s)
    edit(s, "throw this away")
    u.j("POST", f"/office/sessions/{s['session']}/discard")
    close_editor(s)
    final = wait(lambda: [e for e in fake_log(s["config"]["document"]["key"]) if e["status"] == 2])
    assert final[0]["answer"] == {"error": 0}
    assert content(u, rec) == "kept on close"
    s3 = u.j("POST", "/office/sessions", json={"record_id": rec["id"]})
    assert s3["config"]["document"]["key"] != s["config"]["document"]["key"]
    assert editor(s3) == "kept on close"
    close_editor(s3)


def test_one_discard_doesnt_throw_away_another_editors_changes(doc):
    u, t, rec = doc
    a = u.j("POST", "/office/sessions", json={"record_id": rec["id"]})
    b = u.j("POST", "/office/sessions", json={"record_id": rec["id"]})  # the same document open twice (two tabs)
    assert a["config"]["document"]["key"] == b["config"]["document"]["key"]
    editor(a), editor(b)
    edit(b, "edited in the other tab")
    u.j("POST", f"/office/sessions/{a['session']}/discard")
    close_editor(a), close_editor(b)
    wait(lambda: content(u, rec) == "edited in the other tab")


def test_edit_save_and_discard_in_the_viewer(doc):
    u, t, rec = doc
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 860})
        pg.goto(BASE)
        pg.fill("input[autocomplete=username]", u.username)
        pg.fill("input[type=password]", u.password)
        pg.click("button:has-text('Sign in')")
        pg.wait_for_selector(".page, .welcome")
        pg.goto(f"{BASE}/#/table/{t['id']}?record={rec['id']}")
        expect(pg.locator(".viewer .viewer-code")).to_have_text("first draft")
        pg.click(".viewer-bar button:has-text('Edit')")
        menu = pg.locator(".popover .menu-item")
        expect(menu).to_have_text(["Edit here", "Edit in ONLYOFFICE", "Open in ONLYOFFICE (read-only)"])
        pg.click(".popover .menu-item:has-text('Edit in ONLYOFFICE')")
        box = pg.locator(".office-frame .fake-office-text")
        expect(box).to_have_value("first draft")
        expect(pg.locator(".office-badge")).to_have_text("ONLYOFFICE")
        # type and save at once: Save waits for the editor to hand its edits to the Document Server
        box.fill("saved from the viewer")
        expect(pg.locator(".viewer-note")).to_have_text("Unsaved changes")
        pg.click(".viewer-bar button:has-text('Save')")
        expect(pg.locator(".viewer-note")).to_have_text("Saved", timeout=20000)
        assert content(u, rec) == "saved from the viewer"
        # unsaved changes: closing asks; Keep editing goes back to the editor
        box.fill("not wanted")
        expect(pg.locator(".viewer-note")).to_have_text("Unsaved changes")
        pg.click(".viewer-bar button[aria-label=Close]")
        dialog = pg.locator(".viewer-dialog")
        expect(dialog).to_contain_text("Save your changes to “notes.txt”?")
        dialog.locator("button:has-text('Keep editing')").click()
        expect(dialog).to_have_count(0)
        expect(box).to_have_value("not wanted")
        # Discard: the editor closes, the file stays as it was saved
        pg.click(".viewer-bar button:has-text('Close editor')")
        dialog.locator("button:has-text('Discard')").click()
        expect(pg.locator(".office-badge")).to_have_count(0)
        expect(pg.locator(".viewer .viewer-code")).to_have_text("saved from the viewer")
        time.sleep(3)  # the Document Server's final save (ignored)
        assert content(u, rec) == "saved from the viewer"
        # once more, and Save from the question: saved and closed (Escape asks too)
        pg.click(".viewer-bar button:has-text('Edit')")
        pg.click(".popover .menu-item:has-text('Edit in ONLYOFFICE')")
        expect(box).to_have_value("saved from the viewer")
        box.fill("saved on the way out")
        expect(pg.locator(".viewer-note")).to_have_text("Unsaved changes")
        pg.keyboard.press("Escape")
        dialog.locator("button:has-text('Save')").click()
        expect(pg.locator(".office-badge")).to_have_count(0, timeout=20000)
        expect(pg.locator(".viewer .viewer-code")).to_have_text("saved on the way out")
        assert content(u, rec) == "saved on the way out"
        # read-only: no Save button, the text can't be changed
        pg.click(".viewer-bar button:has-text('Edit')")
        pg.click(".popover .menu-item:has-text('read-only')")
        expect(pg.locator(".office-badge")).to_have_text("ONLYOFFICE · read-only")
        expect(pg.locator(".office-frame .fake-office[data-mode=view]")).to_have_count(1)
        expect(pg.locator(".viewer-bar button:has-text('Save')")).to_have_count(0)
        pg.click(".viewer-bar button:has-text('Close editor')")
        expect(pg.locator(".office-badge")).to_have_count(0)
        b.close()


# ---- old formats: edited as their newer one; saving makes that the file's new version

def names(u, t):
    F = u.fields(t["id"])
    return sorted(r["data"][F["name"]] for r in u.records(t["id"]))


def test_an_old_format_is_saved_as_the_new_one(doc):
    u, t, _ = doc
    rec = u.post(API + f"/tables/{t['id']}/files", files=[("files", ("old.doc", b"old words", "application/msword"))]).json()["records"][0]
    s = u.j("POST", "/office/sessions", json={"record_id": rec["id"], "mode": "edit"})
    c = s["config"]
    assert s["mode"] == "edit" and s["converted_from"] == "doc" and s["saves_as"] == "old.docx"
    assert c["document"]["fileType"] == "docx" and c["document"]["title"] == "old.docx"
    assert editor(s) == "old words"            # the Document Server gets the converted copy
    # discarding leaves the old file alone
    edit(s, "not kept")
    u.j("POST", f"/office/sessions/{s['session']}/discard")
    close_editor(s)
    wait(lambda: [e for e in fake_log(c["document"]["key"]) if e.get("status") == 2])
    assert "old.doc" in names(u, t) and content(u, rec) == "old words"
    # saving: the record's file becomes old.docx (the .doc is gone from the storage)
    s = u.j("POST", "/office/sessions", json={"record_id": rec["id"], "mode": "edit"})
    editor(s)
    edit(s, "new words")
    assert u.j("POST", f"/office/sessions/{s['session']}/save") == {"saving": True}
    wait(lambda: u.j("GET", f"/office/sessions/{s['session']}")["saves"] == 1)
    assert "old.docx" in names(u, t) and "old.doc" not in names(u, t)
    assert content(u, rec) == "new words"
    edit(s, "newer words")                     # saving again in the same editor: the same .docx
    u.j("POST", f"/office/sessions/{s['session']}/save")
    wait(lambda: u.j("GET", f"/office/sessions/{s['session']}")["saves"] == 2)
    close_editor(s)
    u.sync(t["id"])
    assert names(u, t).count("old.docx") == 1 and "old.doc" not in names(u, t) and content(u, rec) == "newer words"
    # read-only opens the file as it is, no conversion
    rec2 = u.post(API + f"/tables/{t['id']}/files", files=[("files", ("sheet.xls", b"cells", "application/vnd.ms-excel"))]).json()["records"][0]
    v = u.j("POST", "/office/sessions", json={"record_id": rec2["id"], "mode": "view"})
    assert v["mode"] == "view" and not v["converted_from"] and v["config"]["document"]["fileType"] == "xls"


# ---- linked tables: edited in this server's Document Server, saved through the link

def share(user, t, **perms):
    view = user.j("GET", f"/tables/{t['id']}")["views"][0]
    s = user.j("POST", "/shares", json={"name": "Link", "base_id": user.base_id, "permissions": perms,
                                       "items": [{"kind": "table", "id": t["id"], "views": [view["id"]]}]})
    return s, view


def link(viewer, s, view):
    return viewer.j("POST", "/tables", json={"name": "Linked", "kind": "remote", "base_id": viewer.base_id,
                                             "remote": {"url": f"{BASE}/s/{s['token']}", "view_id": view["id"]}})


def linked_item(viewer, lt, name):
    fid = next(f["id"] for f in viewer.j("GET", f"/tables/{lt['id']}")["fields"] if f.get("system_key") == "file")
    return next(r["data"][fid][0] for r in viewer.records(lt["id"]) if r["data"].get(fid) and r["data"][fid][0]["name"] == name)


def test_link_permissions_for_files(doc):
    owner, t, rec = doc
    root = API.replace("/api", "")
    # links made before the files permissions: they follow add / edit
    s, view = share(owner, t, add=True, edit=True)
    meta = requests.get(API + f"/public/{s['token']}").json()
    assert meta["permissions"] == {"add": True, "edit": True, "delete": False, "upload_files": True, "edit_files": True}
    # records may be edited, files not
    s, view = share(owner, t, add=True, edit=True, upload_files=False, edit_files=False)
    base = f"{API}/public/{s['token']}/views/{view['id']}"
    r = requests.post(base + "/files", files=[("files", ("x.txt", b"x", "text/plain"))])
    assert r.status_code == 403 and "Uploading files" in r.json()["detail"]
    r = requests.put(base + f"/files/{rec['id']}/file", files={"file": ("x.txt", b"x", "text/plain")})
    assert r.status_code == 403 and "Changing files" in r.json()["detail"]
    assert requests.put(base + f"/files/{rec['id']}/text", json={"text": "x"}).status_code == 403
    # files may be changed though records may not
    s, view = share(owner, t, edit_files=True)
    base = f"{API}/public/{s['token']}/views/{view['id']}"
    assert requests.put(base + f"/files/{rec['id']}/text", json={"text": "via the link"}).status_code == 200
    assert content(owner, rec) == "via the link"
    assert requests.patch(base + f"/records/{rec['id']}", json={"data": {}}).status_code == 403
    # a linked table says what it may do; without "Edit files" its files open in ONLYOFFICE only to read
    viewer = Client()
    s, view = share(owner, t, edit=True, edit_files=False)
    lt = link(viewer, s, view)
    perms = viewer.j("GET", f"/tables/{lt['id']}")["table"]["remote"]["permissions"]
    assert perms["edit"] and not perms["edit_files"]
    item = linked_item(viewer, lt, "notes.txt")
    assert viewer.post(API + "/office/sessions", json={"record_id": item["id"], "table_id": lt["id"], "mode": "edit"}).status_code == 403
    v = viewer.j("POST", "/office/sessions", json={"record_id": item["id"], "table_id": lt["id"], "mode": "view"})
    assert v["mode"] == "view"
    assert requests.get(v["config"]["document"]["url"]).text == "via the link"
    assert viewer.request("PUT", root + item["url"] + "/text", json={"text": "no"}).status_code == 403


def test_a_linked_tables_file_is_edited_and_saved_through_the_link(doc):
    owner, t, rec = doc
    owner.post(API + f"/tables/{t['id']}/files", files=[("files", ("old.doc", b"legacy", "application/msword"))])
    s, view = share(owner, t, edit_files=True)          # only changing files: no record edits
    viewer = Client()
    lt = link(viewer, s, view)
    item = linked_item(viewer, lt, "notes.txt")
    # someone else can't use this person's linked table
    assert Client().post(API + "/office/sessions", json={"record_id": item["id"], "table_id": lt["id"]}).status_code == 404
    sess = viewer.j("POST", "/office/sessions", json={"record_id": item["id"], "table_id": lt["id"], "mode": "edit"})
    assert sess["mode"] == "edit" and editor(sess) == "first draft"
    edit(sess, "edited through the link")
    assert viewer.j("POST", f"/office/sessions/{sess['session']}/save") == {"saving": True}
    wait(lambda: viewer.j("GET", f"/office/sessions/{sess['session']}")["saves"] == 1)
    assert content(owner, rec) == "edited through the link"
    close_editor(sess)
    # an old format through the link: the owner's file becomes the new format
    item = linked_item(viewer, lt, "old.doc")
    sess = viewer.j("POST", "/office/sessions", json={"record_id": item["id"], "table_id": lt["id"], "mode": "edit"})
    assert sess["converted_from"] == "doc" and editor(sess) == "legacy"
    edit(sess, "modern")
    close_editor(sess)                                   # closed without Save: the Document Server's final save
    wait(lambda: "old.docx" in names(owner, t))
    assert "old.doc" not in names(owner, t)
    assert linked_item(viewer, lt, "old.docx")


def test_linked_file_in_the_viewer(doc):
    owner, t, rec = doc
    s, view = share(owner, t, edit_files=True)
    viewer = Client()
    lt = link(viewer, s, view)
    owner.post(API + f"/tables/{t['id']}/files", files=[("files", ("memo.doc", b"memo text", "application/msword"))])
    F = owner.fields(t["id"])
    memo = next(r for r in owner.records(t["id"]) if r["data"][F["name"]] == "memo.doc")
    lrec = next(r for r in viewer.records(lt["id"]) if "memo.doc" in str(r["data"]))
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 860})
        pg.goto(BASE)
        pg.fill("input[autocomplete=username]", viewer.username)
        pg.fill("input[type=password]", viewer.password)
        pg.click("button:has-text('Sign in')")
        pg.wait_for_selector(".page, .welcome")
        pg.goto(f"{BASE}/#/table/{lt['id']}?record={lrec['id']}")
        pg.click(".viewer-bar button:has-text('Edit')")
        expect(pg.locator(".popover .menu-item")).to_have_text(["Edit in ONLYOFFICE (saves as .docx)", "Open in ONLYOFFICE (read-only)"])
        pg.click(".popover .menu-item:has-text('Edit in ONLYOFFICE')")
        box = pg.locator(".office-frame .fake-office-text")
        expect(box).to_have_value("memo text")
        expect(pg.locator(".office-badge")).to_have_text("ONLYOFFICE · as .docx")
        box.fill("memo, edited")
        pg.click(".viewer-bar button:has-text('Save')")
        expect(pg.locator(".viewer-note")).to_have_text("Saved as memo.docx", timeout=20000)
        expect(pg.locator(".viewer-title b")).to_have_text("memo.docx")       # the list reloaded: the new name
        assert content(owner, memo) == "memo, edited"
        pg.click(".viewer-bar button:has-text('Close editor')")
        expect(pg.locator(".office-badge")).to_have_count(0)
        b.close()


def test_a_link_to_another_server(doc):
    """The same, with the linked table using the link's public address (as when it's on another server)."""
    import psycopg
    owner, t, rec = doc
    owner.post(API + f"/tables/{t['id']}/files", files=[("files", ("far.xls", b"far cells", "application/vnd.ms-excel"))])
    s, view = share(owner, t, edit_files=True)
    viewer = Client()
    lt = link(viewer, s, view)
    try:
        conn = psycopg.connect(os.environ.get("BENCH_DB", "postgresql://probyte:probyte-test@db:5432/probyte"))
    except Exception:
        pytest.skip("no database access")
    with conn:
        conn.execute("UPDATE tables SET remote = remote || '{\"local\": false}' WHERE id = %s", (lt["id"],))
    item = linked_item(viewer, lt, "far.xls")
    sess = viewer.j("POST", "/office/sessions", json={"record_id": item["id"], "table_id": lt["id"], "mode": "edit"})
    assert sess["saves_as"] == "far.xlsx" and editor(sess) == "far cells"
    edit(sess, "near cells")
    viewer.j("POST", f"/office/sessions/{sess['session']}/save")
    wait(lambda: viewer.j("GET", f"/office/sessions/{sess['session']}")["saves"] == 1)
    close_editor(sess)
    assert "far.xlsx" in names(owner, t) and "far.xls" not in names(owner, t)
    F = owner.fields(t["id"])
    far = next(r for r in owner.records(t["id"]) if r["data"][F["name"]] == "far.xlsx")
    assert owner.get(API + f"/files/{far['id']}/content").text == "near cells"


def test_files_permissions_in_the_link_settings():
    u = Client()
    t = u.table("files", "Shared drive", storage=S3, storage_prefix=f"ui{uuid.uuid4().hex[:6]}")
    plain = u.table(name="Plain list")
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 860})
        pg.goto(BASE)
        pg.fill("input[autocomplete=username]", u.username)
        pg.fill("input[type=password]", u.password)
        pg.click("button:has-text('Sign in')")
        pg.wait_for_selector(".page, .welcome")
        pg.click(".side-user .side-item")
        pg.click(".settings-tabs button:has-text('Public links')")
        pg.click("button:has-text('New public link')")
        pg.fill(".modal input[placeholder='Team calendar']", "Drive for partners")
        pick = lambda name: pg.locator(".modal label, .modal .share-item").filter(has_text=name).locator("input[type=checkbox]").first
        pick("Plain list").check()
        expect(pg.locator(".modal .perm-row", has_text="Files tables")).to_have_count(0)   # no files table chosen
        pick("Shared drive").check()
        files_row = pg.locator(".modal .perm-row", has_text="Files tables")
        expect(files_row.locator("input").nth(0)).not_to_be_checked()
        pg.locator(".modal .perm-row").first.locator("label", has_text="Add records").locator("input").check()
        expect(files_row.locator("input").nth(0)).to_be_checked()      # follows "Add records" until set
        files_row.locator("label", has_text="Edit files").locator("input").check()
        pg.click(".modal-foot button:has-text('Create link')")
        pg.wait_for_timeout(500)
        b.close()
    s = next(x for x in u.j("GET", "/shares") if x["name"] == "Drive for partners")
    assert s["permissions"]["edit_files"] is True and s["permissions"]["edit"] is False and s["permissions"]["add"] is True
    meta = requests.get(API + f"/public/{s['token']}").json()
    assert meta["permissions"]["upload_files"] and meta["permissions"]["edit_files"] and not meta["permissions"]["edit"]



def test_a_failed_download_is_tried_again_with_a_new_key(doc):
    """The Document Server keeps a failed download under the document's key: the viewer retires the key and reopens."""
    u, t, rec = doc
    requests.post(FAKE + "/fake/fail-next", json={"count": 1})
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1280, "height": 860})
        pg.goto(BASE)
        pg.fill("input[autocomplete=username]", u.username)
        pg.fill("input[type=password]", u.password)
        pg.click("button:has-text('Sign in')")
        pg.wait_for_selector(".page, .welcome")
        pg.goto(f"{BASE}/#/table/{t['id']}?record={rec['id']}")
        pg.click(".viewer-bar button:has-text('Edit')")
        pg.click(".popover .menu-item:has-text('Edit in ONLYOFFICE')")
        expect(pg.locator(".office-frame .fake-office-text")).to_have_value("first draft", timeout=15000)
        expect(pg.locator(".viewer-err")).to_have_count(0)
        b.close()



def test_protocol_and_certificate_settings(admin, doc):
    """Network settings: the protocol the Document Server is told browsers use, and an HTTPS Document Server with a
    self-signed certificate."""
    relay = BASE + "/office-ds"
    base = {"url": FAKE, "secret": None, "probyte_url": BASE}
    try:
        # automatic: as it arrives (here http; behind an HTTPS proxy, https)
        assert requests.get(relay + "/fake/headers").json()["x-forwarded-proto"] == "http"
        assert requests.get(relay + "/fake/headers", headers={"X-Forwarded-Proto": "https"}).json()["x-forwarded-proto"] == "https"
        # set by the admin
        admin.j("PUT", "/office/settings", json={**base, "browser_scheme": "https"})
        assert admin.j("GET", "/office/settings")["browser_scheme"] == "https"
        assert requests.get(relay + "/fake/headers").json()["x-forwarded-proto"] == "https"
        assert admin.put(API + "/office/settings", json={**base, "browser_scheme": "ftp"}).status_code == 400
        # an HTTPS Document Server with a self-signed certificate: refused while the certificate is checked…
        tls = {**base, "url": "https://fakeoffice:8443", "browser_scheme": ""}
        admin.j("PUT", "/office/settings", json=tls)
        r = admin.post(API + "/office/settings/test")
        assert r.status_code == 400 and "certificate" in r.json()["detail"]
        # …and works with the check off (the test, and the relay)
        admin.j("PUT", "/office/settings", json={**tls, "verify_tls": False})
        assert admin.j("GET", "/office/settings")["verify_tls"] is False
        assert admin.j("POST", "/office/settings/test")["ok"]
        assert requests.get(relay + "/healthcheck").text == "true"
    finally:
        admin.j("PUT", "/office/settings", json={**base, "browser_scheme": "", "verify_tls": True})


def test_each_editing_session_gets_a_key_the_server_has_not_seen(doc):
    """The Document Server keeps a closed document under its key, and an editor opening that key again gets its
    cached copy, which fails behind the relay ("Download failed"). So only people editing at the same time share a
    key; opening the file again later, even unchanged, starts a new one."""
    u, t, rec = doc
    requests.post(FAKE + "/fake/fail-next", json={"count": 0})  # (whatever another test left armed)
    a = u.j("POST", "/office/sessions", json={"record_id": rec["id"]})
    editor(a)
    close_editor(a)
    wait(lambda: any(e["status"] == 4 for e in fake_log(a["config"]["document"]["key"])))
    u.j("POST", f"/office/sessions/{a['session']}/close")
    b = u.j("POST", "/office/sessions", json={"record_id": rec["id"]})
    assert b["config"]["document"]["key"] != a["config"]["document"]["key"]
    assert editor(b) == "first draft"
    close_editor(b)
