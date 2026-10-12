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

from conftest import API, BASE, Client

FAKE = "http://fakeoffice:8000"
SECRET = "fake-office-secret-123"
S3 = {"kind": "s3", "endpoint": "http://s3:9000", "bucket": "test-bucket", "username": "testkey", "secret": "testsecret12345"}


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
    a.j("PUT", "/office/settings", json={"url": FAKE, "secret": SECRET, "probyte_url": "http://app:8000", "server_url": ""})
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
    admin.j("PUT", "/office/settings", json={"url": FAKE, "secret": "wrong-secret-0000", "probyte_url": "http://app:8000"})
    r = admin.post(API + "/office/settings/test")
    assert r.status_code == 400 and "secret" in r.json()["detail"]
    admin.j("PUT", "/office/settings", json={"url": FAKE, "secret": None, "probyte_url": "http://app:8000"})  # None: keep it
    r = admin.post(API + "/office/settings/test")
    assert r.status_code == 400  # still the wrong one
    admin.j("PUT", "/office/settings", json={"url": FAKE, "secret": SECRET, "probyte_url": "http://app:8000"})
    assert admin.j("POST", "/office/settings/test")["ok"]
    assert admin.put(API + "/office/settings", json={"url": "javascript:alert(1)"}).status_code == 400
    st = other.j("GET", "/office/status")
    assert st["enabled"] and "docx" in st["editable"] and "pdf" in st["viewable"] and "pdf" not in st["editable"]


def test_sessions_are_signed_and_only_for_the_owner(doc):
    u, t, rec = doc
    s = u.j("POST", "/office/sessions", json={"record_id": rec["id"], "mode": "edit"})
    c = s["config"]
    assert s["mode"] == "edit" and s["api_url"] == FAKE + "/web-apps/apps/api/documents/api.js"
    assert c["documentType"] == "word" and c["document"]["fileType"] == "txt" and c["editorConfig"]["mode"] == "edit"
    signed = claims(c["token"])
    assert signed["document"]["url"] == c["document"]["url"] and signed["editorConfig"]["callbackUrl"] == c["editorConfig"]["callbackUrl"]
    assert c["document"]["url"].startswith("http://app:8000/api/office/file/")
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
