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

    kinds.py     what a file is and how a browser shows it (pure)
    catalog.py   files <-> records: writes, the sync with the storage, uploads, new contents, streaming
    previews.py  covers, searchable text and viewable copies, made by the worker
    storages.py  the S3 / WebDAV connections
    api.py       the HTTP routes
"""
import io
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from app.blobs.filestore import clean_rel, guess_mime, norm_prefix, StorageError
from app.calc.compute import FILE_READONLY as READONLY_KEYS
from app.core import events
from app.core.db import connection, Jsonb
from app.data import audit, store
from app.files.kinds import _ext, _keys, _heic, is_svg, is_text, kind_of, previewable, SVG_CSP, view_of
from app.files.previews import _drop_cache, _queue_preview, heic_jpeg, preview_pdf, THUMB_DIR
from app.files.storages import _storage, own_storage, save_storage, StorageIn, test_storage

log = logging.getLogger("probyte.files")

MAX_UPLOAD = int(os.environ.get("FILES_MAX_UPLOAD_MB", "2048")) * 1024 * 1024


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


def _insert(conn, table, fields, meta, user_id=None, extra: dict | None = None):
    """A record for a file that is in the storage now."""
    keys, rid = _keys(fields), uuid.uuid4()
    mime = _mime(meta)
    data = store.apply_defaults([f for f in fields if not f.get("system_key")], extra or {}, user_id)
    data.update(file_values(keys, rid, meta, mime))
    r = store.insert_record(conn, table["id"], data, user_id=user_id, caldav_href=meta["path"], record_id=rid)
    conn.execute("UPDATE records SET caldav_etag = %s WHERE id = %s", (meta["etag"], rid))
    audit.note(conn, table["id"], rid, "created")
    events.record_changed(conn, table, "record.created", rid, source="files")
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
        events.record_changed(conn, {"id": rec["table_id"], "kind": "files"}, "record.updated", rec["id"], source="files")


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
                events.record_changed(conn, table, "record.deleted", rec["id"], source="files")
                stats["removed"] += 1
        stats["files"] = len(objects)
    return stats


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


def _own_record(conn, rid, user_id, need=None):
    rec = conn.execute("SELECT * FROM records WHERE id = %s AND NOT deleted", (rid,)).fetchone() or store.not_found("File not found")
    table = store.get_table(conn, rec["table_id"], user_id, need=need)
    if table["kind"] != "files":
        store.not_found("File not found")
    return table, rec, _keys(store.get_fields(conn, table["id"]))


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


def _check_size(f: UploadFile):
    if f.size is not None and f.size > MAX_UPLOAD:
        raise HTTPException(413, f"{f.filename} is larger than {MAX_UPLOAD // (1024 * 1024)} MB")


def upload_files(table_id: str, files: list[UploadFile], folder: str, user_id, extra: dict | None = None) -> list[dict]:
    """Add files to a files table (the app, a public link that may add, or a linked table through its link).
    extra: values for the new records' own fields."""
    out = []
    with connection() as conn:
        table = store.get_table(conn, table_id)
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
            r = _insert(conn, table, fields, {**meta, "mime": f.content_type}, user_id, extra)
            out.append(store.clean_record(conn.execute("SELECT * FROM records WHERE id = %s", (r["id"],)).fetchone()))
    return out


# formats a new version may change a file to (old Word / Excel / PowerPoint files edited in ONLYOFFICE)
NEW_FORMATS = {"docx", "xlsx", "pptx", "odt", "ods", "odp"}


def replace_contents(table, rec, keys, body_file, mime: str | None, new_ext: str | None = None) -> dict:
    """New contents for a file (its record, fields and links stay). new_ext: the new version is in another format
    (report.doc edited in ONLYOFFICE comes back as a .docx): the file becomes report.docx and the old one goes."""
    new_ext = (new_ext or "").lower().lstrip(".") or None
    old_path = rec["caldav_href"]
    if new_ext and new_ext != _ext(old_path):
        if new_ext not in NEW_FORMATS:
            raise HTTPException(400, f"A file can't be changed to .{new_ext} this way")
        with connection() as conn:
            _lock(conn, table["id"])  # (until it's done: the next sync must see the new name with its record)
            drv = _storage(conn, table)
            folder, _, name = old_path.rpartition("/")
            stem = name.rsplit(".", 1)[0] if "." in name.strip(".") else name
            try:
                target = _free_path(drv, conn, table["id"], f"{folder + '/' if folder else ''}{stem}.{new_ext}", exclude=rec["id"])
                meta = drv.put(target, body_file, guess_mime(target))
            except StorageError as e:
                _bad(e)
            _drop_cache(rec["id"])
            cur = conn.execute("SELECT * FROM records WHERE id = %s FOR UPDATE", (rec["id"],)).fetchone()
            _refresh(conn, cur, keys, {**meta, "mime": None})
            try:
                drv.delete(old_path)
            except StorageError as e:  # the new version is safe; the old file stays (the next sync lists it)
                log.warning("couldn't remove %s after its new version: %s", old_path, e)
            return store.clean_record(conn.execute("SELECT * FROM records WHERE id = %s", (rec["id"],)).fetchone())
    with connection() as conn:
        drv = _storage(conn, table)
    try:
        meta = drv.put(old_path, body_file, mime or guess_mime(old_path))
    except StorageError as e:
        _bad(e)
    with connection() as conn:
        cur = conn.execute("SELECT * FROM records WHERE id = %s FOR UPDATE", (rec["id"],)).fetchone()
        _refresh(conn, cur, keys, {**meta, "mime": mime})
        return store.clean_record(conn.execute("SELECT * FROM records WHERE id = %s", (rec["id"],)).fetchone())


def save_text_contents(table, rec, keys, text: str) -> dict:
    item = (rec["data"].get(keys["file"]) or [{}])[0]
    if not is_text(item.get("mime") or "", item.get("name") or ""):  # text, code, Markdown, SVG
        raise HTTPException(400, "Only text files can be edited here")
    if len(text) > 5 * 1024 * 1024:
        raise HTTPException(413, "That's too much text to save here")
    return replace_contents(table, rec, keys, io.BytesIO(text.encode()), None)


def upload_mime(f: UploadFile) -> str | None:
    return f.content_type if f.content_type and f.content_type != "application/octet-stream" else None


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
