"""Files tables: records that are files in an S3 bucket or a WebDAV folder (test servers: tests/docker-compose.storage.yml)."""
import base64
import time
import uuid

import requests

from conftest import API, BASE, S3_URL, WEBDAV_URL

S3 = {"kind": "s3", "endpoint": S3_URL, "bucket": "test-bucket", "username": "testkey", "secret": "testsecret12345"}
DAV = {"kind": "webdav", "endpoint": WEBDAV_URL + "shared/", "username": "dav", "secret": "davpass1234"}
DAV_AUTH = ("dav", "davpass1234")
# a 2x2 red PNG
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR4nGP4z8AARAwQCgAf7gP9i18U1AAAAABJRU5ErkJggg==")


def wait(check, timeout=15.0):
    end = time.time() + timeout
    while True:
        out = check()
        if out:
            return out
        if time.time() > end:
            raise AssertionError("timed out")
        time.sleep(0.3)


def by_name(user, t, F, name):
    return next((r for r in user.records(t["id"]) if r["data"].get(F["name"]) == name), None)


def upload(user, t, *files, folder=""):
    r = user.post(API + f"/tables/{t['id']}/files", files=[("files", f) for f in files], data={"folder": folder})
    assert r.status_code == 200, r.text
    return r.json()["records"]


def test_s3_files_table(user):
    prefix = f"probyte-{uuid.uuid4().hex[:8]}"
    t = user.table("files", "Bucket", storage=S3, storage_prefix=prefix)
    assert t["kind"] == "files"
    F = user.fields(t["id"])
    info = user.j("GET", f"/tables/{t['id']}")
    assert info["storage"]["location"] == f"test-bucket/{prefix}"
    assert [v["type"] for v in info["views"]] == ["grid", "gallery", "kanban"]
    ro = {f["system_key"] for f in info["fields"] if f.get("readonly")}
    assert ro == {"file", "kind", "size", "modified", "mime", "text"}

    recs = upload(user, t, ("notes.txt", b"hello probyte searchable words", "text/plain"), ("red.png", PNG, "image/png"))
    assert len(recs) == 2
    note, img = by_name(user, t, F, "notes.txt"), by_name(user, t, F, "red.png")
    assert note["data"][F["kind"]] == "Text" and note["data"][F["size"]] == 30 and note["data"][F["mime"]] == "text/plain"
    assert img["data"][F["kind"]] == "Image" and img["data"][F["file"]][0]["view"] == "image"
    # previews: a cover image for the picture, the text for search
    wait(lambda: user.get(API + f"/files/{img['id']}/thumb").status_code == 200)
    assert user.get(API + f"/files/{img['id']}/thumb").headers["content-type"] == "image/webp"
    wait(lambda: (by_name(user, t, F, "notes.txt")["data"].get(F["text"]) or "").startswith("hello probyte"))
    assert by_name(user, t, F, "red.png")["data"][F["file"]][0].get("thumb") is True
    # contents: whole, a byte range, as text
    r = user.get(API + f"/files/{note['id']}/content", params={"download": 1})
    assert r.content == b"hello probyte searchable words" and "attachment" in r.headers["content-disposition"]
    r = user.get(API + f"/files/{note['id']}/content", headers={"Range": "bytes=6-12"})
    assert r.status_code == 206 and r.content == b"probyte"
    assert user.get(API + f"/files/{note['id']}/content", params={"text": 1}).headers["content-type"].startswith("text/plain")
    assert user.get(API + f"/files/{img['id']}/content").headers["content-type"] == "image/png"
    # the same name again gets a number
    again = upload(user, t, ("notes.txt", b"second", "text/plain"))[0]
    assert again["data"][F["name"]] == "notes (2).txt"
    # rename and move: the file follows, the record (and its id) stays
    user.j("PATCH", f"/records/{note['id']}", json={"data": {F["name"]: "renamed.txt", F["folder"]: "docs/2026"}})
    assert user.get(API + f"/files/{note['id']}/content").content == b"hello probyte searchable words"
    r = user.request("PATCH", API + f"/records/{again['id']}", json={"data": {F["name"]: "renamed.txt", F["folder"]: "docs/2026"}})
    assert r.status_code == 400 and "already a file" in r.text
    assert user.request("PATCH", API + f"/records/{again['id']}", json={"data": {F["name"]: "a/b.txt"}}).status_code == 400
    # file fields can't be set by hand
    user.j("PATCH", f"/records/{img['id']}", json={"data": {F["size"]: 1, F["kind"]: "Video"}})
    assert by_name(user, t, F, "red.png")["data"][F["size"]] == len(PNG)
    # edit a text file; replace a file's contents
    user.j("PUT", f"/files/{note['id']}/text", json={"text": "edited in Probyte"})
    assert user.get(API + f"/files/{note['id']}/content").content == b"edited in Probyte"
    rec = user.record(t["id"], note["id"])
    assert rec["data"][F["size"]] == 17 and rec["data"][F["folder"]] == "docs/2026"
    r = user.request("PUT", API + f"/records/{again['id']}/file", files={"file": ("x.txt", b"replaced!", "text/plain")})
    assert r.status_code == 200 and user.get(API + f"/files/{again['id']}/content").content == b"replaced!"
    assert user.request("PUT", API + f"/files/{img['id']}/text", json={"text": "no"}).status_code == 400
    # a new record makes a new empty file
    new = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["name"]: "todo.md", F["folder"]: "docs"}})
    assert user.get(API + f"/files/{new['id']}/content").content == b""
    assert user.record(t["id"], new["id"])["data"][F["kind"]] == "Text"
    # a sync finds nothing new: everything written here is known
    stats = user.sync(t["id"])
    assert stats["pulled"] == 0 and stats["removed"] == 0 and stats["files"] == 4
    # delete: the record goes to the trash, the file stays until the trash is emptied
    user.j("DELETE", f"/records/{again['id']}")
    assert user.get(API + f"/files/{again['id']}/content").status_code == 404
    assert user.sync(t["id"])["files"] == 4 and len(user.records(t["id"])) == 3       # not imported again
    user.j("POST", "/trash/records/restore", json={"ids": [again["id"]]})
    assert len(user.records(t["id"])) == 4 and user.get(API + f"/files/{again['id']}/content").content == b"replaced!"
    user.j("DELETE", f"/records/{again['id']}")
    user.j("POST", "/trash/records/delete", json={"ids": [again["id"]]})
    assert user.sync(t["id"])["files"] == 3
    # deleting the table leaves the files alone
    user.j("DELETE", f"/tables/{t['id']}")
    t2 = user.table("files", "Same bucket", storage_id=t["storage_id"], storage_prefix=prefix)
    assert user.sync(t2["id"])["pulled"] == 3
    # someone else can't read the files
    other = type(user)()
    assert other.get(API + f"/files/{img['id']}/content").status_code == 404


def test_webdav_files_table_and_outside_changes(user):
    s = user.j("POST", "/storages", json={**DAV, "name": "NAS"})
    assert s["kind"] == "webdav" and s["has_secret"] and "secret" not in s
    prefix = f"p{uuid.uuid4().hex[:8]}"
    t = user.table("files", "NAS files", storage_id=s["id"], storage_prefix=prefix)
    user.sync(t["id"])  # after the first sync (started when the table is made), so the counts below are this test's
    F = user.fields(t["id"])
    base = f"{WEBDAV_URL}shared/{prefix}/"
    # uploaded here: on the server, in a folder
    rec = upload(user, t, ("plan.md", b"# Plan", "text/markdown"), folder="projects/a")[0]
    assert requests.get(base + "projects/a/plan.md", auth=DAV_AUTH).content == b"# Plan"
    assert rec["data"][F["folder"]] == "projects/a"
    # added, changed and removed on the server: picked up by a sync
    requests.request("MKCOL", base + "inbox/", auth=DAV_AUTH)
    assert requests.put(base + "inbox/scan.png", data=PNG, auth=DAV_AUTH).status_code in (200, 201, 204)
    stats = user.sync(t["id"])
    assert stats["pulled"] == 1
    scan = by_name(user, t, F, "scan.png")
    assert scan["data"][F["folder"]] == "inbox" and scan["data"][F["kind"]] == "Image"
    wait(lambda: user.get(API + f"/files/{scan['id']}/thumb").status_code == 200)
    time.sleep(1.1)  # a new modification time
    requests.put(base + "projects/a/plan.md", data=b"# Plan v2", auth=DAV_AUTH)
    assert user.sync(t["id"])["updated"] == 1
    assert user.record(t["id"], rec["id"])["data"][F["size"]] == 9
    requests.delete(base + "inbox/scan.png", auth=DAV_AUTH)
    assert user.sync(t["id"])["removed"] == 1 and by_name(user, t, F, "scan.png") is None
    # a move here is a move there
    user.j("PATCH", f"/records/{rec['id']}", json={"data": {F["folder"]: "done"}})
    assert requests.get(base + "done/plan.md", auth=DAV_AUTH).content == b"# Plan v2"
    assert requests.get(base + "projects/a/plan.md", auth=DAV_AUTH).status_code == 404
    # emptying the trash deletes the file on the server
    user.j("DELETE", f"/records/{rec['id']}")
    assert requests.get(base + "done/plan.md", auth=DAV_AUTH).status_code == 200
    user.j("POST", "/trash/records/delete", json={"ids": [rec["id"]]})
    assert requests.get(base + "done/plan.md", auth=DAV_AUTH).status_code == 404
    # a connection in use can't be deleted; a wrong password is caught before saving
    assert user.request("DELETE", API + f"/storages/{s['id']}").status_code == 400
    r = user.post(API + "/storages/test", json={**DAV, "secret": "wrong"})
    assert r.status_code == 400 and "rejected" in r.text
    assert user.post(API + "/storages/test", json={**S3, "bucket": "nope"}).status_code == 400


def test_linking_files_and_public_links(user):
    t = user.table("files", "Docs", storage=S3, storage_prefix=f"p{uuid.uuid4().hex[:8]}")
    F = user.fields(t["id"])
    doc = upload(user, t, ("contract.pdf", b"%PDF-1.4 not really", "application/pdf"))[0]
    projects = user.table(name="Projects")
    P = user.fields(projects["id"])
    link = user.j("POST", f"/tables/{projects['id']}/fields", json={"name": "Files", "type": "link", "options": {"foreignTableId": t["id"]}})
    look = user.j("POST", f"/tables/{projects['id']}/fields", json={"name": "Previews", "type": "lookup",
                                                                     "options": {"linkFieldId": link["id"], "lookupFieldId": F["file"]}})
    p = user.j("POST", f"/tables/{projects['id']}/records", json={"data": {P["Name"]: "House", link["id"]: [doc["id"]]}})
    got = user.record(projects["id"], p["id"])["data"]
    assert got[look["id"]][0]["id"] == doc["id"] and got[look["id"]][0]["file"] is True
    # the Files table got the two-way link field back
    assert by_name(user, t, F, "contract.pdf")["data"][user.fields(t["id"])["Projects"]] == [p["id"]]
    # a public link shows the files
    view = user.j("GET", f"/tables/{t['id']}")["views"][0]
    s = user.j("POST", "/shares", json={"name": "Docs", "base_id": user.base_id, "items": [{"kind": "table", "id": t["id"], "views": [view["id"]]}]})
    anon = requests.Session()
    r = anon.get(API + f"/public/{s['token']}/files/{doc['id']}/content")
    assert r.status_code == 200 and r.content == b"%PDF-1.4 not really" and r.headers["content-type"] == "application/pdf"
    assert anon.get(API + f"/public/{s['token']}/files/{p['id']}/content").status_code == 404


def test_json_schema(user):
    t = user.table(name="Schema")
    F = user.fields(t["id"])
    user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Email", "type": "email", "required": True})
    user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Size", "type": "single_select", "options": {
        "choices": [{"name": "S", "color": "#000000"}, {"name": "M", "color": "#000000"}], "allowNew": False}, "default_value": "M"})
    user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Total", "type": "formula", "options": {"expression": "1 + 1"}})
    key = user.j("POST", "/keys", json={"name": "k", "scope": "read"})["key"]
    H = {"Authorization": "Bearer " + key}
    root = API.replace("/api", "")
    s = requests.get(root + f"/api/v1/tables/{t['id']}/schema", headers=H).json()
    assert s["$schema"].endswith("2020-12/schema") and s["type"] == "object" and s["required"] == ["Email"]
    p = s["properties"]
    assert p["Email"]["type"] == "string" and p["Email"]["format"] == "email"
    assert p["Size"]["enum"] == ["S", "M"] and p["Size"]["default"] == "M" and p["Size"]["type"] == ["string", "null"]
    assert p["Total"]["readOnly"] and p["Total"]["type"] == ["number", "null"]
    assert p["Status"]["examples"] == ["Todo", "Doing", "Done"] and p["Name"]["x-probyte-primary"]
    by_id = requests.get(root + f"/api/v1/tables/{t['id']}/schema", headers=H, params={"fieldKeyType": "id"}).json()
    assert F["Name"] in by_id["properties"]
    mcp = requests.post(root + "/mcp", headers=H, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "get_json_schema", "arguments": {"table": t["id"]}}}).json()
    assert '"Email"' in mcp["result"]["content"][0]["text"]


def test_document_previews(user):
    """Word / PowerPoint files are converted to PDF (the office service) for viewing, and get a cover image."""
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    t = user.table("files", "Office", storage=S3, storage_prefix=f"p{uuid.uuid4().hex[:8]}")
    with open(os.path.join(here, "fixtures", "report.docx"), "rb") as f:
        doc = upload(user, t, ("report.docx", f.read(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"))[0]
    r = user.get(API + f"/files/{doc['id']}/preview")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    wait(lambda: user.get(API + f"/files/{doc['id']}/thumb").status_code == 200, timeout=60)
    F = user.fields(t["id"])
    wait(lambda: "Revenue grew" in (by_name(user, t, F, "report.docx")["data"].get(F["text"]) or ""))
    txt = upload(user, t, ("a.txt", b"x", "text/plain"))[0]
    assert user.get(API + f"/files/{txt['id']}/preview").status_code == 400


def test_covers_for_text_and_files_added_before(user):
    """Text files get a page-style cover; files without one (e.g. added before the converter) get one at the next sync."""
    t = user.table("files", "Covers", storage=S3, storage_prefix=f"p{uuid.uuid4().hex[:8]}")
    md = upload(user, t, ("notes.md", b"# Title\n\n- one\n- two\n", "text/markdown"))[0]
    wait(lambda: user.get(API + f"/files/{md['id']}/thumb").status_code == 200)
    F = user.fields(t["id"])
    item = user.record(t["id"], md["id"])["data"][F["file"]][0]
    assert item["thumb"] and item["previewed"]
    # a file whose cover was never made (as if added before): the sync makes it
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    doc = upload(user, t, ("old.docx", open(os.path.join(here, "fixtures", "report.docx"), "rb").read(), "application/octet-stream"))[0]
    wait(lambda: user.get(API + f"/files/{doc['id']}/thumb").status_code == 200, timeout=60)


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><circle cx="20" cy="20" r="18" fill="#3b82f6"/>' \
      b'<script>alert(document.cookie)</script></svg>'


def test_svg_is_shown_as_a_picture_safely(user):
    t = user.table("files", "Pictures", storage=S3, storage_prefix=f"p{uuid.uuid4().hex[:8]}")
    F = user.fields(t["id"])
    rec = upload(user, t, ("logo.svg", SVG, "image/svg+xml"))[0]
    item = rec["data"][F["file"]][0]
    assert item["view"] == "image" and rec["data"][F["kind"]] == "Image"
    r = user.get(API + f"/files/{rec['id']}/content")
    assert r.headers["content-type"].startswith("image/svg+xml") and "inline" in r.headers["content-disposition"]
    csp = r.headers["content-security-policy"]
    assert "sandbox" in csp and "script" not in csp and "default-src 'none'" in csp   # the script inside can't run
    # the code is still there for editing
    assert user.get(API + f"/files/{rec['id']}/content", params={"text": 1}).headers["content-type"].startswith("text/plain")
    # no "text page" cover: the picture is its own
    wait(lambda: (user.record(t["id"], rec["id"])["data"][F["file"]][0].get("previewed")))
    assert not user.record(t["id"], rec["id"])["data"][F["file"]][0].get("thumb")
    # attachments too
    a = user.post(API + "/attachments", files={"file": ("icon.svg", SVG, "image/svg+xml")}).json()
    r = user.get(API + f"/attachments/{a['id']}")
    assert r.headers["content-type"].startswith("image/svg+xml") and "sandbox" in r.headers["content-security-policy"]


def test_files_through_a_linked_table(user, other_user):
    """A files table shared by a public link and added on another account / server as a linked table: its files open."""
    import os
    import psycopg
    t = user.table("files", "Shared files", storage=S3, storage_prefix=f"p{uuid.uuid4().hex[:8]}")
    pic, note = upload(user, t, ("pic.png", PNG, "image/png"), ("note.txt", b"0123456789 shared words", "text/plain"))
    wait(lambda: user.get(API + f"/files/{pic['id']}/thumb").status_code == 200)
    view = user.j("GET", f"/tables/{t['id']}")["views"][0]
    s = user.j("POST", "/shares", json={"name": "Files", "base_id": user.base_id, "items": [{"kind": "table", "id": t["id"], "views": [view["id"]]}]})
    link = f"{BASE}/s/{s['token']}"
    lt = other_user.j("POST", "/tables", json={"name": "Their files", "kind": "remote", "base_id": other_user.base_id,
                                               "remote": {"url": link, "view_id": view["id"]}})

    def items():
        info = other_user.j("GET", f"/tables/{lt['id']}")
        fid = next(f["id"] for f in info["fields"] if f["type"] == "attachment")
        return {r["data"][fid][0]["name"]: r["data"][fid][0] for r in info["records"] if r["data"].get(fid)}

    def check():
        got = items()
        a, b = got["pic.png"], got["note.txt"]
        assert a["file"] and a["url"].startswith(f"/api/remote/{lt['id']}/files/")
        root = BASE + a["url"]
        r = other_user.get(root + "/content")
        assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
        assert other_user.get(root + "/thumb").headers["content-type"] == "image/webp"
        r = other_user.get(BASE + b["url"] + "/content", headers={"Range": "bytes=11-16"})
        assert r.status_code == 206 and r.content == b"shared"
        assert other_user.get(BASE + b["url"] + "/content", params={"text": 1}).text.startswith("0123")
        assert other_user.get(BASE + b["url"] + "/content", params={"download": 1}).headers["content-type"] == "application/octet-stream"
        # only the linked table's owner, and only files the link shows
        assert user.get(root + "/content").status_code == 404
        assert other_user.get(f"{BASE}/api/remote/{lt['id']}/files/{uuid.uuid4()}/content").status_code == 404

    check()  # same server: straight from the share
    # as if the link were on another server: through its public address
    db = os.environ.get("BENCH_DB", "postgresql://probyte:probyte-test@db:5432/probyte")
    try:
        conn = psycopg.connect(db)
    except Exception:
        return
    with conn:
        conn.execute("UPDATE tables SET remote = remote || '{\"local\": false}' WHERE id = %s", (lt["id"],))
    check()
