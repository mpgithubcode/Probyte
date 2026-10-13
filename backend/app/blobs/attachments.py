"""Files for attachment fields. Bytes live on disk (a Docker volume); metadata in the attachments table."""
import mimetypes
import os
import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse

ATTACH_DIR = Path(os.environ.get("ATTACHMENTS_DIR", "/data/attachments"))
MAX_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024
# Only plain raster images are shown inline; everything else (HTML, SVG, PDF…) downloads, so an
# uploaded file can never run script on Probyte's origin.
INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp", "image/svg+xml"}
# SVG can hold script: it's shown inline only under a sandbox policy, so nothing in it runs (in an <img> it never does)
SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; sandbox"


def _clean_name(name: str | None) -> str:
    name = os.path.basename(name or "").strip() or "file"
    return re.sub(r"[\x00-\x1f\\/]", "_", name)[:200]


async def save_upload(conn, user_id, file: UploadFile) -> dict:
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)
    aid = uuid.uuid4()
    name = _clean_name(file.filename)
    mime = (file.content_type or mimetypes.guess_type(name)[0] or "application/octet-stream").split(";")[0].strip().lower()
    path, size = ATTACH_DIR / str(aid), 0
    try:
        with path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, f"File is larger than {MAX_BYTES // (1024 * 1024)} MB")
                out.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    conn.execute("INSERT INTO attachments (id, user_id, name, mime, size) VALUES (%s,%s,%s,%s,%s)",
                 (aid, user_id, name, mime, size))
    return {"id": str(aid), "name": name, "mime": mime, "size": size}


def serve(conn, user_id, attachment_id, download: bool):
    """An uploaded file, for whoever uploaded it or can see a record it's in (a base shared with them, too)."""
    a = conn.execute(
        "SELECT a.* FROM attachments a WHERE a.id = %s AND (a.user_id = %s OR EXISTS (SELECT 1 FROM records r JOIN tables t ON "
        "t.id = r.table_id WHERE (t.user_id = %s OR t.base_id IN (SELECT base_id FROM base_members WHERE user_id = %s)) "
        "AND jsonb_path_exists(r.data, '$.*[*] ? (@.id == $id)', jsonb_build_object('id', a.id::text))))",
        (attachment_id, user_id, user_id, user_id)).fetchone()
    path = ATTACH_DIR / str(a["id"]) if a else None
    if not a or not path.exists():
        raise HTTPException(404, "File not found")
    inline = a["mime"] in INLINE_TYPES and not download
    return FileResponse(
        path,
        media_type=a["mime"] if inline else "application/octet-stream",
        filename=a["name"],
        content_disposition_type="inline" if inline else "attachment",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": SVG_CSP if a["mime"] == "image/svg+xml" else "default-src 'none'; img-src 'self'; sandbox",
            "Cache-Control": "private, max-age=86400",
        },
    )


def collect_garbage(conn, grace_hours: int = 1) -> int:
    """Delete uploads no record refers to any more (older than the grace period, so files that were
    just uploaded and not yet saved into a record are left alone). Returns how many were removed."""
    rows = conn.execute(
        """
        SELECT a.id FROM attachments a
        WHERE a.created_at < now() - make_interval(hours => %s)
          AND NOT EXISTS (
              SELECT 1 FROM records r JOIN tables t ON t.id = r.table_id
              WHERE (t.user_id = a.user_id OR t.base_id IN (SELECT base_id FROM base_members m WHERE m.user_id = a.user_id))
                AND jsonb_path_exists(r.data, '$.*[*] ? (@.id == $id)', jsonb_build_object('id', a.id::text)))
        """,
        (grace_hours,),
    ).fetchall()
    for r in rows:
        (ATTACH_DIR / str(r["id"])).unlink(missing_ok=True)
        conn.execute("DELETE FROM attachments WHERE id = %s", (r["id"],))
    return len(rows)


def collect_garbage_job(_payload=None):
    """Worker job: remove uploaded files no record uses any more."""
    import logging
    from app.core.db import connection
    with connection() as conn:
        n = collect_garbage(conn)
    if n:
        logging.getLogger("probyte.attachments").info("removed %d unused attachment(s)", n)
