"""REST API v1: files sent straight into a record's field (a link to a files table, an attachment field, a file's own
File field), with an API key."""
import uuid

import requests

from conftest import API, BASE, S3_URL

V1 = BASE + "/api/v1"
S3 = {"kind": "s3", "endpoint": S3_URL, "bucket": "test-bucket", "username": "testkey", "secret": "testsecret12345"}
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82")


def hdr(user, scope="write"):
    return {"Authorization": "Bearer " + user.j("POST", "/keys", json={"name": f"k {scope}", "scope": scope})["key"]}


def test_uploading_into_a_link_to_a_files_table_and_an_attachment_field(user):
    W = hdr(user)
    drive = user.table("files", "Drive", storage=S3, storage_prefix=f"up{uuid.uuid4().hex[:6]}")
    jobs = user.table(name="Jobs")
    J = user.fields(jobs["id"])
    user.j("POST", f"/tables/{jobs['id']}/fields", json={"name": "Drawings", "type": "link", "options": {"foreignTableId": drive["id"]}})
    user.j("POST", f"/tables/{jobs['id']}/fields", json={"name": "Photos", "type": "attachment"})
    user.j("POST", f"/tables/{jobs['id']}/fields", json={"name": "Cover sheet", "type": "link",
                                                         "options": {"foreignTableId": drive["id"], "relationship": "one"}})
    rec = user.j("POST", f"/tables/{jobs['id']}/records", json={"data": {J["Name"]: "Mill St"}})
    url = f"{V1}/tables/Jobs/records/{rec['id']}/fields"

    # a link to the files table: uploaded into it (into the folder) and linked, two at once
    r = requests.post(f"{url}/Drawings/files", headers=W, data={"folder": "Jobs/Mill St"},
                      files=[("files", ("plan.pdf", b"%PDF-1.4 plan", "application/pdf")), ("files", ("notes.txt", b"walls", "text/plain"))])
    assert r.status_code == 200, r.text
    linked = r.json()["fields"]["Drawings"]
    assert len(linked) == 2
    D = user.fields(drive["id"])
    by_name = {x["data"][D["name"]]: x for x in user.records(drive["id"])}
    assert set(by_name) == {"plan.pdf", "notes.txt"} and by_name["plan.pdf"]["data"][D["folder"]] == "Jobs/Mill St"
    assert set(linked) == {by_name["plan.pdf"]["id"], by_name["notes.txt"]["id"]}
    assert user.get(API + f"/files/{by_name['plan.pdf']['id']}/content").content == b"%PDF-1.4 plan"     # in the storage
    # more are added; replace=true starts over
    r = requests.post(f"{url}/Drawings/files", headers=W, files=[("files", ("rev-b.pdf", b"%PDF-1.4 b", "application/pdf"))])
    assert len(r.json()["fields"]["Drawings"]) == 3
    r = requests.post(f"{url}/Drawings/files", headers=W, data={"replace": "true"}, files=[("files", ("rev-c.pdf", b"%PDF c", "application/pdf"))])
    assert len(r.json()["fields"]["Drawings"]) == 1
    # a link to one record takes one file
    r = requests.post(f"{url}/Cover sheet/files", headers=W, files=[("files", ("a.pdf", b"%PDF a", "application/pdf")), ("files", ("b.pdf", b"%PDF b", "application/pdf"))])
    assert r.status_code == 400
    r = requests.post(f"{url}/Cover sheet/files", headers=W, files=[("files", ("cover.pdf", b"%PDF cover", "application/pdf"))])
    assert r.status_code == 200 and len(r.json()["fields"]["Cover sheet"]) == 1

    # an attachment field
    r = requests.post(f"{url}/Photos/files", headers=W, files=[("files", ("site.png", PNG, "image/png"))])
    assert r.status_code == 200, r.text
    photo = r.json()["fields"]["Photos"][0]
    assert photo["name"] == "site.png" and photo["size"] == len(PNG)
    assert user.get(API + f"/attachments/{photo['id']}").content == PNG

    # a file's own File field: new contents
    plan = by_name["plan.pdf"]["id"]
    r = requests.post(f"{V1}/tables/Drive/records/{plan}/fields/File/files", headers=W, files=[("files", ("plan.pdf", b"%PDF-1.4 v2", "application/pdf"))])
    assert r.status_code == 200, r.text
    assert user.get(API + f"/files/{plan}/content").content == b"%PDF-1.4 v2"

    # not a field for files, a read-only key, another user's record
    assert requests.post(f"{url}/Name/files", headers=W, files=[("files", ("x.txt", b"x", "text/plain"))]).status_code == 400
    assert requests.post(f"{url}/Photos/files", headers=hdr(user, "read"), files=[("files", ("x.png", PNG, "image/png"))]).status_code == 403
    from conftest import Client
    assert requests.post(f"{V1}/tables/{jobs['id']}/records/{rec['id']}/fields/Photos/files", headers=hdr(Client()),
                         files=[("files", ("x.png", PNG, "image/png"))]).status_code == 404
