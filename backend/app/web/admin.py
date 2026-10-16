"""Server administration in the app (the admin account only): backups."""
import io
import tarfile

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from fastapi.responses import StreamingResponse

from app.core import jobs
from app.core.db import connection
from app.ops import backup
from app.web.common import User

router = APIRouter()


def _admin(user):
    if not user.get("is_admin") or user.get("api_key"):
        raise HTTPException(403, "Only the server's admin account can do this")


@router.get("/api/admin/backups")
def list_backups(user=User):
    _admin(user)
    return {"enabled": backup.enabled(), "interval_hours": backup.INTERVAL_HOURS, "keep": backup.KEEP,
            "dir": str(backup.BACKUP_DIR), "backups": backup.list_backups()}


class BackupCopyIn(BaseModel):
    storage_id: str | None = None     # none: no copy
    prefix: str = "probyte-backups"
    keep: int = 14


@router.get("/api/admin/backups/copy")
def get_backup_copy(user=User):
    """Where each backup is copied off this server's disk (one of the admin's storages), and how the last copy went."""
    _admin(user)
    with connection() as conn:
        storages = [{"id": str(r["id"]), "name": r["name"], "kind": r["kind"]} for r in conn.execute(
            "SELECT id, name, kind FROM storages WHERE user_id = %s ORDER BY name", (user["id"],)).fetchall()]
        return {"settings": backup.copy_settings(conn), "status": backup.copy_status(conn), "storages": storages}


@router.put("/api/admin/backups/copy")
def set_backup_copy(body: BackupCopyIn, user=User):
    _admin(user)
    with connection() as conn:
        if body.storage_id and not conn.execute("SELECT 1 FROM storages WHERE id = %s AND user_id = %s",
                                                (body.storage_id, user["id"])).fetchone():
            raise HTTPException(400, "Choose one of your storages")
        backup.save_copy_settings(conn, body.storage_id, body.prefix, body.keep)
    return get_backup_copy(user)


@router.post("/api/admin/backups/copy")
def copy_latest_now(user=User):
    """Copy the newest backup now (to test the setting)."""
    _admin(user)
    latest = backup.list_backups()[:1]
    if not latest:
        raise HTTPException(400, "There's no backup yet to copy")
    with connection() as conn:
        if not backup.copy_settings(conn):
            raise HTTPException(400, "Choose where backups are copied first")
        jobs.enqueue(conn, "backup_copy", {"name": latest[0]["name"]}, key="backup_copy", max_attempts=1)
    return {"ok": True, "name": latest[0]["name"]}


@router.post("/api/admin/backups")
def back_up_now(user=User):
    """Ask the worker for a backup now (it appears in the list when done)."""
    _admin(user)
    if not backup.enabled():
        raise HTTPException(400, f"Backups are off: mount a volume at {backup.BACKUP_DIR}")
    jobs.enqueue(None, "backup", key="backup")
    return {"ok": True}


@router.get("/api/admin/backups/{name}/download")
def download_backup(name: str, user=User):
    """One backup as a .tar file, to keep a copy somewhere else."""
    _admin(user)
    match = next((b for b in backup.list_backups() if b["name"] == name), None)
    if not match:
        raise HTTPException(404, "No such backup")
    folder = backup.BACKUP_DIR / name

    def stream():
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w|") as tar:  # streamed: no copy of the whole backup in memory
            for f in sorted(folder.iterdir()):
                tar.add(f, arcname=f"{name}/{f.name}")
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()
        yield buf.getvalue()

    return StreamingResponse(stream(), media_type="application/x-tar",
                             headers={"Content-Disposition": f'attachment; filename="{name}.tar"'})
