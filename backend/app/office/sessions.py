"""Editing sessions: what the Document Server's editor is given to open a file, the document key it shares between
people editing the same version, and what happens to the versions it saves (written to the storage, or ignored when
everyone discarded)."""
import hashlib
import io
import json
import logging
import re
import secrets
import time

from fastapi import HTTPException

from app.core.db import connection
from app.data import audit, store
from app.files import catalog as files
from app.office import docserver
from app.office.config import CONVERT, CONVERTED_DIR, doc_type, EDITABLE, settings
from app.office.jwt import sign, verify

log = logging.getLogger("probyte.office")

TOKEN_DAYS = 7


def doc_key(base: str, discards: int) -> str:
    """The Document Server's document key: the same file version → the same key (people can edit together); a new
    version, or a discarded edit, → a new key."""
    return re.sub(r"[^0-9A-Za-z_-]", "", f"{base}-{discards}")[:120]


def settings_stamp(cfg: dict) -> str:
    """The settings are part of the key: the Document Server keeps a document (and a failed download) by its key."""
    return hashlib.sha1("|".join((cfg["url"], cfg["probyte_url"], cfg["server_url"], cfg["secret"],
                                  "1" if cfg["via_probyte"] else "")).encode()).hexdigest()[:6]


def target(conn, record_id: str, table_id: str | None, user) -> dict:
    """The file to open: its name, a version stamp, and whether this user may change it."""
    if table_id:  # a linked table: through its link
        from app.sharing import linked as remote
        t = store.get_table(conn, table_id, user["id"])
        if t["kind"] != "remote" or (t["remote"] or {}).get("source_kind") != "files":
            store.not_found("File not found")
        for r in remote.records(conn, t):
            item = next((a for v in r["data"].values() if isinstance(v, list)
                         for a in v if isinstance(a, dict) and a.get("file") and str(a.get("id")) == record_id), None)
            if item:
                stamp = hashlib.sha1(json.dumps(r["data"], sort_keys=True, default=str).encode()).hexdigest()
                return {"table": t, "rid": record_id, "name": item.get("name") or "file", "linked": True,
                        "base": "l" + hashlib.sha1(f"{t['id']}:{record_id}:{stamp}".encode()).hexdigest()[:40],
                        "can_edit": remote.can(t, "edit_files")}
        store.not_found("File not found")
    table, rec, keys = files._own_record(conn, record_id, user["id"])
    item = (rec["data"].get(keys["file"]) or [{}])[0]
    return {"table": table, "rid": str(rec["id"]), "name": item.get("name") or "file", "linked": False,
            "base": f"{rec['id']}-{(rec['caldav_etag'] or 'x')[:20]}", "can_edit": True}


def open_editor(record_id: str, table_id: str | None, mode: str, user, *, probyte: str, browser: str, lang: str) -> dict:
    """An editor for a file: the settings for the Document Server's editor, signed.
    probyte: Probyte's address for the Document Server; browser: where browsers load the Document Server from."""
    with connection() as conn:
        cfg = settings(conn)
        if not cfg["url"]:
            raise HTTPException(400, "No document server is set up (Settings → Document editing)")
        f = target(conn, record_id, table_id, user)
        name, ext = f["name"], files._ext(f["name"])
        dtype = doc_type(name)
        if not dtype:
            raise HTTPException(400, "The document server can't open this kind of file")
        edit = mode == "edit" and f["can_edit"] and (ext in EDITABLE or ext in CONVERT)
        if mode == "edit" and not f["can_edit"]:
            raise HTTPException(403, "The link's owner doesn't let you change its files")
        mode = "edit" if edit else "view"
        convert = CONVERT.get(ext) if edit else None
        base = re.sub(r"[^0-9A-Za-z_-]", "", f["base"] + (f"-{convert}" if convert else "") + f"-{settings_stamp(cfg)}")
        discards = conn.execute("SELECT count(*) AS n FROM office_sessions WHERE record_id = %s AND discarded AND doc_key LIKE %s",
                                (f["rid"], base + "-%")).fetchone()["n"]
        key = doc_key(base, discards)
        token = secrets.token_urlsafe(32)
        s = conn.execute("INSERT INTO office_sessions (token, record_id, table_id, user_id, doc_key, mode, linked, file_type, convert_from) "
                         "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                         (token, f["rid"], f["table"]["id"], user["id"], key, mode, f["linked"], convert or ext, ext if convert else None)).fetchone()
    title = name
    if convert:  # the editor works on the newer format; the converted copy is what the Document Server gets
        _convert(cfg, s, probyte, name, ext, convert)
        title = name.rsplit(".", 1)[0] + "." + convert
    config = {
        "documentType": dtype,
        "document": {
            "fileType": convert or ext, "key": key, "title": title,
            "url": f"{probyte}/api/office/file/{token}",
            "permissions": {"edit": mode == "edit", "download": True, "print": True, "comment": mode == "edit", "review": mode == "edit"},
        },
        "editorConfig": {
            "mode": mode, "callbackUrl": f"{probyte}/api/office/callback/{token}",
            "user": {"id": str(user["id"]), "name": user.get("username") or "Probyte"},
            "lang": lang,
            "customization": {"forcesave": True, "autosave": True, "compactHeader": True, "hideRightMenu": True,
                              "goback": False, "chat": False},
        },
        "height": "100%", "width": "100%", "type": "desktop",
    }
    if cfg["secret"]:
        config["token"] = sign(config, cfg["secret"])
    return {"session": str(s["id"]), "mode": mode, "converted_from": ext if convert else None, "saves_as": title if convert else None,
            "api_url": browser + "/web-apps/apps/api/documents/api.js", "config": config}


def _convert(cfg: dict, s, probyte: str, name: str, src: str, out: str):
    """The Document Server converts the file (it fetches the original from Probyte); the result is kept for the editor."""
    r = docserver.convert(cfg, {"async": False, "filetype": src, "outputtype": out, "key": f"conv-{s['id']}", "title": name,
                                "url": f"{probyte}/api/office/file/{s['token']}?original=1"})
    if r.get("error"):
        raise HTTPException(502, f"The document server couldn't convert the .{src} file (error {r['error']})")
    if not r.get("endConvert") or not r.get("fileUrl"):
        raise HTTPException(502, "The document server didn't finish converting the file")
    data = docserver.fetch(cfg, r["fileUrl"])
    CONVERTED_DIR.mkdir(parents=True, exist_ok=True)
    for old in CONVERTED_DIR.glob("*"):  # copies of editors from days ago
        if old.stat().st_mtime < time.time() - 3 * 24 * 3600:
            old.unlink(missing_ok=True)
    (CONVERTED_DIR / f"{s['id']}.{out}").write_bytes(data)


def own(conn, session_id, user):
    s = conn.execute("SELECT * FROM office_sessions WHERE id = %s AND user_id = %s", (session_id, user["id"])).fetchone()
    return s or store.not_found("Editing session not found")


def state(session_id, user) -> dict:
    """How many versions this editor's key has written to the storage (the viewer waits for a save with it)."""
    with connection() as conn:
        s = own(conn, session_id, user)
        n = conn.execute("SELECT COALESCE(max(saves), 0) AS n, max(last_saved_at) AS at, max(last_error) AS err FROM office_sessions "
                         "WHERE doc_key = %s", (s["doc_key"],)).fetchone()
    return {"saves": n["n"], "last_saved_at": n["at"], "error": n["err"], "discarded": s["discarded"], "closed": bool(s["closed_at"])}


def save_now(session_id, user) -> dict:
    """Save now: the Document Server sends the current version, which is written to the storage."""
    with connection() as conn:
        s = own(conn, session_id, user)
        cfg = settings(conn)
    r = docserver.command(cfg, {"c": "forcesave", "key": s["doc_key"], "userdata": str(s["id"])})
    err = r.get("error")
    if err == 0:
        return {"saving": True}
    if err == 4:  # nothing changed since the last save
        return {"saving": False, "unchanged": True}
    raise HTTPException(502, f"The document server couldn't save (error {err})")


def close(session_id, user, discard: bool) -> None:
    """Closed; with discard, the Document Server's final save is ignored (unless someone else editing the document
    along keeps their changes)."""
    with connection() as conn:
        own(conn, session_id, user)
        conn.execute("UPDATE office_sessions SET closed_at = now()" + (", discarded = true" if discard else "") + " WHERE id = %s",
                     (session_id,))


# ---------------------------------------------------------------- what the Document Server calls

def by_token(conn, token: str):
    s = conn.execute("SELECT * FROM office_sessions WHERE token = %s", (token,)).fetchone()
    if not s or (time.time() - s["created_at"].timestamp()) > TOKEN_DAYS * 24 * 3600:
        raise HTTPException(404, "Not found")
    return s


def check_jwt(token: str | None, cfg: dict) -> dict | None:
    """The Document Server signs what it sends with the shared secret (in the body or the Authorization header)."""
    if not cfg["secret"]:
        return None
    if not token:
        raise HTTPException(403, "Missing token")
    try:
        claims = verify(token, cfg["secret"])
    except ValueError:
        raise HTTPException(403, "Invalid token")
    return claims.get("payload", claims)


def _write(s, data: bytes, new_ext: str | None):
    """A saved version into the storage: the file's new contents (and format, for a converted one)."""
    with connection() as conn:
        table = store.get_table(conn, s["table_id"])
        user = conn.execute("SELECT username FROM users WHERE id = %s", (s["user_id"],)).fetchone()
        if s["linked"]:
            rec = keys = None
        else:
            rec = conn.execute("SELECT * FROM records WHERE id = %s AND NOT deleted", (s["record_id"],)).fetchone()
            if not rec:
                raise HTTPException(404, "The file was deleted")
            keys = files._keys(store.get_fields(conn, table["id"]))
    audit.set_actor(f"{user['username'] if user else 'Someone'} in ONLYOFFICE", s["user_id"])
    if s["linked"]:  # back through the link (its owner's "Edit files" permission decides)
        from starlette.datastructures import Headers, UploadFile
        from app.sharing import linked as remote
        name = f"document.{new_ext or s['file_type'] or 'bin'}"
        up = UploadFile(io.BytesIO(data), size=len(data), filename=name, headers=Headers({"content-type": "application/octet-stream"}))
        remote.replace_file(table, s["record_id"], up, new_ext if s["convert_from"] or new_ext != s["file_type"] else None)
    else:
        files.replace_contents(table, rec, keys, io.BytesIO(data), None, new_ext)


def _all_discarded(key: str, users: list) -> bool:
    """Did everyone who changed the document (the Document Server's list; everyone in the editor when it has none)
    close it with Discard?"""
    with connection() as conn:
        rows = conn.execute("SELECT user_id::text AS u, bool_and(discarded) AS d FROM office_sessions WHERE doc_key = %s GROUP BY user_id",
                            (key,)).fetchall()
    by_user = {r["u"]: r["d"] for r in rows}
    who = [str(u) for u in users if str(u) in by_user] or list(by_user)
    return bool(who) and all(by_user[u] for u in who)


def handle_callback(token: str, auth_header: str, body: dict) -> dict:
    """The Document Server reports on the document: 1 editing, 2 ready to save (everyone closed), 4 closed with no
    changes, 6 saved on request (forcesave), 3 / 7 errors. Answers {"error": 0} unless the save failed."""
    with connection() as conn:
        s = by_token(conn, token)
        cfg = settings(conn)
    payload = check_jwt((body or {}).get("token") or auth_header.removeprefix("Bearer ").strip(), cfg) or body
    if payload.get("key") and payload["key"] != s["doc_key"]:
        raise HTTPException(400, "Not this session's document")
    status = int(payload.get("status") or 0)
    if status in (2, 6) and payload.get("url"):
        if status == 2 and _all_discarded(s["doc_key"], payload.get("users") or []):
            log.info("ignored the final save of %s (discarded)", s["doc_key"])
            return {"error": 0}
        try:
            data = docserver.fetch(cfg, payload["url"])
            _write(s, data, (payload.get("filetype") or s["file_type"] or "").lower() or None)
            with connection() as conn:
                conn.execute("UPDATE office_sessions SET saves = saves + 1, last_saved_at = now(), last_error = NULL"
                             + (", closed_at = now()" if status == 2 else "") + " WHERE doc_key = %s", (s["doc_key"],))
        except HTTPException as e:
            log.warning("saving %s failed: %s", s["doc_key"], e.detail)
            with connection() as conn:
                conn.execute("UPDATE office_sessions SET last_error = %s WHERE doc_key = %s", (str(e.detail)[:500], s["doc_key"]))
            return {"error": 1}
    elif status in (3, 7):
        with connection() as conn:
            conn.execute("UPDATE office_sessions SET last_error = %s WHERE doc_key = %s",
                         ("The document server couldn't save the document", s["doc_key"]))
    elif status == 4:
        with connection() as conn:
            conn.execute("UPDATE office_sessions SET closed_at = now() WHERE doc_key = %s AND closed_at IS NULL", (s["doc_key"],))
    return {"error": 0}
