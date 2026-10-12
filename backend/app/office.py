"""Editing files of files tables in an ONLYOFFICE Document Server (Docs), inside Probyte's file viewer.

How it fits together (ONLYOFFICE's Docs API, with JWT signing):
1. The viewer asks for an editor (POST /api/office/sessions): Probyte makes a session with a secret token and
   returns the editor's settings, signed with the Document Server's secret (JWT).
2. The browser loads the Document Server's editor (api.js) with them. The Document Server downloads the file from
   Probyte (GET /api/office/file/<token>) and calls back (POST /api/office/callback/<token>) as people edit.
3. A save (the Save button: a "forcesave" command; or everyone closing the editor) makes the Document Server call
   back with the new version's address: Probyte downloads it and writes it to the S3 / WebDAV storage, as a new
   version of the same record.
4. Closing with "Discard": the session is marked, and the Document Server's final save is ignored when everyone
   who changed the document discarded (someone editing along who didn't keeps their changes). The next editor gets
   a new key, so it starts again from the file in the storage.

Files of linked tables are edited in this server's Document Server too: Probyte hands it the file it gets through
the link, and saves go back through the link (when its owner lets it change files: "Edit files").

Old formats (.doc, .xls, .ppt…) can't be saved as they are: the Document Server first converts them (its conversion
service) and the editor works on the .docx / .xlsx / .pptx. Saving makes that the file's new version (report.doc
becomes report.docx, same record); discarding leaves the old file as it was.

Browsers can reach the Document Server through Probyte ("through Probyte", the default for new setups): Probyte
relays it at /office-ds/ (pages, files and the editor's WebSocket), so it works wherever Probyte does, over HTTPS,
with the Document Server only on the local network or the same Docker network. Otherwise browsers load it from its
own address, which they must be able to reach.

The Document Server must reach Probyte (its file and callback addresses), and Probyte must reach the Document
Server (commands, and the saved files): settings "Probyte's address for the Document Server" and "the Document
Server's address for Probyte" cover setups where these differ from the public addresses.
"""
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from . import audit, files, store
from .db import connection
from .routes.common import User
from .secretbox import seal, unseal

log = logging.getLogger("probyte.office")
router = APIRouter()

WORD = {"docx", "docm", "dotx", "dotm", "odt", "ott", "rtf", "txt", "doc", "dot", "epub", "fb2", "html", "htm", "mht", "xml"}
CELL = {"xlsx", "xlsm", "xltx", "xltm", "ods", "ots", "csv", "xls", "xlt"}
SLIDE = {"pptx", "pptm", "ppsx", "potx", "potm", "odp", "otp", "ppt", "pps", "pot"}
PDF = {"pdf", "djvu", "xps", "oxps"}
# opened for editing; the rest (old binary formats, PDF…) open to read
EDITABLE = {"docx", "docm", "dotx", "odt", "ott", "rtf", "txt", "xlsx", "xlsm", "xltx", "ods", "ots", "csv", "pptx", "pptm",
            "ppsx", "potx", "odp", "otp"}
# opened for editing as their newer format (the saved version replaces the file, with the new extension)
CONVERT = {"doc": "docx", "dot": "docx", "dotm": "docx", "xls": "xlsx", "xlt": "xlsx", "xltm": "xlsx",
           "ppt": "pptx", "pps": "pptx", "pot": "pptx", "potm": "pptx"}
SETTINGS = ("url", "secret", "probyte_url", "server_url", "via_probyte")
PREFIX = "/office-ds"   # where Probyte relays the Document Server for browsers
CONVERTED_DIR = files.ATTACH_DIR / "office"   # converted copies, while they're edited


# ---------------------------------------------------------------- settings (Settings → Document editing, admin)

def settings(conn) -> dict:
    rows = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM app_settings WHERE key LIKE 'office.%'").fetchall()}
    out = {k: rows.get(f"office.{k}", "") for k in SETTINGS}
    out["secret"] = unseal(out["secret"]) or ""
    out["via_probyte"] = rows.get("office.via_probyte", "1") == "1"   # (on unless turned off)
    return out


def enabled(conn) -> bool:
    return bool(settings(conn)["url"])


def doc_type(name: str) -> str | None:
    e = files._ext(name)
    return "word" if e in WORD else "cell" if e in CELL else "slide" if e in SLIDE else "pdf" if e in PDF else None


# ---------------------------------------------------------------- JWT (HS256), as the Document Server signs and checks

def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def sign(payload: dict, secret: str) -> str:
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


def verify(token: str, secret: str) -> dict:
    try:
        head, body, sig = token.split(".")
        good = _b64(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(good, sig):
            raise ValueError("bad signature")
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if claims.get("exp") and claims["exp"] < time.time():
            raise ValueError("expired")
        return claims
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(str(e))


# ---------------------------------------------------------------- sessions

def _key(base: str, discards: int) -> str:
    """The Document Server's document key: the same file version → the same key (people can edit together); a new
    version, or a discarded edit, → a new key."""
    return re.sub(r"[^0-9A-Za-z_-]", "", f"{base}-{discards}")[:120]


class SessionIn(BaseModel):
    record_id: str
    mode: str = "edit"            # "view" opens it read-only
    table_id: str | None = None   # a linked table's file (record_id: the source's file id)


def _probyte_base(cfg: dict, request: Request) -> str:
    from .oauth import public_base
    return (cfg["probyte_url"] or public_base(request)).rstrip("/")


@router.get("/api/office/status")
def status(user=User):
    with connection() as conn:
        cfg = settings(conn)
    return {"enabled": bool(cfg["url"]), "url": cfg["url"] or None,
            "editable": sorted(EDITABLE), "convertible": CONVERT, "viewable": sorted(WORD | CELL | SLIDE | PDF)}


def _target(conn, body: SessionIn, user) -> dict:
    """The file to open: its name, a version stamp, and whether this user may change it."""
    if body.table_id:  # a linked table: through its link
        from . import remote
        t = store.get_table(conn, body.table_id, user["id"])
        if t["kind"] != "remote" or (t["remote"] or {}).get("source_kind") != "files":
            store.not_found("File not found")
        for r in remote.records(conn, t):
            item = next((a for v in r["data"].values() if isinstance(v, list)
                         for a in v if isinstance(a, dict) and a.get("file") and str(a.get("id")) == body.record_id), None)
            if item:
                stamp = hashlib.sha1(json.dumps(r["data"], sort_keys=True, default=str).encode()).hexdigest()
                return {"table": t, "rid": body.record_id, "name": item.get("name") or "file", "linked": True,
                        "base": "l" + hashlib.sha1(f"{t['id']}:{body.record_id}:{stamp}".encode()).hexdigest()[:40],
                        "can_edit": remote.can(t, "edit_files")}
        store.not_found("File not found")
    table, rec, keys = files._own_record(conn, body.record_id, user["id"])
    item = (rec["data"].get(keys["file"]) or [{}])[0]
    return {"table": table, "rid": str(rec["id"]), "name": item.get("name") or "file", "linked": False,
            "base": f"{rec['id']}-{(rec['caldav_etag'] or 'x')[:20]}", "can_edit": True}


@router.post("/api/office/sessions")
def open_session(body: SessionIn, request: Request, user=User):
    """An editor for a file: the settings for the Document Server's editor, signed."""
    with connection() as conn:
        cfg = settings(conn)
        if not cfg["url"]:
            raise HTTPException(400, "No document server is set up (Settings → Document editing)")
        f = _target(conn, body, user)
        name, ext = f["name"], files._ext(f["name"])
        dtype = doc_type(name)
        if not dtype:
            raise HTTPException(400, "The document server can't open this kind of file")
        edit = body.mode == "edit" and f["can_edit"] and (ext in EDITABLE or ext in CONVERT)
        if body.mode == "edit" and not f["can_edit"]:
            raise HTTPException(403, "The link's owner doesn't let you change its files")
        mode = "edit" if edit else "view"
        convert = CONVERT.get(ext) if edit else None
        base = re.sub(r"[^0-9A-Za-z_-]", "", f["base"] + (f"-{convert}" if convert else ""))
        discards = conn.execute("SELECT count(*) AS n FROM office_sessions WHERE record_id = %s AND discarded AND doc_key LIKE %s",
                                (f["rid"], base + "-%")).fetchone()["n"]
        key = _key(base, discards)
        token = secrets.token_urlsafe(32)
        s = conn.execute("INSERT INTO office_sessions (token, record_id, table_id, user_id, doc_key, mode, linked, file_type, convert_from) "
                         "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                         (token, f["rid"], f["table"]["id"], user["id"], key, mode, f["linked"], convert or ext, ext if convert else None)).fetchone()
    probyte = _probyte_base(cfg, request)
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
            "lang": (request.headers.get("accept-language") or "en").split(",")[0].split("-")[0] or "en",
            "customization": {"forcesave": True, "autosave": True, "compactHeader": True, "hideRightMenu": True,
                              "goback": False, "chat": False},
        },
        "height": "100%", "width": "100%", "type": "desktop",
    }
    if cfg["secret"]:
        config["token"] = sign(config, cfg["secret"])
    return {"session": str(s["id"]), "mode": mode, "converted_from": ext if convert else None, "saves_as": title if convert else None,
            "api_url": _browser_base(cfg, request) + "/web-apps/apps/api/documents/api.js", "config": config}


def _browser_base(cfg: dict, request: Request) -> str:
    """Where browsers load the Document Server from: through Probyte, or its own address."""
    from .oauth import public_base
    return public_base(request).rstrip("/") + PREFIX if cfg["via_probyte"] else cfg["url"].rstrip("/")


def _convert(cfg: dict, s, probyte: str, name: str, src: str, out: str):
    """The Document Server converts the file (it fetches the original from Probyte); the result is kept for the editor."""
    r = _post(cfg, ("/ConvertService.ashx", "/converter"), {
        "async": False, "filetype": src, "outputtype": out, "key": f"conv-{s['id']}", "title": name,
        "url": f"{probyte}/api/office/file/{s['token']}?original=1"})
    if r.get("error"):
        raise HTTPException(502, f"The document server couldn't convert the .{src} file (error {r['error']})")
    if not r.get("endConvert") or not r.get("fileUrl"):
        raise HTTPException(502, "The document server didn't finish converting the file")
    data = _from_server(cfg, r["fileUrl"])
    CONVERTED_DIR.mkdir(parents=True, exist_ok=True)
    for old in CONVERTED_DIR.glob("*"):  # copies of editors from days ago
        if old.stat().st_mtime < time.time() - 3 * 24 * 3600:
            old.unlink(missing_ok=True)
    (CONVERTED_DIR / f"{s['id']}.{out}").write_bytes(data)


def _session(conn, session_id, user):
    s = conn.execute("SELECT * FROM office_sessions WHERE id = %s AND user_id = %s", (session_id, user["id"])).fetchone()
    return s or store.not_found("Editing session not found")


@router.get("/api/office/sessions/{session_id}")
def session_state(session_id: str, user=User):
    """How many versions this editor's key has written to the storage (the viewer waits for a save with it)."""
    with connection() as conn:
        s = _session(conn, session_id, user)
        n = conn.execute("SELECT COALESCE(max(saves), 0) AS n, max(last_saved_at) AS at, max(last_error) AS err FROM office_sessions "
                         "WHERE doc_key = %s", (s["doc_key"],)).fetchone()
    return {"saves": n["n"], "last_saved_at": n["at"], "error": n["err"], "discarded": s["discarded"], "closed": bool(s["closed_at"])}


def _command(cfg: dict, payload: dict) -> dict:
    """A command to the Document Server's command service (forcesave, version…)."""
    return _post(cfg, ("/coauthoring/CommandService.ashx", "/command"), payload)


def _post(cfg: dict, paths: tuple, payload: dict) -> dict:
    """A signed request to one of the Document Server's services (its older address first, then the newer one)."""
    base = (cfg["server_url"] or cfg["url"]).rstrip("/")
    body = dict(payload)
    headers = {"Accept": "application/json"}
    if cfg["secret"]:
        body["token"] = sign(payload, cfg["secret"])
        headers["Authorization"] = "Bearer " + sign({"payload": payload}, cfg["secret"])
    last = None
    for path in paths:
        try:
            r = requests.post(base + path, json=body, headers=headers, timeout=120)
        except requests.RequestException as e:
            raise HTTPException(502, f"The document server isn't reachable from Probyte ({e.__class__.__name__})")
        if r.status_code == 404:
            last = r
            continue
        try:
            return r.json()
        except ValueError:
            raise HTTPException(502, f"The document server answered HTTP {r.status_code}")
    raise HTTPException(502, f"The document server has no {paths[0].strip('/')} service (HTTP {last.status_code if last else '?'})")


@router.post("/api/office/sessions/{session_id}/save")
def save(session_id: str, user=User):
    """Save now: the Document Server sends the current version, which is written to the storage."""
    with connection() as conn:
        s = _session(conn, session_id, user)
        cfg = settings(conn)
    r = _command(cfg, {"c": "forcesave", "key": s["doc_key"], "userdata": str(s["id"])})
    err = r.get("error")
    if err == 0:
        return {"saving": True}
    if err == 4:  # nothing changed since the last save
        return {"saving": False, "unchanged": True}
    raise HTTPException(502, f"The document server couldn't save (error {err})")


@router.post("/api/office/sessions/{session_id}/discard")
def discard(session_id: str, user=User):
    """Close without saving: the Document Server's final save is ignored (unless someone else editing the document
    along keeps their changes)."""
    with connection() as conn:
        _session(conn, session_id, user)
        conn.execute("UPDATE office_sessions SET discarded = true, closed_at = now() WHERE id = %s", (session_id,))
    return {"ok": True}


@router.post("/api/office/sessions/{session_id}/close")
def close(session_id: str, user=User):
    with connection() as conn:
        _session(conn, session_id, user)
        conn.execute("UPDATE office_sessions SET closed_at = now() WHERE id = %s", (session_id,))
    return {"ok": True}


# ---------------------------------------------------------------- what the Document Server calls

def _by_token(conn, token: str):
    s = conn.execute("SELECT * FROM office_sessions WHERE token = %s", (token,)).fetchone()
    if not s or (time.time() - s["created_at"].timestamp()) > 7 * 24 * 3600:
        raise HTTPException(404, "Not found")
    return s


def _check_jwt(request: Request, body: dict | None, cfg: dict) -> dict | None:
    """The Document Server signs what it sends with the shared secret (in the body or the Authorization header)."""
    if not cfg["secret"]:
        return None
    token = (body or {}).get("token") or (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(403, "Missing token")
    try:
        claims = verify(token, cfg["secret"])
    except ValueError:
        raise HTTPException(403, "Invalid token")
    return claims.get("payload", claims)


@router.get("/api/office/file/{token}", include_in_schema=False)
def file_for_server(token: str, request: Request, original: bool = False):
    """The file, for the Document Server (the session's secret token is the permission): the converted copy for an
    old format (original=1: the file itself, for the conversion)."""
    from fastapi.responses import FileResponse
    with connection() as conn:
        s = _by_token(conn, token)
        cfg = settings(conn)
        if cfg["secret"] and request.headers.get("authorization"):
            _check_jwt(request, None, cfg)
        table = store.get_table(conn, s["table_id"])
        if s["convert_from"] and not original:
            path = CONVERTED_DIR / f"{s['id']}.{s['file_type']}"
            if not path.exists():
                raise HTTPException(404, "Not found")
            return FileResponse(path, media_type="application/octet-stream")
        if s["linked"]:
            from . import remote
            return remote.file(conn, table, s["record_id"], "content", download=True)
        rec = conn.execute("SELECT * FROM records WHERE id = %s AND NOT deleted", (s["record_id"],)).fetchone()
        if not rec:
            raise HTTPException(404, "Not found")
        keys = files._keys(store.get_fields(conn, table["id"]))
    return files.stream(table, rec, keys, download=True)


def _from_server(cfg: dict, url: str) -> bytes:
    """A saved version from the Document Server (only from its own addresses)."""
    allowed = {urlparse(u).netloc for u in (cfg["url"], cfg["server_url"]) if u}
    u = urlparse(url)
    if u.scheme not in ("http", "https") or u.netloc not in allowed:
        # the Document Server may report its own address as it knows it: fetch the same path from the configured one
        base = (cfg["server_url"] or cfg["url"]).rstrip("/")
        path = u.path[len(PREFIX):] if u.path.startswith(PREFIX + "/") else u.path  # (reported through Probyte's relay)
        url = base + path + (f"?{u.query}" if u.query else "")
    r = requests.get(url, timeout=(10, 600))
    if r.status_code != 200:
        raise HTTPException(502, f"Couldn't get the saved document (HTTP {r.status_code})")
    return r.content


@router.post("/api/office/callback/{token}", include_in_schema=False)
async def callback(token: str, request: Request):
    """The Document Server reports on the document: 1 editing, 2 ready to save (everyone closed), 4 closed with no
    changes, 6 saved on request (forcesave), 3 / 7 errors. Always answers {"error": 0} unless the save failed."""
    body = await request.json()
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(_handle_callback, token, request, body)


def _write(s, data: bytes, new_ext: str | None):
    """A saved version into the storage: the file's new contents (and format, for a converted one)."""
    import io
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
        from . import remote
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


def _handle_callback(token: str, request: Request, body: dict):
    with connection() as conn:
        s = _by_token(conn, token)
        cfg = settings(conn)
    payload = _check_jwt(request, body, cfg) or body
    if payload.get("key") and payload["key"] != s["doc_key"]:
        raise HTTPException(400, "Not this session's document")
    status = int(payload.get("status") or 0)
    if status in (2, 6) and payload.get("url"):
        if status == 2 and _all_discarded(s["doc_key"], payload.get("users") or []):
            log.info("ignored the final save of %s (discarded)", s["doc_key"])
            return JSONResponse({"error": 0})
        try:
            data = _from_server(cfg, payload["url"])
            _write(s, data, (payload.get("filetype") or s["file_type"] or "").lower() or None)
            with connection() as conn:
                conn.execute("UPDATE office_sessions SET saves = saves + 1, last_saved_at = now(), last_error = NULL"
                             + (", closed_at = now()" if status == 2 else "") + " WHERE doc_key = %s", (s["doc_key"],))
        except HTTPException as e:
            log.warning("saving %s failed: %s", s["doc_key"], e.detail)
            with connection() as conn:
                conn.execute("UPDATE office_sessions SET last_error = %s WHERE doc_key = %s", (str(e.detail)[:500], s["doc_key"]))
            return JSONResponse({"error": 1})
    elif status in (3, 7):
        with connection() as conn:
            conn.execute("UPDATE office_sessions SET last_error = %s WHERE doc_key = %s",
                         ("The document server couldn't save the document", s["doc_key"]))
    elif status == 4:
        with connection() as conn:
            conn.execute("UPDATE office_sessions SET closed_at = now() WHERE doc_key = %s AND closed_at IS NULL", (s["doc_key"],))
    return JSONResponse({"error": 0})


# ---------------------------------------------------------------- admin: Settings → Document editing

class SettingsIn(BaseModel):
    url: str = ""
    secret: str | None = None        # empty = keep the saved one
    probyte_url: str = ""
    server_url: str = ""
    via_probyte: bool = True


def _admin(user):
    if not user.get("is_admin") or user.get("api_key"):
        raise HTTPException(403, "Only the server's admin account can do this")


@router.get("/api/office/settings")
def get_settings(user=User):
    _admin(user)
    with connection() as conn:
        cfg = settings(conn)
    return {"url": cfg["url"], "probyte_url": cfg["probyte_url"], "server_url": cfg["server_url"], "via_probyte": cfg["via_probyte"],
            "has_secret": bool(cfg["secret"])}


def _clean_url(u: str) -> str:
    u = (u or "").strip().rstrip("/")
    if u and not re.match(r"^https?://[^/\s]+", u):
        raise HTTPException(400, f"“{u}” isn't an address starting with http:// or https://")
    return u


@router.put("/api/office/settings")
def put_settings(body: SettingsIn, user=User):
    _admin(user)
    vals = {"url": _clean_url(body.url), "probyte_url": _clean_url(body.probyte_url), "server_url": _clean_url(body.server_url),
            "via_probyte": "1" if body.via_probyte else ""}
    with connection() as conn:
        for k, v in vals.items():
            conn.execute("INSERT INTO app_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                         (f"office.{k}", v))
        if body.secret is not None and (body.secret or not vals["url"]):
            conn.execute("INSERT INTO app_settings (key, value) VALUES ('office.secret', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                         (seal(body.secret) if body.secret else "",))
    return get_settings(user)


_probes: dict[str, float] = {}


@router.get("/api/office/probe/{token}", include_in_schema=False)
def probe(token: str):
    """A tiny file the Document Server fetches during the settings test (can it reach Probyte?)."""
    if _probes.get(token, 0) < time.time():
        raise HTTPException(404, "Not found")
    return Response(b"Probyte test", media_type="text/plain")


@router.post("/api/office/settings/test")
def test_settings(request: Request, user=User):
    """Can Probyte reach the Document Server, does it accept our signature, and can it reach Probyte back?"""
    _admin(user)
    with connection() as conn:
        cfg = settings(conn)
    if not cfg["url"]:
        raise HTTPException(400, "Enter the document server's address first")
    base = (cfg["server_url"] or cfg["url"]).rstrip("/")
    try:
        h = requests.get(base + "/healthcheck", timeout=10)
    except requests.RequestException as e:
        raise HTTPException(400, f"Probyte can't reach {base} ({e.__class__.__name__})")
    if h.status_code != 200 or "true" not in h.text:
        raise HTTPException(400, f"{base} doesn't look like an ONLYOFFICE Document Server (healthcheck: HTTP {h.status_code})")
    r = _command(cfg, {"c": "version"})
    if r.get("error") not in (0, None):
        raise HTTPException(400, "The document server refused Probyte's signature: check the secret (JWT)" if r.get("error") == 6
                            else f"The document server answered error {r.get('error')}")
    # the other way: the Document Server downloads a small file from Probyte (as it does every document)
    token = secrets.token_urlsafe(24)
    _probes[token] = time.time() + 120
    probyte = _probyte_base(cfg, request)
    try:
        c = _post(cfg, ("/ConvertService.ashx", "/converter"), {"async": False, "filetype": "txt", "outputtype": "docx",
                                                                 "key": f"probe-{token[:20]}", "title": "probe.txt",
                                                                 "url": f"{probyte}/api/office/probe/{token}"})
    finally:
        _probes.pop(token, None)
    if c.get("error"):
        raise HTTPException(400, f"The document server can't download files from Probyte at {probyte} (error {c['error']}). "
                                 "Set “Probyte's address for the document server” (network settings) to an address it can reach, "
                                 "e.g. http://probyte:8000 in the same Docker network or http://<this machine's IP>:<Probyte's port>; "
                                 "a Document Server on a private network may also need ALLOW_PRIVATE_IP_ADDRESS=true.")
    return {"ok": True, "version": r.get("version")}


# ---------------------------------------------------------------- the relay: the Document Server through Probyte

_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding",
        "upgrade", "host", "content-length"}
# what browsers never need (Probyte talks to these directly), and the Document Server's own pages
_NOT_RELAYED = re.compile(r"^(coauthoring/commandservice\.ashx|command|convertservice\.ashx|converter|info|example|welcome)(/|$)", re.I)
_client = None


def _relay_target(path: str) -> str:
    with connection() as conn:
        cfg = settings(conn)
    if not cfg["url"] or not cfg["via_probyte"] or _NOT_RELAYED.match(path.lstrip("/")) or ".." in path.split("/"):
        raise HTTPException(404, "Not found")
    return (cfg["server_url"] or cfg["url"]).rstrip("/") + "/" + path.lstrip("/")


def _relay_headers(headers, request_base: str) -> dict:
    """The browser's request headers, plus where it sees the Document Server (so the addresses it makes point back
    through Probyte, as an nginx in front of it would say)."""
    u = urlparse(request_base)
    out = {k: v for k, v in headers.items() if k.lower() not in _HOP and k.lower() not in ("cookie", "authorization")}
    out["X-Forwarded-Host"] = u.netloc + PREFIX
    out["X-Forwarded-Proto"] = u.scheme
    return out


# Document Server 9 installs a service worker over its files; behind the relay a stale one (its cache from an earlier
# version or a failed load) keeps the editor from ever loading: "a glimpse, then nothing". This one takes its place,
# empties its caches and removes itself, so the browser loads the editor from the network again.
_SW_RESET = """
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys()
    .then(function (keys) { return Promise.all(keys.map(function (k) { return caches.delete(k); })); })
    .then(function () { return self.registration.unregister(); })
    .then(function () { return self.clients.matchAll({ type: 'window' }); })
    .then(function (clients) { clients.forEach(function (c) { c.navigate(c.url); }); }));
});
"""


@router.api_route(PREFIX + "/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS"], include_in_schema=False)
async def relay(path: str, request: Request):
    if path.endswith("document_editor_service_worker.js"):
        return Response(_SW_RESET, media_type="application/javascript", headers={"Cache-Control": "no-store"})
    import httpx
    from starlette.background import BackgroundTask
    from starlette.concurrency import run_in_threadpool
    from starlette.responses import StreamingResponse
    from .oauth import public_base
    global _client
    target = await run_in_threadpool(_relay_target, path)
    if request.url.query:
        target += "?" + request.url.query
    _client = _client or httpx.AsyncClient(timeout=httpx.Timeout(30, read=600), follow_redirects=False)
    body = await request.body() if request.method in ("POST", "PUT") else None  # (with its length: not every server takes chunks)
    try:
        up = await _client.send(_client.build_request(request.method, target, headers=_relay_headers(request.headers, public_base(request)),
                                                      content=body), stream=True)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Probyte can't reach the document server ({e.__class__.__name__})")
    headers = {k: v for k, v in up.headers.items() if k.lower() not in _HOP and k.lower() != "set-cookie"}
    if "location" in headers:  # a redirect to its own address: through the relay instead
        loc = urlparse(headers["location"])
        if loc.netloc:
            headers["location"] = public_base(request).rstrip("/") + PREFIX + loc.path + (f"?{loc.query}" if loc.query else "")
        elif loc.path.startswith("/") and not loc.path.startswith(PREFIX):
            headers["location"] = PREFIX + headers["location"]
    return StreamingResponse(up.aiter_raw(), status_code=up.status_code, headers=headers, background=BackgroundTask(up.aclose))


@router.websocket(PREFIX + "/{path:path}")
async def relay_ws(ws: WebSocket, path: str):
    """The editor's live connection (co-editing, saving) to the Document Server."""
    import asyncio
    import websockets
    from starlette.concurrency import run_in_threadpool
    from .oauth import public_base
    try:
        target = await run_in_threadpool(_relay_target, path)
    except HTTPException:
        await ws.close(code=1008)
        return
    target = re.sub(r"^http", "ws", target) + (f"?{ws.url.query}" if ws.url.query else "")
    headers = _relay_headers({k: v for k, v in ws.headers.items() if k.lower() not in ("sec-websocket-key", "sec-websocket-version",
                              "sec-websocket-extensions", "sec-websocket-protocol")}, public_base(ws))
    try:
        up = await websockets.connect(target, extra_headers=headers, subprotocols=ws.scope.get("subprotocols") or None,
                                      max_size=None, open_timeout=20, ping_interval=None)
    except Exception as e:  # noqa: BLE001
        log.warning("relay: can't open the document server's WebSocket: %s", e)
        await ws.close(code=1011)
        return
    await ws.accept(subprotocol=up.subprotocol)

    async def to_server():
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                break
            await up.send(m["text"] if m.get("text") is not None else m.get("bytes") or b"")

    async def to_browser():
        async for m in up:
            await (ws.send_text(m) if isinstance(m, str) else ws.send_bytes(m))

    tasks = [asyncio.create_task(to_server()), asyncio.create_task(to_browser())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await up.close()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
