"""Covers and previews of files, made by the worker (job "file_preview"): a small image for images, PDFs, office
documents and text, the first part of a text file in the Text field (for search), PDFs of Word / PowerPoint files
(the document converter) and JPEGs of iPhone photos (HEIC). Kept on the attachments volume (thumbs/, previews/).

Covers are drawn in a process of their own with a time limit (render.py), at most PREVIEW_CONCURRENCY at once, so a
file that's slow to draw never holds up the web app, syncs or webhooks. Each version of a file is tried once (a
temporary failure, such as the storage or the converter not answering, up to PREVIEW_TRIES times); a file the storage
lists but can't find is marked "missing" rather than asked for again."""
import io
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException

from app.blobs.attachments import ATTACH_DIR
from app.blobs.filestore import StorageError
from app.core import jobs
from app.core.db import connection, Jsonb
from app.data import store
from app.files.kinds import _ext, _heic, _keys, _RASTER, convertible, is_svg, is_text, previewable
from app.files.storages import _storage

log = logging.getLogger("probyte.files")

THUMB_DIR = ATTACH_DIR / "thumbs"
PREVIEW_DIR = ATTACH_DIR / "previews"      # Word / PowerPoint / OpenDocument files converted to PDF for viewing
CONVERTER = os.environ.get("OFFICE_CONVERTER_URL", "").strip().rstrip("/")
CONVERT_MAX = 100 * 1024 * 1024
THUMB_PX = 480
# files tried by an earlier version of this (or before the converter was set up) and left without a cover are tried
# again once: 2 = a converter that couldn't be reached no longer counts as tried
PREVIEW_VERSION = 2
TEXT_CHARS = 20_000           # how much of a text file goes into the Text field (for search)
PREVIEW_TIMEOUT = int(os.environ.get("PREVIEW_TIMEOUT", "60"))          # seconds a cover may take to draw
PREVIEW_CONCURRENCY = int(os.environ.get("PREVIEW_CONCURRENCY", "2"))  # covers made at once (the rest of the worker stays free)
PREVIEW_TRIES = 5             # a cover that fails for a temporary reason (storage, converter) is tried this often per version
_APP_ROOT = Path(__file__).resolve().parents[2]   # where `python -m app.files.render` runs


def _drop_cache(rid):
    (THUMB_DIR / f"{rid}.webp").unlink(missing_ok=True)
    for p in PREVIEW_DIR.glob(f"{rid}.*.pdf"):
        p.unlink(missing_ok=True)


def _queue_preview(conn, rid, mime, path):
    if previewable(mime, path):
        jobs.enqueue(conn, "file_preview", {"record_id": str(rid)}, key=f"preview:{rid}", delay=0.5, max_attempts=2)


def _save_thumb(img, rid):
    from PIL import ImageOps
    img = ImageOps.exif_transpose(img)
    img.thumbnail((THUMB_PX, THUMB_PX))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    tmp = THUMB_DIR / f".{rid}.webp"
    img.save(tmp, "WEBP", quality=78)
    tmp.replace(THUMB_DIR / f"{rid}.webp")


def preview_pdf(drv, rec, item) -> "os.PathLike":
    """The file as PDF (made by the document converter once per version, then kept)."""
    import requests
    if not CONVERTER:
        raise HTTPException(501, "Previews of Word and PowerPoint files need the document converter (OFFICE_CONVERTER_URL)")
    name = item.get("name") or "file"
    if not convertible(name):
        raise HTTPException(400, "This kind of file isn't converted for previews")
    out = PREVIEW_DIR / f"{rec['id']}.{re.sub(r'[^A-Za-z0-9]', '', rec['caldav_etag'] or 'x')[:16]}.pdf"
    if out.exists():
        return out
    size = item.get("size") or 0
    if size > CONVERT_MAX:
        raise HTTPException(413, "This file is too large to preview; download it instead")
    try:
        data = drv.read(rec["caldav_href"], size + 1)
    except FileNotFoundError:
        raise HTTPException(404, "The file isn't in the storage any more")
    except StorageError as e:
        raise HTTPException(502, str(e))
    try:
        r = requests.post(f"{CONVERTER}/convert", params={"name": name}, data=data, timeout=(5, 180))
    except requests.RequestException:
        raise HTTPException(502, "The document converter isn't reachable")
    if r.status_code != 200 or not r.content.startswith(b"%PDF"):
        raise HTTPException(422, "This document couldn't be converted for a preview; download it instead")
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    for old in PREVIEW_DIR.glob(f"{rec['id']}.*.pdf"):
        old.unlink(missing_ok=True)
    tmp = PREVIEW_DIR / f".{rec['id']}.pdf"
    tmp.write_bytes(r.content)
    tmp.replace(out)
    return out


def _pdf_thumb(pdf_path, rid):
    """The first page as the cover, without its blank margins (so a small table or a letter's text shows larger)."""
    from PIL import Image, ImageChops
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["pdftoppm", "-f", "1", "-l", "1", "-scale-to", str(THUMB_PX * 3), "-png", str(pdf_path), os.path.join(d, "p")],
                       check=True, capture_output=True, timeout=60)
        page = next((os.path.join(d, x) for x in os.listdir(d) if x.endswith(".png")), None)
        if not page:
            return False
        with Image.open(page) as img:
            img = img.convert("RGB")
            box = ImageChops.difference(img, Image.new("RGB", img.size, (255, 255, 255))).point(lambda v: 255 if v > 12 else 0).getbbox()
            if box:
                pad = int(img.width * 0.04)
                l, t, r, b = max(0, box[0] - pad), max(0, box[1] - pad), min(img.width, box[2] + pad), min(img.height, box[3] + pad)
                w = r - l
                b = min(b, t + int(w * 1.4))  # at most a page's shape: a long document shows its top
                if w < img.width * 0.5:        # very little on the page: keep some room around it
                    r = min(img.width, l + int(img.width * 0.5))
                img = img.crop((l, t, r, b))
            _save_thumb(img, rid)
        return True


def heic_jpeg(drv, rec, item) -> "os.PathLike":
    """An iPhone photo (HEIC) as JPEG, up to 2400 px, made once per version."""
    from PIL import Image, ImageOps
    import pillow_heif
    pillow_heif.register_heif_opener()
    out = PREVIEW_DIR / f"{rec['id']}.{re.sub(r'[^A-Za-z0-9]', '', rec['caldav_etag'] or 'x')[:16]}.jpg"
    if out.exists():
        return out
    size = item.get("size") or 0
    if size > 60 * 1024 * 1024:
        raise HTTPException(413, "This photo is too large to preview; download it instead")
    with Image.open(io.BytesIO(drv.read(rec["caldav_href"], size + 1))) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((2400, 2400))
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        for old in PREVIEW_DIR.glob(f"{rec['id']}.*.jpg"):
            old.unlink(missing_ok=True)
        tmp = PREVIEW_DIR / f".{rec['id']}.jpg"
        img.save(tmp, "JPEG", quality=85)
        tmp.replace(out)
    return out


def _render(job: dict) -> dict:
    """Draw a cover in a process of its own (render.py), stopped after PREVIEW_TIMEOUT: whatever a file does to the
    drawing code, it can't hold up the web app or the worker's other jobs (they share this process's CPU)."""
    try:
        r = subprocess.run([sys.executable, "-m", "app.files.render", json.dumps(job)], cwd=_APP_ROOT, capture_output=True,
                           timeout=PREVIEW_TIMEOUT)
    except subprocess.TimeoutExpired:   # (the process is killed)
        raise HTTPException(422, f"The file took too long to preview (over {PREVIEW_TIMEOUT} s)")
    if r.returncode != 0:
        why = (r.stderr.decode("utf-8", "replace").strip().splitlines() or ["?"])[-1]
        raise ValueError(why)
    return json.loads(r.stdout)


def _render_bytes(what: str, data: bytes, rid, name: str, **extra) -> dict:
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "src")
        with open(src, "wb") as f:
            f.write(data)
        return _render({"what": what, "src": src, "out": str(THUMB_DIR / f"{rid}.webp"), "name": name, **extra})


def mark_missing(rid, path: str):
    """The storage says the file isn't there (though it was listed): say so on the record ("missing"), once per version,
    instead of trying to read it again at every sync or view. The next sync removes the record if it's really gone; a new
    version of the file clears it."""
    with connection() as conn:
        cur = conn.execute("SELECT r.data, r.caldav_etag, r.table_id FROM records r WHERE r.id = %s AND r.caldav_href = %s", (rid, path)).fetchone()
        if not cur:
            return
        keys = _keys(store.get_fields(conn, cur["table_id"]))
        items = cur["data"].get(keys["file"]) or []
        if items and not (items[0].get("missing") and items[0].get("previewed") == cur["caldav_etag"]):
            done = {k: v for k, v in items[0].items() if k not in ("preview_retry", "preview_tries")}
            conn.execute("UPDATE records SET data = data || %s WHERE id = %s", (Jsonb({keys["file"]: [{
                **done, "missing": True, "previewed": cur["caldav_etag"], "preview_error": "The file isn't in the storage any more"}]}), rid))


@jobs.handler("file_preview", limit=PREVIEW_CONCURRENCY)
def _job_preview(payload):
    from PIL import Image
    rid = payload["record_id"]
    with connection() as conn:
        rec = conn.execute("SELECT * FROM records WHERE id = %s AND NOT deleted", (rid,)).fetchone()
        if not rec:
            return
        table = conn.execute("SELECT * FROM tables WHERE id = %s", (rec["table_id"],)).fetchone()
        keys = _keys(store.get_fields(conn, table["id"]))
        drv = _storage(conn, table)
    item = (rec["data"].get(keys["file"]) or [{}])[0]
    mime, path, size = item.get("mime") or "", rec["caldav_href"], item.get("size") or 0
    thumb, text, error = False, None, None
    temporary = None   # the converter or the storage couldn't be reached: not tried yet (the job, and later syncs, try again)
    try:
        if _heic(mime, path):
            with Image.open(heic_jpeg(drv, rec, item)) as img:
                _save_thumb(img.copy(), rid)
            thumb = True
        elif (mime in _RASTER or mime == "image/tiff") and size <= 40 * 1024 * 1024:
            thumb = _render_bytes("image", drv.read(path, size + 1), rid, path)["thumb"]
        elif mime == "application/pdf" and size <= 100 * 1024 * 1024:
            with tempfile.TemporaryDirectory() as d:
                src = os.path.join(d, "in.pdf")
                with open(src, "wb") as f:
                    f.write(drv.read(path, size + 1))
                thumb = _pdf_thumb(src, rid)
        elif convertible(path) and CONVERTER and size <= CONVERT_MAX:
            # the first page of the converted PDF (also kept, so opening the preview is instant)
            thumb = _pdf_thumb(preview_pdf(drv, rec, item), rid)
            if _ext(path) == "docx":
                text = _render_bytes("docx_text", drv.read(path, size + 1), rid, path)["text"]
        elif _ext(path) in ("odt", "ods", "odp", "docx", "xlsx", "pptx") and size <= 60 * 1024 * 1024:
            out = _render_bytes("zip", drv.read(path, size + 1), rid, path)
            thumb, text = out["thumb"], out["text"]
        elif is_text(mime, path):
            out = _render_bytes("text", drv.read(path, TEXT_CHARS * 4), rid, path, svg=is_svg(mime, path))
            thumb, text = out["thumb"], out["text"]
    except FileNotFoundError:
        log.info("no preview for %s: not in the storage", path)
        return mark_missing(rid, path)
    except StorageError as e:
        temporary = e
    except HTTPException as e:
        if e.status_code == 404:   # (the converter's "not in the storage")
            return mark_missing(rid, path)
        if e.status_code >= 500:
            temporary = e
        error = str(e.detail)
        log.info("no preview for %s: %s", path, e.detail)
    except Exception as e:
        error = "The file couldn't be read for a preview"
        log.info("no preview for %s: %s", path, e)
    if temporary is not None:
        why = str(getattr(temporary, "detail", "") or "The file's storage couldn't be reached")
        tries = (item.get("preview_tries") or 0) + 1
        if tries < PREVIEW_TRIES:
            # said on the file (the app shows "Preview pending: …"), and not marked tried: the job and later syncs try again
            log.warning("no preview for %s yet (try %d of %d): %s", path, tries, PREVIEW_TRIES, why)
            with connection() as conn:
                cur = conn.execute("SELECT data FROM records WHERE id = %s AND caldav_href = %s", (rid, path)).fetchone()
                items = (cur["data"].get(keys["file"]) or []) if cur else []
                if items:
                    conn.execute("UPDATE records SET data = data || %s WHERE id = %s", (Jsonb({keys["file"]: [
                        {**items[0], "preview_error": why, "preview_retry": True, "preview_tries": tries}]}), rid))
            raise RuntimeError(f"preview of {path} not made yet: {why}")
        error = why   # tried often enough for this version: given up on (a new version of the file is tried again)
        log.warning("no preview for %s: gave up after %d tries: %s", path, tries, why)
    with connection() as conn:
        cur = conn.execute("SELECT data, caldav_etag FROM records WHERE id = %s AND caldav_href = %s", (rid, path)).fetchone()
        if not cur:
            return  # moved or changed again meanwhile: its own preview job follows
        patch = {}
        items = cur["data"].get(keys["file"]) or []
        if items:  # tried for this version (so the next sync doesn't try again), and whether there's a cover now
            done = {k: v for k, v in items[0].items() if k not in ("preview_error", "preview_retry", "preview_tries", "missing")}
            patch[keys["file"]] = [{**done, "previewed": cur["caldav_etag"], **({"thumb": True} if thumb else {}),
                                    **({"pv": PREVIEW_VERSION} if CONVERTER else {}), **({"preview_error": error} if error and not thumb else {})}]
        if text is not None:
            patch[keys["text"]] = text.replace("\x00", "")
        conn.execute("UPDATE records SET data = data || %s, updated_at = now() WHERE id = %s", (Jsonb(patch), rid))
