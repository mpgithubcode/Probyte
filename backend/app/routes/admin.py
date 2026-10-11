"""Server administration in the app (the admin account only): backups."""
import io
import tarfile

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .. import backup, jobs
from .common import User

router = APIRouter()


def _admin(user):
    if not user.get("is_admin") or user.get("api_key"):
        raise HTTPException(403, "Only the server's admin account can do this")


@router.get("/api/admin/backups")
def list_backups(user=User):
    _admin(user)
    return {"enabled": backup.enabled(), "interval_hours": backup.INTERVAL_HOURS, "keep": backup.KEEP,
            "dir": str(backup.BACKUP_DIR), "backups": backup.list_backups()}


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
