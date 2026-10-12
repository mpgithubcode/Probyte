"""Against a real ONLYOFFICE Document Server (optional: runs when ONLYOFFICE_TEST_URL is set, e.g. http://onlyoffice
with JWT secret ONLYOFFICE_TEST_SECRET). Opens a Word file from a files table in the editor, types, saves to the
storage, then types again and discards."""
import io
import os
import time
import uuid
import zipfile

import pytest
import requests
from playwright.sync_api import expect, sync_playwright

from conftest import API, BASE, Client

URL = os.environ.get("ONLYOFFICE_TEST_URL")
SECRET = os.environ.get("ONLYOFFICE_TEST_SECRET", "")
pytestmark = pytest.mark.skipif(not URL, reason="no ONLYOFFICE Document Server to test against")
S3 = {"kind": "s3", "endpoint": "http://s3:9000", "bucket": "test-bucket", "username": "testkey", "secret": "testsecret12345"}


def docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.read("word/document.xml").decode()


def wait(check, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        out = check()
        if out:
            return out
        time.sleep(1)
    raise AssertionError("timed out")


@pytest.fixture
def admin():
    name, pw = os.environ["PROBYTE_ADMIN"].split(":")
    a = Client(name, pw)
    yield a
    a.j("PUT", "/office/settings", json={"url": "", "secret": "", "probyte_url": "", "server_url": ""})


def test_edit_save_and_discard_in_onlyoffice(admin):
    name, pw = os.environ["PROBYTE_ADMIN"].split(":")
    admin.j("PUT", "/office/settings", json={"url": URL, "secret": SECRET, "probyte_url": "http://app:8000", "server_url": URL})
    assert admin.j("POST", "/office/settings/test")["ok"]
    t = admin.table("files", f"Office {uuid.uuid4().hex[:4]}", storage=S3, storage_prefix=f"oo{uuid.uuid4().hex[:6]}")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rec = admin.post(API + f"/tables/{t['id']}/files", files=[("files", ("report.docx", open(os.path.join(here, "fixtures", "report.docx"), "rb").read(),
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))]).json()["records"][0]
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        pg.goto(BASE)
        pg.fill("input[autocomplete=username]", name)
        pg.fill("input[type=password]", pw)
        pg.click("button:has-text('Sign in')")
        pg.wait_for_selector(".page, .welcome")
        pg.goto(f"{BASE}/#/table/{t['id']}?record={rec['id']}")

        def open_editor():
            pg.click(".viewer-bar button:has-text('Edit')")
            pg.click(".popover .menu-item:has-text('Edit in ONLYOFFICE')")
            frame = pg.frame_locator(".office-frame iframe, iframe[name='frameEditor']").first
            frame.locator("#id_viewer, #editor_sdk").first.wait_for(timeout=120000)
            pg.wait_for_timeout(4000)
            dialog = frame.locator(".asc-window.modal:visible, .alert:visible")
            if dialog.count():  # the document server is telling us something (it shouldn't be)
                raise AssertionError("ONLYOFFICE says: " + " ".join(dialog.all_inner_texts()))
            return frame

        frame = open_editor()
        frame.locator("#id_viewer, #editor_sdk").first.click(position={"x": 300, "y": 250})
        pg.keyboard.type(" EDITED-BY-TEST ", delay=40)
        expect(pg.locator(".viewer-note")).to_contain_text("Unsaved changes", timeout=20000)
        pg.click(".viewer-bar button:has-text('Save')")
        expect(pg.locator(".viewer-note")).to_contain_text("Saved", timeout=90000)
        assert "EDITED-BY-TEST" in docx_text(admin.get(API + f"/files/{rec['id']}/content").content)
        pg.click(".viewer-bar button:has-text('Close editor')")              # saved: closes without asking
        expect(pg.locator(".office-badge")).to_have_count(0)
        # again: type, then close and discard
        frame = open_editor()
        frame.locator("#id_viewer, #editor_sdk").first.click(position={"x": 300, "y": 250})
        pg.keyboard.type(" DISCARD-ME ", delay=40)
        expect(pg.locator(".viewer-note")).to_contain_text("Unsaved changes", timeout=20000)
        pg.click(".viewer-bar button:has-text('Close editor')")
        expect(pg.locator(".viewer-dialog")).to_contain_text("Save your changes")
        pg.click(".viewer-dialog button:has-text('Discard')")
        expect(pg.locator(".office-badge")).to_have_count(0)
        pg.wait_for_timeout(15000)  # the document server's final save after closing (ignored)
        text = docx_text(admin.get(API + f"/files/{rec['id']}/content").content)
        assert "EDITED-BY-TEST" in text and "DISCARD-ME" not in text
        b.close()
