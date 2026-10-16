"""Backups: the database, the calendars and contacts (Radicale's data) and uploaded files, in one folder per backup.

The worker makes one whenever the newest is older than BACKUP_INTERVAL hours (and on "Back up now" in Settings),
keeping the newest BACKUP_KEEP. Each backup is a folder in BACKUP_DIR:

    probyte-20261011-030000/
        database.dump        pg_dump, custom format
        calendars.tar.gz     Radicale's collections
        attachments.tar.gz   uploaded files
        manifest.json        when, sizes, database migrations

Point BACKUP_DIR at a disk that isn't the server's own, or set up a copy to a storage (S3, WebDAV; see the end of
this file), to survive a lost server.
The encryption key for stored passwords (secretbox.py) is not included: keep it with your .env.
Restore with `python -m app.admin restore <name>` while the app is stopped (see README).
"""
import json
import logging
import os
import shutil
import subprocess
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

from app.blobs.attachments import ATTACH_DIR
from app.core import jobs
from app.core.db import connection, DATABASE_URL

log = logging.getLogger("probyte.backup")

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/backups"))
INTERVAL_HOURS = float(os.environ.get("BACKUP_INTERVAL", "24"))  # 0 = no automatic backups
KEEP = max(1, int(os.environ.get("BACKUP_KEEP", "14")))
RADICALE_DATA = Path(os.environ.get("RADICALE_DATA_DIR", "/radicale-data"))  # restoring a backup from before 0.9 (calendars were there)
PREFIX = "probyte-"


def enabled() -> bool:
    return BACKUP_DIR.is_dir()


def _manifest(path: Path) -> dict | None:
    try:
        return json.loads((path / "manifest.json").read_text())
    except (OSError, ValueError):
        return None


def list_backups() -> list[dict]:
    """Finished backups, newest first."""
    if not enabled():
        return []
    out = []
    for p in BACKUP_DIR.iterdir():
        m = _manifest(p) if p.is_dir() and p.name.startswith(PREFIX) else None
        if m:
            out.append({"name": p.name, **m})
    return sorted(out, key=lambda b: b["created_at"], reverse=True)


def _tar(src: Path, dest: Path) -> int:
    with tarfile.open(dest, "w:gz", compresslevel=6) as tar:
        if src.is_dir():
            for child in sorted(src.iterdir()):
                tar.add(child, arcname=child.name)
    return dest.stat().st_size


def run_backup() -> dict:
    """Make a backup now; returns its manifest."""
    if not enabled():
        raise RuntimeError(f"Backups are off: {BACKUP_DIR} doesn't exist (mount a volume there)")
    started = time.time()
    name = PREFIX + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    tmp = BACKUP_DIR / f".{name}.partial"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        subprocess.run(["pg_dump", "--format=custom", "--no-owner", "--file", str(tmp / "database.dump"), DATABASE_URL],
                       check=True, capture_output=True, timeout=3600)
        sizes = {"database": (tmp / "database.dump").stat().st_size,
                 "attachments": _tar(ATTACH_DIR, tmp / "attachments.tar.gz")}
        with connection() as conn:
            migrations = [r["version"] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()]
            counts = conn.execute("SELECT (SELECT count(*) FROM users) AS users, (SELECT count(*) FROM tables) AS tables, "
                                  "(SELECT count(*) FROM records) AS records").fetchone()
        manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "seconds": round(time.time() - started, 1),
                    "sizes": sizes, "total": sum(sizes.values()), "migrations": migrations, "counts": dict(counts),
                    "calendars_included": True}  # in the database (Probyte serves them itself)
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2))
        tmp.rename(BACKUP_DIR / name)  # only complete backups get a real name
    except subprocess.CalledProcessError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"pg_dump failed: {e.stderr.decode(errors='replace')[-500:]}")
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    prune()
    log.info("backup %s done in %.1fs (%d bytes)", name, manifest["seconds"], manifest["total"])
    with connection() as conn:
        if copy_settings(conn):   # and a copy off this disk, when one is set up
            jobs.enqueue(conn, "backup_copy", {"name": name}, key="backup_copy", max_attempts=4)
    return {"name": name, **manifest}


def prune():
    """Keep the newest KEEP backups; also clear leftovers of interrupted ones."""
    for old in list_backups()[KEEP:]:
        shutil.rmtree(BACKUP_DIR / old["name"], ignore_errors=True)
    for p in BACKUP_DIR.glob(f".{PREFIX}*.partial"):
        if time.time() - p.stat().st_mtime > 6 * 3600:
            shutil.rmtree(p, ignore_errors=True)


def backup_if_due():
    """Run by the worker every hour: back up when the newest backup is older than the interval."""
    if INTERVAL_HOURS <= 0 or not enabled():
        return
    last = list_backups()[:1]
    if last and (datetime.now(timezone.utc) - datetime.fromisoformat(last[0]["created_at"])).total_seconds() < INTERVAL_HOURS * 3600 - 300:
        return
    jobs.enqueue(None, "backup", key="backup")


@jobs.handler("backup")
def _job_backup(_payload):
    run_backup()


def restore(name: str):
    """Put a backup back: the database (replacing what's there), the calendars and the uploaded files.
    Stop the app, worker and Radicale first (see README)."""
    src = BACKUP_DIR / name
    if not _manifest(src):
        raise SystemExit(f"No finished backup called {name!r} in {BACKUP_DIR}")
    # pg_restore writes the restore as SQL; psql runs it in one transaction that stops at the first error (all or
    # nothing). A newer pg_restore sets transaction_timeout, which a Postgres 16 server doesn't know: leave it out.
    dump = subprocess.run(["pg_restore", "--clean", "--if-exists", "--no-owner", "--file", "-", str(src / "database.dump")],
                          capture_output=True, check=True)
    sql = b"\n".join(ln for ln in dump.stdout.split(b"\n") if not ln.startswith(b"SET transaction_timeout"))
    r = subprocess.run(["psql", "--quiet", "--no-psqlrc", "--single-transaction", "-v", "ON_ERROR_STOP=1", DATABASE_URL],
                       input=sql, capture_output=True)
    if r.returncode:
        raise SystemExit("Restoring the database failed (nothing was changed):\n" + r.stderr.decode(errors="replace")[-2000:])
    for archive, dest in (("calendars.tar.gz", RADICALE_DATA), ("attachments.tar.gz", ATTACH_DIR)):
        if not (src / archive).exists():
            continue
        dest.mkdir(parents=True, exist_ok=True)
        for child in dest.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        with tarfile.open(src / archive) as tar:
            tar.extractall(dest, filter="data")


# ======================================================================== a copy of each backup off this disk
#
# Backups in BACKUP_DIR are on the server's own disk: a lost disk takes them with the data. With a copy set up
# (Settings → Backups), each new backup is also uploaded to one of the admin's storages (an S3 bucket, a WebDAV
# folder), into <prefix><backup name>/, and the newest `keep` there are kept.

COPY_KEY, COPY_STATUS = "backup_copy", "backup_copy_status"


def _setting(conn, key):
    row = conn.execute("SELECT value FROM app_settings WHERE key = %s", (key,)).fetchone()
    try:
        return json.loads(row["value"]) if row else None
    except ValueError:
        return None


def copy_settings(conn) -> dict | None:
    """{"storage_id", "prefix", "keep"} or None."""
    s = _setting(conn, COPY_KEY)
    return s if s and s.get("storage_id") else None


def copy_status(conn) -> dict | None:
    """The last copy: {"name", "at", "ok", "error", "seconds"}."""
    return _setting(conn, COPY_STATUS)


def save_copy_settings(conn, storage_id: str | None, prefix: str, keep: int):
    if not storage_id:
        conn.execute("DELETE FROM app_settings WHERE key = %s", (COPY_KEY,))
        return None
    from app.blobs.filestore import norm_prefix
    value = {"storage_id": str(storage_id), "prefix": norm_prefix(prefix or "probyte-backups"), "keep": max(1, min(int(keep or KEEP), 365))}
    conn.execute("INSERT INTO app_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                 (COPY_KEY, json.dumps(value)))
    return value


def _copy_driver(conn, cfg):
    from app.blobs.filestore import driver
    st = conn.execute("SELECT * FROM storages WHERE id = %s", (cfg["storage_id"],)).fetchone()
    return driver(dict(st) if st else None, cfg["prefix"])


def copy_backup(name: str) -> dict:
    """Upload one backup's files to the copy's storage, then keep only the newest there."""
    with connection() as conn:
        cfg = copy_settings(conn)
        if not cfg:
            return {}
        drv = _copy_driver(conn, cfg)
    folder = BACKUP_DIR / name
    if not folder.is_dir():
        raise RuntimeError(f"No backup {name} to copy")
    started, status = time.time(), {"name": name, "at": datetime.now(timezone.utc).isoformat(), "ok": False}
    try:
        files = sorted(folder.iterdir(), key=lambda f: f.name == "manifest.json")   # the manifest last: a complete copy has one
        for f in files:
            with f.open("rb") as fh:
                drv.put(f"{name}/{f.name}", fh, "application/json" if f.suffix == ".json" else "application/octet-stream")
        # the newest `keep` complete copies stay (each one's folder named probyte-<date>)
        names = sorted({o["path"].split("/")[0] for o in drv.list() if o["path"].startswith(PREFIX) and "/" in o["path"]}, reverse=True)
        for old in names[cfg["keep"]:]:
            for o in [o for o in drv.list() if o["path"].startswith(old + "/")]:
                drv.delete(o["path"])
        status.update(ok=True, seconds=round(time.time() - started, 1), copies=min(len(names), cfg["keep"]))
    except Exception as e:
        status.update(error=str(e)[:500])
        raise
    finally:
        with connection() as conn:
            conn.execute("INSERT INTO app_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                         (COPY_STATUS, json.dumps(status)))
    log.info("backup %s copied off the server in %.1fs", name, status.get("seconds", 0))
    return status


@jobs.handler("backup_copy")
def _job_backup_copy(payload):
    copy_backup(payload["name"])
