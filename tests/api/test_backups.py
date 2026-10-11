"""Backups: made by the worker, listed and downloaded by the admin only."""
import io
import tarfile
import time

from conftest import API


def test_backups_for_the_admin_only(admin, user):
    assert user.get(API + "/admin/backups").status_code == 403
    assert user.post(API + "/admin/backups").status_code == 403
    info = admin.j("GET", "/admin/backups")
    assert info["enabled"] is True and info["keep"] >= 1
    before = {b["name"] for b in info["backups"]}
    t = user.table(name="Worth keeping")
    F = user.fields(t["id"])
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "precious"}})
    admin.j("POST", "/admin/backups")
    new = None
    for _ in range(60):
        new = next((b for b in admin.j("GET", "/admin/backups")["backups"] if b["name"] not in before), None)
        if new:
            break
        time.sleep(0.5)
    assert new, "the worker made no backup"
    assert new["counts"]["records"] >= 1 and new["sizes"]["database"] > 1000 and new["calendars_included"]
    r = admin.get(API + f"/admin/backups/{new['name']}/download")
    assert r.status_code == 200
    names = tarfile.open(fileobj=io.BytesIO(r.content)).getnames()
    assert {f"{new['name']}/{f}" for f in ("database.dump", "calendars.tar.gz", "attachments.tar.gz", "manifest.json")} <= set(names)
    assert admin.get(API + "/admin/backups/../../etc/passwd/download").status_code == 404
    assert user.get(API + f"/admin/backups/{new['name']}/download").status_code == 403
