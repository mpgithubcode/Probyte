"""Covers and previews of files, made by the worker (job "file_preview"): a small image for images, PDFs, office
documents and text, the first part of a text file in the Text field (for search), PDFs of Word / PowerPoint files
(the document converter) and JPEGs of iPhone photos (HEIC). Kept on the attachments volume (thumbs/, previews/)."""
import io
import logging
import os
import re
import subprocess
import tempfile
import zipfile

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


FONT_DIR = "/usr/share/fonts/truetype/dejavu"


def _font(size: int, bold=False, mono=False):
    from PIL import ImageFont
    name = "DejaVuSansMono" if mono else "DejaVuSans"
    try:
        return ImageFont.truetype(f"{FONT_DIR}/{name}{'-Bold' if bold else ''}.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _text_thumb(text: str, rid, name: str):
    """A text file's cover: its first lines on a page (Markdown headings larger), like a document preview."""
    from PIL import Image, ImageDraw
    w, h, pad = THUMB_PX, int(THUMB_PX * 1.3), 28
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    md = _ext(name) in ("md", "markdown")
    mono = not md and _ext(name) not in ("txt", "log", "rst")
    y = pad
    for raw in text.replace("\t", "    ").splitlines()[:80]:
        line = raw.rstrip()
        size, bold, color = (15, False, (32, 33, 36)) if not mono else (13, False, (55, 65, 81))
        if md and line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            line = line.lstrip("#").strip()
            size, bold, color = {1: (26, True, (17, 24, 39)), 2: (21, True, (17, 24, 39))}.get(level, (17, True, (31, 41, 55)))
        elif md:
            line = re.sub(r"[*_`]", "", line)
            if line.lstrip().startswith(("- ", "* ", "+ ")):
                line = "•  " + line.lstrip()[2:]
        f = _font(size, bold, mono)
        # wrap long lines to the page width
        while True:
            cut = len(line)
            while cut > 0 and d.textlength(line[:cut], font=f) > w - 2 * pad:
                cut -= 1
            d.text((pad, y), line[:cut], font=f, fill=color)
            y += int(size * 1.45)
            line = line[cut:]
            if not line or y > h - pad:
                break
        if y > h - pad:
            break
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


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


@jobs.handler("file_preview")
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
            with Image.open(io.BytesIO(drv.read(path, size + 1))) as img:
                img.seek(0)
                _save_thumb(img.copy(), rid)
            thumb = True
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
                text = _docx_text(drv.read(path, size + 1))[:TEXT_CHARS]
        elif _ext(path) in ("odt", "ods", "odp", "docx", "xlsx", "pptx") and size <= 60 * 1024 * 1024:
            data = drv.read(path, size + 1)
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                name = next((n for n in ("Thumbnails/thumbnail.png", "docProps/thumbnail.jpeg", "docProps/thumbnail.png") if n in z.namelist()), None)
                if name:
                    with Image.open(io.BytesIO(z.read(name))) as img:
                        _save_thumb(img.copy(), rid)
                    thumb = True
            if _ext(path) == "docx":
                text = _docx_text(data)[:TEXT_CHARS]
        elif is_text(mime, path):
            raw = drv.read(path, TEXT_CHARS * 4)
            text = raw.decode("utf-8", "replace")[:TEXT_CHARS]
            if text.strip() and "\x00" not in text[:2000] and not is_svg(mime, path):
                thumb = _text_thumb(text, rid, path)
    except FileNotFoundError:
        return
    except StorageError as e:
        temporary = e
    except HTTPException as e:
        if e.status_code >= 500:
            temporary = e
        error = str(e.detail)
        log.info("no preview for %s: %s", path, e.detail)
    except Exception as e:
        error = "The file couldn't be read for a preview"
        log.info("no preview for %s: %s", path, e)
    if temporary is not None:
        # said on the file (the app shows "Preview pending: …"), and not marked tried: the job and later syncs try again
        why = str(getattr(temporary, "detail", "") or "The file's storage couldn't be reached")
        log.warning("no preview for %s yet (tried again later): %s", path, why)
        with connection() as conn:
            cur = conn.execute("SELECT data FROM records WHERE id = %s AND caldav_href = %s", (rid, path)).fetchone()
            items = (cur["data"].get(keys["file"]) or []) if cur else []
            if items:
                conn.execute("UPDATE records SET data = data || %s WHERE id = %s",
                             (Jsonb({keys["file"]: [{**items[0], "preview_error": why, "preview_retry": True}]}), rid))
        raise RuntimeError(f"preview of {path} not made yet: {why}")
    with connection() as conn:
        cur = conn.execute("SELECT data, caldav_etag FROM records WHERE id = %s AND caldav_href = %s", (rid, path)).fetchone()
        if not cur:
            return  # moved or changed again meanwhile: its own preview job follows
        patch = {}
        items = cur["data"].get(keys["file"]) or []
        if items:  # tried for this version (so the next sync doesn't try again), and whether there's a cover now
            done = {k: v for k, v in items[0].items() if k not in ("preview_error", "preview_retry")}
            patch[keys["file"]] = [{**done, "previewed": cur["caldav_etag"], **({"thumb": True} if thumb else {}),
                                    **({"pv": PREVIEW_VERSION} if CONVERTER else {}), **({"preview_error": error} if error and not thumb else {})}]
        if text is not None:
            patch[keys["text"]] = text.replace("\x00", "")
        conn.execute("UPDATE records SET data = data || %s, updated_at = now() WHERE id = %s", (Jsonb(patch), rid))
