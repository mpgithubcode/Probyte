"""Blueprint takeoff (extension): the marked-up drawings and the takeoff saved next to the drawing set, with its
folder and fields."""
import os
import uuid

import fitz
import pytest

from conftest import API, S3_URL, Client
from drawing_set import make

X = API + "/x/takeoff"
S3 = {"kind": "s3", "endpoint": S3_URL, "bucket": "test-bucket", "username": "testkey", "secret": "testsecret12345"}


@pytest.fixture(scope="module")
def admin():
    name, pw = os.environ["PROBYTE_ADMIN"].split(":")
    a = Client(name, pw)
    a.j("PUT", "/extensions/takeoff", json={"enabled": True})
    yield a
    a.j("PUT", "/extensions/takeoff", json={"enabled": False})


def test_saved_files_inherit_the_drawings_folder_and_fields(admin, tmp_path):
    u = Client()
    drive = u.table("files", "Plans", storage=S3, storage_prefix=f"sv{uuid.uuid4().hex[:6]}")
    tags = u.j("POST", f"/tables/{drive['id']}/fields", json={"name": "Tags", "type": "multi_select", "options": {
        "choices": [{"name": "Bid", "color": "#e5484d"}, {"name": "Rev B", "color": "#3e63dd"}]}})
    job = u.j("POST", f"/tables/{drive['id']}/fields", json={"name": "Job", "type": "text"})
    pdf = make(str(tmp_path / "Job 7.pdf"))
    rec = u.post(API + f"/tables/{drive['id']}/files", files=[("files", ("Job 7.pdf", open(pdf, "rb"), "application/pdf"))],
                 data={"folder": "Jobs/7"}).json()["records"][0]
    u.j("PATCH", f"/records/{rec['id']}", json={"data": {tags["id"]: ["Bid", "Rev B"], job["id"]: "Mill St"}})
    p = u.j("POST", "/x/takeoff/projects/from-file", json={"record_id": rec["id"], "name": "Job 7"})
    it = u.j("POST", f"/x/takeoff/projects/{p['id']}/items", json={"items": [{"name": "Outlet", "kind": "count"}]})[0]
    u.j("POST", f"/x/takeoff/projects/{p['id']}/marks", json={"item_id": it["id"], "page_no": 1, "geom": {"type": "point", "points": [[300, 300]]}})

    # by default: the folder and every field added to the table
    st = u.j("GET", "/x/takeoff/settings")
    assert st["inherit"] is None and {"Folder", "Tags", "Job"} <= set(st["fields"]) and "Size" not in st["fields"]
    out = u.j("POST", f"/x/takeoff/projects/{p['id']}/export/files", json={"what": "pdf"})
    F = u.fields(drive["id"])
    new = out["record"]
    assert out["table_id"] == drive["id"] and new["data"][F["name"]] == "Job-7-marked-up.pdf"
    assert new["data"][F["folder"]] == "Jobs/7"
    assert sorted(new["data"][tags["id"]]) == ["Bid", "Rev B"] and new["data"][job["id"]] == "Mill St"
    got = u.get(API + f"/files/{new['id']}/content")
    assert got.status_code == 200 and fitz.open(stream=got.content, filetype="pdf").page_count >= 1

    # only the tags: the takeoff goes to the top of the table, without the job
    assert u.j("PUT", "/x/takeoff/settings", json={"inherit": ["Tags"]})["inherit"] == ["Tags"]
    new = u.j("POST", f"/x/takeoff/projects/{p['id']}/export/files", json={"what": "csv"})["record"]
    assert new["data"][F["name"]] == "Job-7-takeoff.csv" and not new["data"].get(F["folder"])
    assert sorted(new["data"][tags["id"]]) == ["Bid", "Rev B"] and not new["data"].get(job["id"])
    assert "Outlet" in u.get(API + f"/files/{new['id']}/content").content.decode("utf-8-sig")

    # a takeoff from an uploaded PDF has nowhere to save it
    up = u.post(X + "/projects", files={"file": ("x.pdf", open(pdf, "rb"), "application/pdf")}).json()
    assert u.post(X + f"/projects/{up['id']}/export/files", json={"what": "pdf"}).status_code == 400
    # nor can someone else's takeoff be saved
    assert Client().post(X + f"/projects/{p['id']}/export/files", json={"what": "pdf"}).status_code == 404
