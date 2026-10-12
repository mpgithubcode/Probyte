"""Files tables: every file in an S3 bucket or WebDAV folder is a record.

The file's own fields are fixed (like a calendar table's): Name, File, Folder, Kind, Size, Modified, Type and Text.
Renaming a record (Name) or changing its Folder renames / moves the file in the storage; uploading adds a file and
its record; replacing a file's contents keeps the record (and every link to it). Fields you add yourself (tags,
links to other tables…) are stored in Probyte only. Changes made directly in the storage arrive with the next sync
(every SYNC_INTERVAL, on "Sync now", and after uploads).

Previews: images, PDFs and OpenDocument / Office files with an embedded thumbnail get a small cover image, made
by the worker and kept on the attachments volume (thumbs/). Text files (and .docx) get their first part in the
Text field, so they can be searched. In the browser, images, PDFs, video, audio and text files are shown inline;
everything else downloads.

Deleting a record moves it to the trash; the file itself is only deleted when the trash is emptied (or after 30
days). Deleting a whole files table never deletes the files.
"""
import io
import logging
import os
import re
import subprocess
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import audit, jobs, store, webhooks
from .attachments import ATTACH_DIR
from .compute import FILE_READONLY as READONLY_KEYS
from .db import Jsonb, connection
from .filestore import StorageError, clean_rel, driver, guess_mime, norm_prefix
from .routes.common import User
from .secretbox import seal

log = logging.getLogger("probyte.files")
router = APIRouter()

THUMB_DIR = ATTACH_DIR / "thumbs"
PREVIEW_DIR = ATTACH_DIR / "previews"      # Word / PowerPoint / OpenDocument files converted to PDF for viewing
CONVERTER = os.environ.get("OFFICE_CONVERTER_URL", "").strip().rstrip("/")
CONVERT_MAX = 100 * 1024 * 1024
SHEET_EXT = {"xlsx", "xlsm", "xls", "ods"}   # shown as tables in the browser; converted only for their cover image
HEIC_MIME = {"image/heic", "image/heif"}       # iPhone photos: most browsers can't show them, so a JPEG is made
CONVERT_EXT = {"doc", "docx", "docm", "dot", "dotx", "odt", "ott", "rtf", "wpd", "pptx", "ppt", "pps", "ppsx", "pptm", "odp", "otp", "odg", "vsd", "vsdx"}
MAX_UPLOAD = int(os.environ.get("FILES_MAX_UPLOAD_MB", "2048")) * 1024 * 1024
THUMB_PX = 480
TEXT_CHARS = 20_000           # how much of a text file goes into the Text field (for search)

KINDS = ["Image", "Video", "Audio", "PDF", "Document", "Spreadsheet", "Presentation", "Text", "Code", "Archive", "Other"]
_DOC = {"doc", "docx", "odt", "rtf", "pages", "wpd"}
_SHEET = {"xls", "xlsx", "ods", "csv", "tsv", "numbers"}
_SLIDES = {"ppt", "pptx", "odp", "key"}
_ARCHIVE = {"zip", "tar", "gz", "tgz", "7z", "rar", "bz2", "xz", "zst"}
_CODE = {"py", "js", "mjs", "ts", "tsx", "jsx", "css", "scss", "html", "htm", "sh", "bash", "ps1", "bat", "c", "h", "cpp", "hpp",
         "cs", "java", "kt", "go", "rs", "rb", "php", "swift", "sql", "json", "xml", "yaml", "yml", "toml", "ini", "cfg", "conf",
         "vue", "svelte", "lua", "pl", "r", "dart", "svg", "dockerfile", "makefile", "env", "gitignore"}
_TEXT = {"txt", "md", "markdown", "log", "csv", "tsv", "rst", "srt", "vtt", "ics", "vcf"} | _CODE
_RASTER = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp"}


def _ext(name: str) -> str:
    base = name.rsplit("/", 1)[-1].lower()
    return base.rsplit(".", 1)[-1] if "." in base else base


def kind_of(mime: str, name: str) -> str:
    e = _ext(name)
    if mime.startswith("image/"):
        return "Image"
    if mime.startswith("video/"):
        return "Video"
    if mime.startswith("audio/"):
        return "Audio"
    if mime == "application/pdf" or e == "pdf":
        return "PDF"
    for k, exts in (("Document", _DOC), ("Spreadsheet", _SHEET), ("Presentation", _SLIDES), ("Archive", _ARCHIVE), ("Code", _CODE)):
        if e in exts:
            return k
    if mime.startswith("text/") or e in _TEXT:
        return "Text"
    return "Other"


def is_text(mime: str, name: str) -> bool:
    return mime.startswith("text/") or _ext(name) in _TEXT or mime in ("application/json", "application/xml", "application/javascript")


def convertible(name: str) -> bool:
    """Can be turned into PDF by the document converter (Word, PowerPoint, OpenDocument…; spreadsheets for covers)."""
    return _ext(name) in CONVERT_EXT or _ext(name) in SHEET_EXT


def _heic(mime: str, name: str) -> bool:
    return mime in HEIC_MIME or _ext(name) in ("heic", "heif")


def previewable(mime: str, name: str) -> bool:
    """A cover image (and / or searchable text) can be made for it."""
    return (mime in _RASTER or mime in ("image/tiff", "application/pdf") or _heic(mime, name) or is_text(mime, name)
            or convertible(name) or _ext(name) in ("odt", "ods", "odp", "docx", "xlsx", "pptx"))


def _drop_cache(rid):
    (THUMB_DIR / f"{rid}.webp").unlink(missing_ok=True)
    for p in PREVIEW_DIR.glob(f"{rid}.*.pdf"):
        p.unlink(missing_ok=True)


# SVG pictures are served with a policy that blocks any script inside them (a sandbox), so they can be shown as images
SVG_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; sandbox"


def is_svg(mime: str, name: str) -> bool:
    return mime == "image/svg+xml" or _ext(name) == "svg"


def view_of(mime: str, name: str) -> str | None:
    """How the browser shows the file: image, pdf, video, audio, text (its source, never rendered) or not at all."""
    if mime in _RASTER or is_svg(mime, name):
        return "image"
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("video/") and mime in ("video/mp4", "video/webm", "video/ogg", "video/quicktime"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if is_text(mime, name):
        return "text"
    return None


# ---------------------------------------------------------------- records <-> files

def _keys(fields) -> dict:
    return {f["system_key"]: str(f["id"]) for f in fields if f.get("system_key")}


def file_values(keys: dict, rid, meta: dict, mime: str, *, thumb=False) -> dict:
    """The file's own fields, from what the storage says about it."""
    folder, _, name = meta["path"].rpartition("/")
    m = meta.get("modified")
    item = {"id": str(rid), "name": name, "mime": mime, "size": meta["size"], "file": True, "view": view_of(mime, name)}
    if thumb:
        item["thumb"] = True
    return {keys["name"]: name, keys["folder"]: folder, keys["file"]: [item], keys["kind"]: kind_of(mime, name),
            keys["size"]: meta["size"], keys["mime"]: mime,
            keys["modified"]: m.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(m, datetime) else None}


def _mime(meta: dict) -> str:
    """Trust the file name over a generic type from the server."""
    given = (meta.get("mime") or "").split(";")[0].strip().lower()
    guessed = guess_mime(meta["path"])
    return guessed if not given or given in ("application/octet-stream", "binary/octet-stream") or guessed != "application/octet-stream" else given


def _storage(conn, table):
    s = conn.execute("SELECT * FROM storages WHERE id = %s", (table["storage_id"],)).fetchone() if table.get("storage_id") else None
    return driver(s, table.get("storage_prefix"))


def _insert(conn, table, fields, meta, user_id=None, extra: dict | None = None):
    """A record for a file that is in the storage now."""
    keys, rid = _keys(fields), uuid.uuid4()
    mime = _mime(meta)
    data = store.apply_defaults([f for f in fields if not f.get("system_key")], extra or {}, user_id)
    data.update(file_values(keys, rid, meta, mime))
    r = store.insert_record(conn, table["id"], data, user_id=user_id, caldav_href=meta["path"], record_id=rid)
    conn.execute("UPDATE records SET caldav_etag = %s WHERE id = %s", (meta["etag"], rid))
    audit.note(conn, table["id"], rid, "created")
    webhooks.emit(conn, table["id"], "record.created", rid)
    _queue_preview(conn, rid, mime, meta["path"])
    return r


def _refresh(conn, rec, keys, meta, *, note=True):
    """The file changed (new contents, or moved): update its fields; the preview is made again."""
    mime = _mime(meta)
    old = rec["data"]
    vals = file_values(keys, rec["id"], meta, mime)
    if rec["caldav_etag"] == meta["etag"] and (old.get(keys["file"]) or [{}])[0].get("thumb"):
        vals[keys["file"]][0]["thumb"] = True       # only moved: the preview is still right
        vals[keys["file"]][0]["previewed"] = rec["caldav_etag"]
    else:
        vals[keys["text"]] = None
        _queue_preview(conn, rec["id"], mime, meta["path"])
    new = {**old, **vals}
    conn.execute("UPDATE records SET data = %s, caldav_href = %s, caldav_etag = %s, updated_at = now() WHERE id = %s",
                 (Jsonb(new), meta["path"], meta["etag"], rec["id"]))
    if note:
        audit.note(conn, rec["table_id"], rec["id"], "updated", old, new, None)
        webhooks.emit(conn, rec["table_id"], "record.updated", rec["id"])


def _free_path(drv, conn, table_id, rel: str, exclude=None) -> str:
    """rel, or 'name (2).ext', 'name (3).ext'… when a file of that name already exists."""
    folder, _, name = rel.rpartition("/")
    stem, dot, ext = name.rpartition(".") if "." in name.strip(".") else (name, "", "")
    n = 1
    while True:
        cand = rel if n == 1 else f"{folder + '/' if folder else ''}{stem} ({n}){dot}{ext}"
        taken = conn.execute("SELECT 1 FROM records WHERE table_id = %s AND caldav_href = %s AND id IS DISTINCT FROM %s",
                             (table_id, cand, exclude)).fetchone()
        if not taken and drv.head(cand) is None:
            return cand
        n += 1
        if n > 500:
            raise StorageError("Too many files with that name")


def _lock(conn, table_id):
    """Writes wait for a running sync of the table (it holds the same lock), so a file written meanwhile is never
    listed by the sync before its record exists (which would import it twice)."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('sync:' || %s))", (str(table_id),))


def _rel_for(name: str, folder: str) -> str:
    name = (name or "").strip()
    if not name:
        raise StorageError("The file needs a name")
    if "/" in name or "\\" in name:
        raise StorageError("A file name can't contain /; use the Folder field to put it in a folder")
    folder = norm_prefix(folder)
    return clean_rel(folder + name)


def _bad(e: Exception):
    raise HTTPException(400, str(e))


# ---- record writes from the app, the API, public links (called by recordops)

def create(conn, table, data: dict, user_id):
    """A new record in a files table makes a new (empty) file; give it a name like notes.txt."""
    fields = store.get_fields(conn, table["id"])
    keys = _keys(fields)
    data = {k: v for k, v in (data or {}).items() if k not in {keys[x] for x in READONLY_KEYS}}
    _lock(conn, table["id"])
    try:
        drv = _storage(conn, table)
        rel = _free_path(drv, conn, table["id"], _rel_for(data.get(keys["name"]) or "Untitled.txt", data.get(keys["folder"]) or ""))
        meta = drv.put(rel, io.BytesIO(b""), guess_mime(rel))
    except StorageError as e:
        _bad(e)
    r, t = store.create_record(conn, table["id"], {**data, keys["name"]: rel.rpartition("/")[2], keys["folder"]: rel.rpartition("/")[0]}, user_id)
    rec = conn.execute("SELECT * FROM records WHERE id = %s", (r["id"],)).fetchone()
    _refresh(conn, rec, keys, meta, note=False)
    return r, t


def update(conn, table, record_id, patch: dict, user_id):
    """Name and Folder rename / move the file; the file's other fields can't be set."""
    fields = store.get_fields(conn, table["id"])
    keys = _keys(fields)
    patch = {k: v for k, v in (patch or {}).items() if k not in {keys[x] for x in READONLY_KEYS}}
    if keys["name"] in patch or keys["folder"] in patch:
        _lock(conn, table["id"])
    rec = conn.execute("SELECT * FROM records WHERE id = %s AND NOT deleted FOR UPDATE", (record_id,)).fetchone() or store.not_found("Record not found")
    moved = None
    if keys["name"] in patch or keys["folder"] in patch:
        try:
            rel = _rel_for(patch.get(keys["name"], rec["data"].get(keys["name"])), patch.get(keys["folder"], rec["data"].get(keys["folder"])) or "")
            if rel != rec["caldav_href"]:
                drv = _storage(conn, table)
                if rel.lower() != (rec["caldav_href"] or "").lower() and (
                        drv.head(rel) is not None or conn.execute("SELECT 1 FROM records WHERE table_id = %s AND caldav_href = %s",
                                                                  (table["id"], rel)).fetchone()):
                    raise StorageError(f"There's already a file called {rel}")
                moved = drv.move(rec["caldav_href"], rel)
            patch[keys["name"]], patch[keys["folder"]] = rel.rpartition("/")[2], rel.rpartition("/")[0]
        except StorageError as e:
            _bad(e)
    store.update_record(conn, record_id, patch, user_id)
    if moved:
        _refresh(conn, conn.execute("SELECT * FROM records WHERE id = %s", (record_id,)).fetchone(), keys, moved, note=False)


def _purge(conn, rows):
    """Delete the files of records removed from the trash for good."""
    by_table = {}
    for r in rows:
        by_table.setdefault(r["table_id"], []).append(r)
    for tid, recs in by_table.items():
        t = conn.execute("SELECT * FROM tables WHERE id = %s", (tid,)).fetchone()
        try:
            drv = _storage(conn, t)
        except StorageError:
            drv = None
        for r in recs:
            try:
                if drv and r["caldav_href"]:
                    drv.delete(r["caldav_href"])
            except Exception as e:  # the storage is down: try again next time
                log.warning("couldn't delete %s: %s", r["caldav_href"], e)
                continue
            conn.execute("DELETE FROM records WHERE id = %s", (r["id"],))
            _drop_cache(r["id"])


def purge_deleted(conn=None):
    """Files whose records are deleted and no longer in the trash are deleted from the storage."""
    def run(c):
        _purge(c, c.execute("SELECT r.id, r.table_id, r.caldav_href FROM records r JOIN tables t ON t.id = r.table_id "
                            "WHERE t.kind = 'files' AND r.deleted AND NOT EXISTS (SELECT 1 FROM record_trash x WHERE x.id = r.id)").fetchall())
    if conn is not None:
        return run(conn)
    with connection() as c:
        run(c)


# ---------------------------------------------------------------- sync (storage -> records)

def sync(table, stats: dict, full: bool = False) -> dict:
    audit.set_actor("File storage sync")
    with connection() as conn:
        drv = _storage(conn, table)
        fields = store.get_fields(conn, table["id"])
    keys = _keys(fields)
    objects = {o["path"]: o for o in drv.list()}
    with connection() as conn:
        rows = conn.execute("SELECT * FROM records WHERE table_id = %s AND caldav_href IS NOT NULL", (table["id"],)).fetchall()
        by_path = {r["caldav_href"]: r for r in rows}
        for path, meta in objects.items():
            rec = by_path.get(path)
            if rec is None:
                _insert(conn, table, fields, meta)
                stats["pulled"] += 1
            elif not rec["deleted"] and rec["caldav_etag"] != meta["etag"]:
                _refresh(conn, rec, keys, meta)
                stats["updated"] += 1
        # files without a cover that could have one (e.g. added before the document converter was set up): try once per version
        for path, rec in by_path.items():
            if path in objects and not rec["deleted"]:
                it = (rec["data"].get(keys["file"]) or [{}])[0]
                if it and not it.get("thumb") and it.get("previewed") != rec["caldav_etag"] and previewable(it.get("mime") or "", path):
                    _queue_preview(conn, rec["id"], it.get("mime") or "", path)
        for path, rec in by_path.items():
            if path not in objects and not rec["deleted"]:
                conn.execute("DELETE FROM records WHERE id = %s", (rec["id"],))
                _drop_cache(rec["id"])
                audit.note(conn, table["id"], rec["id"], "deleted")
                webhooks.emit(conn, table["id"], "record.deleted", rec["id"])
                stats["removed"] += 1
        stats["files"] = len(objects)
    return stats


# ---------------------------------------------------------------- previews (worker)

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
    thumb, text = False, None
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
    except Exception as e:
        log.info("no preview for %s: %s", path, e)
    with connection() as conn:
        cur = conn.execute("SELECT data, caldav_etag FROM records WHERE id = %s AND caldav_href = %s", (rid, path)).fetchone()
        if not cur:
            return  # moved or changed again meanwhile: its own preview job follows
        patch = {}
        items = cur["data"].get(keys["file"]) or []
        if items:  # tried for this version (so the next sync doesn't try again), and whether there's a cover now
            patch[keys["file"]] = [{**items[0], "previewed": cur["caldav_etag"], **({"thumb": True} if thumb else {})}]
        if text is not None:
            patch[keys["text"]] = text.replace("\x00", "")
        conn.execute("UPDATE records SET data = data || %s, updated_at = now() WHERE id = %s", (Jsonb(patch), rid))


# ---------------------------------------------------------------- serving files to the browser

def _disposition(kind: str, name: str) -> str:
    ascii_name = re.sub(r'[^\x20-\x7e]|["\\]', "_", name)
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


def stream(table, rec, keys, *, download=False, as_text=False, rng: str | None = None):
    """The file's bytes, streamed from the storage (with byte ranges, so video can seek)."""
    item = (rec["data"].get(keys["file"]) or [{}])[0]
    name, mime = item.get("name") or "file", item.get("mime") or "application/octet-stream"
    with connection() as conn:
        drv = _storage(conn, table)
    try:
        status, headers, chunks = drv.get(rec["caldav_href"], rng)
    except FileNotFoundError:
        raise HTTPException(404, "The file isn't in the storage any more")
    except StorageError as e:
        raise HTTPException(502, str(e))
    view = view_of(mime, name)
    # no-cache: a file edited or replaced here (or in the storage) shows its new contents at once
    h = {**headers, "Accept-Ranges": "bytes", "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-cache"}
    if as_text and (view == "text" or is_text(mime, name)):
        media, h["Content-Disposition"] = "text/plain; charset=utf-8", _disposition("inline", name)
        h["Content-Security-Policy"] = "default-src 'none'; sandbox"
    elif view in ("image", "video", "audio", "pdf") and not download:
        media, h["Content-Disposition"] = mime, _disposition("inline", name)
        if is_svg(mime, name):
            media, h["Content-Security-Policy"] = "image/svg+xml", SVG_CSP
        elif view != "pdf":  # a sandbox stops the browser's PDF viewer; PDFs can't reach Probyte's pages anyway
            h["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; media-src 'self'; sandbox"
    else:
        media, h["Content-Disposition"] = "application/octet-stream", _disposition("attachment", name)
    return StreamingResponse(chunks, status_code=status, media_type=media, headers=h)


def thumb_response(rid):
    p = THUMB_DIR / f"{rid}.webp"
    if not p.exists():
        raise HTTPException(404, "No preview")
    return FileResponse(p, media_type="image/webp", headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


def _own_record(conn, rid, user_id):
    rec = conn.execute("SELECT * FROM records WHERE id = %s AND NOT deleted", (rid,)).fetchone() or store.not_found("File not found")
    table = store.get_table(conn, rec["table_id"], user_id)
    if table["kind"] != "files":
        store.not_found("File not found")
    return table, rec, _keys(store.get_fields(conn, table["id"]))


@router.get("/api/files/{record_id}/content")
def file_content(record_id: str, request: Request, download: bool = False, text: bool = False, user=User):
    with connection() as conn:
        table, rec, keys = _own_record(conn, record_id, user["id"])
    return stream(table, rec, keys, download=download, as_text=text, rng=request.headers.get("range"))


def preview_response(table, rec, keys):
    with connection() as conn:
        drv = _storage(conn, table)
    item = (rec["data"].get(keys["file"]) or [{}])[0]
    if _heic(item.get("mime") or "", item.get("name") or ""):
        return FileResponse(heic_jpeg(drv, rec, item), media_type="image/jpeg", content_disposition_type="inline",
                            headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})
    pdf = preview_pdf(drv, rec, item)
    return FileResponse(pdf, media_type="application/pdf", content_disposition_type="inline",
                        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.get("/api/files/{record_id}/preview")
def file_preview(record_id: str, user=User):
    """Word / PowerPoint / OpenDocument files as PDF, to view them in the browser."""
    with connection() as conn:
        table, rec, keys = _own_record(conn, record_id, user["id"])
    return preview_response(table, rec, keys)


@router.get("/api/files/{record_id}/thumb")
def file_thumb(record_id: str, user=User):
    with connection() as conn:
        _own_record(conn, record_id, user["id"])
    return thumb_response(record_id)


# ---------------------------------------------------------------- uploads and new contents

def _check_size(f: UploadFile):
    if f.size is not None and f.size > MAX_UPLOAD:
        raise HTTPException(413, f"{f.filename} is larger than {MAX_UPLOAD // (1024 * 1024)} MB")


@router.post("/api/tables/{table_id}/files")
def upload(table_id: str, files: list[UploadFile] = File(...), folder: str = Form(""), user=User):
    """Add files (to a folder inside the table's folder). A name that's taken gets a number: report (2).pdf."""
    out = []
    with connection() as conn:
        table = store.get_table(conn, table_id, user["id"])
        if table["kind"] != "files":
            raise HTTPException(400, "Files can only be uploaded to a files table")
        fields = store.get_fields(conn, table_id)
        try:
            drv = _storage(conn, table)
        except StorageError as e:
            _bad(e)
    for f in files:
        _check_size(f)
        with connection() as conn:
            _lock(conn, table_id)
            try:
                rel = _free_path(drv, conn, table_id, _rel_for(os.path.basename(f.filename or "file"), folder))
                meta = drv.put(rel, f.file, (f.content_type or "").split(";")[0].strip().lower()
                               if f.content_type and f.content_type != "application/octet-stream" else guess_mime(rel))
            except StorageError as e:
                _bad(e)
            r = _insert(conn, table, fields, {**meta, "mime": f.content_type}, user["id"])
            out.append(store.clean_record(conn.execute("SELECT * FROM records WHERE id = %s", (r["id"],)).fetchone()))
    return {"records": out}


def _replace(record_id, user, body_file, mime: str | None):
    with connection() as conn:
        table, rec, keys = _own_record(conn, record_id, user["id"])
        drv = _storage(conn, table)
    try:
        meta = drv.put(rec["caldav_href"], body_file, mime or guess_mime(rec["caldav_href"]))
    except StorageError as e:
        _bad(e)
    with connection() as conn:
        rec = conn.execute("SELECT * FROM records WHERE id = %s FOR UPDATE", (record_id,)).fetchone()
        _refresh(conn, rec, keys, {**meta, "mime": mime})
        return store.clean_record(conn.execute("SELECT * FROM records WHERE id = %s", (record_id,)).fetchone())


@router.put("/api/records/{record_id}/file")
def replace_file(record_id: str, file: UploadFile = File(...), user=User):
    """New contents for a file; its name, folder, links and your own fields stay."""
    _check_size(file)
    return _replace(record_id, user, file.file, file.content_type if file.content_type != "application/octet-stream" else None)


class TextIn(BaseModel):
    text: str


@router.put("/api/files/{record_id}/text")
def save_text(record_id: str, body: TextIn, user=User):
    """Save a text file edited in the preview."""
    with connection() as conn:
        _, rec, keys = _own_record(conn, record_id, user["id"])
    item = (rec["data"].get(keys["file"]) or [{}])[0]
    if not is_text(item.get("mime") or "", item.get("name") or ""):  # text, code, Markdown, SVG
        raise HTTPException(400, "Only text files can be edited here")
    if len(body.text) > 5 * 1024 * 1024:
        raise HTTPException(413, "That's too much text to save here")
    return _replace(record_id, user, io.BytesIO(body.text.encode()), None)


# ---------------------------------------------------------------- storage connections

class StorageIn(BaseModel):
    name: str | None = None
    kind: str = "s3"
    endpoint: str = ""
    region: str = ""
    bucket: str = ""
    username: str = ""
    secret: str | None = None      # empty when editing = keep the saved one
    path_style: bool = True
    prefix: str = ""               # only for testing: the folder a table would use


def storage_out(s) -> dict:
    return {k: (str(s[k]) if k == "id" else s[k]) for k in ("id", "name", "kind", "endpoint", "region", "bucket", "username", "path_style")} \
        | {"has_secret": bool(s["secret"])}


def _storage_row(body: StorageIn, old=None) -> dict:
    if body.kind not in ("s3", "webdav"):
        raise HTTPException(400, "Choose S3 or WebDAV")
    if body.kind == "webdav" and not body.endpoint.strip():
        raise HTTPException(400, "Enter the WebDAV folder's address")
    secret = seal(body.secret) if body.secret else (old["secret"] if old else "")
    name = (body.name or "").strip() or (body.bucket.strip() if body.kind == "s3" else body.endpoint.strip())[:80] or "Storage"
    return {"name": name[:100], "kind": body.kind, "endpoint": body.endpoint.strip(), "region": body.region.strip(),
            "bucket": body.bucket.strip(), "username": body.username.strip(), "secret": secret, "path_style": body.path_style}


def test_storage(row: dict, prefix: str = ""):
    try:
        driver(row, prefix).test()
    except StorageError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Can't connect: {e}")


def save_storage(conn, user_id, body: StorageIn) -> dict:
    row = _storage_row(body)
    test_storage(row, body.prefix)
    return conn.execute("INSERT INTO storages (user_id, name, kind, endpoint, region, bucket, username, secret, path_style) "
                        "VALUES (%(u)s, %(name)s, %(kind)s, %(endpoint)s, %(region)s, %(bucket)s, %(username)s, %(secret)s, %(path_style)s) RETURNING *",
                        {**row, "u": user_id}).fetchone()


def own_storage(conn, storage_id, user_id):
    return conn.execute("SELECT * FROM storages WHERE id = %s AND user_id = %s", (storage_id, user_id)).fetchone() \
        or store.not_found("Storage connection not found")


def create_table(conn, user_id, base_id, body) -> dict:
    """A new files table on a saved storage connection or a new one (tested first)."""
    if body.storage_id:
        s = own_storage(conn, body.storage_id, user_id)
        test_storage(s, body.storage_prefix or "")
    elif body.storage:
        s = save_storage(conn, user_id, StorageIn(**{**body.storage, "prefix": body.storage_prefix or ""}))
    else:
        raise HTTPException(400, "Choose where the files are")
    t = store.create_table(conn, user_id, base_id, body.name, "files")
    return conn.execute("UPDATE tables SET storage_id = %s, storage_prefix = %s WHERE id = %s RETURNING *",
                        (s["id"], norm_prefix(body.storage_prefix), t["id"])).fetchone()


@router.get("/api/storages")
def list_storages(user=User):
    with connection() as conn:
        rows = conn.execute("SELECT s.*, (SELECT count(*) FROM tables t WHERE t.storage_id = s.id) AS tables FROM storages s "
                            "WHERE s.user_id = %s ORDER BY s.name", (user["id"],)).fetchall()
    return [storage_out(s) | {"tables": s["tables"]} for s in rows]


@router.post("/api/storages")
def create_storage(body: StorageIn, user=User):
    with connection() as conn:
        return storage_out(save_storage(conn, user["id"], body))


@router.post("/api/storages/test")
def test_storage_route(body: StorageIn, storage_id: str | None = None, user=User):
    """Check a connection before saving it (with an id: test changes to a saved one)."""
    with connection() as conn:
        old = own_storage(conn, storage_id, user["id"]) if storage_id else None
    test_storage(_storage_row(body, old), body.prefix)
    return {"ok": True}


@router.patch("/api/storages/{storage_id}")
def update_storage(storage_id: str, body: StorageIn, user=User):
    with connection() as conn:
        row = _storage_row(body, own_storage(conn, storage_id, user["id"]))
        test_storage(row)
        s = conn.execute("UPDATE storages SET name = %(name)s, kind = %(kind)s, endpoint = %(endpoint)s, region = %(region)s, "
                         "bucket = %(bucket)s, username = %(username)s, secret = %(secret)s, path_style = %(path_style)s "
                         "WHERE id = %(id)s RETURNING *", {**row, "id": storage_id}).fetchone()
    return storage_out(s)


@router.delete("/api/storages/{storage_id}")
def delete_storage(storage_id: str, user=User):
    with connection() as conn:
        own_storage(conn, storage_id, user["id"])
        n = conn.execute("SELECT count(*) AS n FROM tables WHERE storage_id = %s", (storage_id,)).fetchone()["n"]
        if n:
            raise HTTPException(400, f"{n} table{'s use' if n > 1 else ' uses'} this connection; delete {'them' if n > 1 else 'it'} first")
        conn.execute("DELETE FROM storages WHERE id = %s", (storage_id,))
    return {"ok": True}
