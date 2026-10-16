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
    assert {f"{new['name']}/{f}" for f in ("database.dump", "attachments.tar.gz", "manifest.json")} <= set(names)
    assert admin.get(API + "/admin/backups/../../etc/passwd/download").status_code == 404
    assert user.get(API + f"/admin/backups/{new['name']}/download").status_code == 403


def test_each_backup_is_copied_off_the_server(admin, user):
    """Backups → Off this server: each new backup goes to one of the admin's storages (here S3), in its own folder,
    the newest kept there; how the last copy went is shown."""
    import uuid
    from test_files_tables import S3
    assert user.get(API + "/admin/backups/copy").status_code == 403
    s = admin.j("POST", "/storages", json={**S3, "name": f"Offsite {uuid.uuid4().hex[:6]}"})
    prefix = f"bk{uuid.uuid4().hex[:8]}"
    cfg = admin.j("PUT", "/admin/backups/copy", json={"storage_id": s["id"], "prefix": prefix, "keep": 1})
    assert cfg["settings"]["prefix"] == prefix + "/" and cfg["settings"]["keep"] == 1
    for _ in range(2):   # two backups: only the newest stays there
        before = {b["name"] for b in admin.j("GET", "/admin/backups")["backups"]}
        admin.j("POST", "/admin/backups")
        for _ in range(120):
            st = admin.j("GET", "/admin/backups/copy")["status"]
            if st and st["name"] not in before and st["ok"]:
                break
            time.sleep(0.5)
        assert st and st["ok"] and st["name"] not in before, st
    # what's in the bucket: seen through a files table on the same folder
    t = admin.table("files", "Offsite copies", storage_id=s["id"], storage_prefix=prefix)
    admin.sync(t["id"])
    F = admin.fields(t["id"])
    names = sorted(f"{r['data'].get(F['folder'], '')}/{r['data'][F['name']]}" for r in admin.records(t["id"]))
    assert names == [f"{st['name']}/attachments.tar.gz", f"{st['name']}/database.dump", f"{st['name']}/manifest.json"], names
    admin.j("PUT", "/admin/backups/copy", json={"storage_id": None})
    assert admin.j("GET", "/admin/backups/copy")["settings"] is None
