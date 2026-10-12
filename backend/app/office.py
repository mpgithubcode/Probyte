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
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
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
SETTINGS = ("url", "secret", "probyte_url", "server_url")


# ---------------------------------------------------------------- settings (Settings → Document editing, admin)

def settings(conn) -> dict:
    rows = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM app_settings WHERE key LIKE 'office.%'").fetchall()}
    out = {k: rows.get(f"office.{k}", "") for k in SETTINGS}
    out["secret"] = unseal(out["secret"]) or ""
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

def _key(record_id, etag: str, discards: int) -> str:
    """The Document Server's document key: the same file version → the same key (people can edit together); a new
    version, or a discarded edit, → a new key."""
    return re.sub(r"[^0-9A-Za-z_-]", "", f"{record_id}-{(etag or 'x')[:20]}-{discards}")[:120]


class SessionIn(BaseModel):
    record_id: str
    mode: str = "edit"   # "view" opens it read-only


def _probyte_base(cfg: dict, request: Request) -> str:
    from .oauth import public_base
    return (cfg["probyte_url"] or public_base(request)).rstrip("/")


@router.get("/api/office/status")
def status(user=User):
    with connection() as conn:
        cfg = settings(conn)
    return {"enabled": bool(cfg["url"]), "url": cfg["url"] or None,
            "editable": sorted(EDITABLE), "viewable": sorted(WORD | CELL | SLIDE | PDF)}


@router.post("/api/office/sessions")
def open_session(body: SessionIn, request: Request, user=User):
    """An editor for a file: the settings for the Document Server's editor, signed."""
    with connection() as conn:
        cfg = settings(conn)
        if not cfg["url"]:
            raise HTTPException(400, "No document server is set up (Settings → Document editing)")
        table, rec, keys = files._own_record(conn, body.record_id, user["id"])
        item = (rec["data"].get(keys["file"]) or [{}])[0]
        name = item.get("name") or "file"
        dtype = doc_type(name)
        if not dtype:
            raise HTTPException(400, "The document server can't open this kind of file")
        mode = "edit" if body.mode == "edit" and files._ext(name) in EDITABLE else "view"
        discards = conn.execute("SELECT count(*) AS n FROM office_sessions WHERE record_id = %s AND discarded AND doc_key LIKE %s",
                                (rec["id"], f"{rec['id']}-{(rec['caldav_etag'] or 'x')[:20]}-%")).fetchone()["n"]
        key = _key(rec["id"], rec["caldav_etag"], discards)
        token = secrets.token_urlsafe(32)
        s = conn.execute("INSERT INTO office_sessions (token, record_id, table_id, user_id, doc_key, mode) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
                         (token, rec["id"], table["id"], user["id"], key, mode)).fetchone()
    base = _probyte_base(cfg, request)
    config = {
        "documentType": dtype,
        "document": {
            "fileType": files._ext(name), "key": key, "title": name,
            "url": f"{base}/api/office/file/{token}",
            "permissions": {"edit": mode == "edit", "download": True, "print": True, "comment": mode == "edit", "review": mode == "edit"},
        },
        "editorConfig": {
            "mode": mode, "callbackUrl": f"{base}/api/office/callback/{token}",
            "user": {"id": str(user["id"]), "name": user.get("username") or "Probyte"},
            "lang": (request.headers.get("accept-language") or "en").split(",")[0].split("-")[0] or "en",
            "customization": {"forcesave": True, "autosave": True, "compactHeader": True, "hideRightMenu": True,
                              "goback": False, "chat": False},
        },
        "height": "100%", "width": "100%", "type": "desktop",
    }
    if cfg["secret"]:
        config["token"] = sign(config, cfg["secret"])
    return {"session": str(s["id"]), "mode": mode, "api_url": cfg["url"].rstrip("/") + "/web-apps/apps/api/documents/api.js", "config": config}


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
    base = (cfg["server_url"] or cfg["url"]).rstrip("/")
    body = dict(payload)
    headers = {}
    if cfg["secret"]:
        body["token"] = sign(payload, cfg["secret"])
        headers["Authorization"] = "Bearer " + sign({"payload": payload}, cfg["secret"])
    last = None
    for path in ("/coauthoring/CommandService.ashx", "/command"):
        try:
            r = requests.post(base + path, json=body, headers=headers, timeout=20)
        except requests.RequestException as e:
            raise HTTPException(502, f"The document server isn't reachable from Probyte ({e.__class__.__name__})")
        if r.status_code == 404:
            last = r
            continue
        try:
            return r.json()
        except ValueError:
            raise HTTPException(502, f"The document server answered HTTP {r.status_code}")
    raise HTTPException(502, f"The document server has no command service (HTTP {last.status_code if last else '?'})")


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
def file_for_server(token: str, request: Request):
    """The file, for the Document Server (the session's secret token is the permission)."""
    with connection() as conn:
        s = _by_token(conn, token)
        cfg = settings(conn)
        if cfg["secret"] and request.headers.get("authorization"):
            _check_jwt(request, None, cfg)
        table = store.get_table(conn, s["table_id"])
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
        url = base + u.path + (f"?{u.query}" if u.query else "")
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
            with connection() as conn:
                table = store.get_table(conn, s["table_id"])
                rec = conn.execute("SELECT * FROM records WHERE id = %s AND NOT deleted", (s["record_id"],)).fetchone()
                if not rec:
                    raise HTTPException(404, "The file was deleted")
                keys = files._keys(store.get_fields(conn, table["id"]))
                user = conn.execute("SELECT username FROM users WHERE id = %s", (s["user_id"],)).fetchone()
            audit.set_actor(f"{user['username'] if user else 'Someone'} in ONLYOFFICE", s["user_id"])
            import io
            files.replace_contents(table, rec, keys, io.BytesIO(data), None)
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


def _admin(user):
    if not user.get("is_admin") or user.get("api_key"):
        raise HTTPException(403, "Only the server's admin account can do this")


@router.get("/api/office/settings")
def get_settings(user=User):
    _admin(user)
    with connection() as conn:
        cfg = settings(conn)
    return {"url": cfg["url"], "probyte_url": cfg["probyte_url"], "server_url": cfg["server_url"], "has_secret": bool(cfg["secret"])}


def _clean_url(u: str) -> str:
    u = (u or "").strip().rstrip("/")
    if u and not re.match(r"^https?://[^/\s]+", u):
        raise HTTPException(400, f"“{u}” isn't an address starting with http:// or https://")
    return u


@router.put("/api/office/settings")
def put_settings(body: SettingsIn, user=User):
    _admin(user)
    vals = {"url": _clean_url(body.url), "probyte_url": _clean_url(body.probyte_url), "server_url": _clean_url(body.server_url)}
    with connection() as conn:
        for k, v in vals.items():
            conn.execute("INSERT INTO app_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                         (f"office.{k}", v))
        if body.secret is not None and (body.secret or not vals["url"]):
            conn.execute("INSERT INTO app_settings (key, value) VALUES ('office.secret', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                         (seal(body.secret) if body.secret else "",))
    return get_settings(user)


@router.post("/api/office/settings/test")
def test_settings(user=User):
    """Can Probyte reach the Document Server, and does it accept our signature?"""
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
    return {"ok": True, "version": r.get("version")}
