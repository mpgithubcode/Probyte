"""The HTTP side of document editing: the viewer's editor sessions, what the Document Server downloads and calls back,
and the admin's settings. The work is in sessions.py, docserver.py and config.py."""
import secrets
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.core.db import connection
from app.data import store
from app.files import catalog as files
from app.identity.auth import User
from app.identity.oauth import public_base
from app.office import config, docserver, sessions
from app.office.config import CONVERT, CONVERTED_DIR, EDITABLE, PDF, PREFIX, SLIDE, WORD, CELL

router = APIRouter()


def _probyte_base(cfg: dict, request: Request) -> str:
    """Probyte's address for the Document Server."""
    return (cfg["probyte_url"] or public_base(request)).rstrip("/")


def _browser_base(cfg: dict, request: Request) -> str:
    """Where browsers load the Document Server from: through Probyte, or its own address."""
    return public_base(request).rstrip("/") + PREFIX if cfg["via_probyte"] else cfg["url"].rstrip("/")


@router.get("/api/office/status")
def status(user=User):
    with connection() as conn:
        cfg = config.settings(conn)
    return {"enabled": bool(cfg["url"]), "url": cfg["url"] or None,
            "editable": sorted(EDITABLE), "convertible": CONVERT, "viewable": sorted(WORD | CELL | SLIDE | PDF)}


class SessionIn(BaseModel):
    record_id: str
    mode: str = "edit"            # "view" opens it read-only
    table_id: str | None = None   # a linked table's file (record_id: the source's file id)


@router.post("/api/office/sessions")
def open_session(body: SessionIn, request: Request, user=User):
    with connection() as conn:
        cfg = config.settings(conn)
    lang = (request.headers.get("accept-language") or "en").split(",")[0].split("-")[0] or "en"
    return sessions.open_editor(body.record_id, body.table_id, body.mode, user,
                                probyte=_probyte_base(cfg, request), browser=_browser_base(cfg, request), lang=lang)


@router.get("/api/office/sessions/{session_id}")
def session_state(session_id: str, user=User):
    return sessions.state(session_id, user)


@router.post("/api/office/sessions/{session_id}/save")
def save(session_id: str, user=User):
    return sessions.save_now(session_id, user)


@router.post("/api/office/sessions/{session_id}/discard")
def discard(session_id: str, user=User):
    sessions.close(session_id, user, discard=True)
    return {"ok": True}


@router.post("/api/office/sessions/{session_id}/close")
def close(session_id: str, user=User):
    sessions.close(session_id, user, discard=False)
    return {"ok": True}


# ---------------------------------------------------------------- what the Document Server calls

@router.get("/api/office/file/{token}", include_in_schema=False)
def file_for_server(token: str, request: Request, original: bool = False):
    """The file, for the Document Server (the session's secret token is the permission): the converted copy for an
    old format (original=1: the file itself, for the conversion)."""
    with connection() as conn:
        s = sessions.by_token(conn, token)
        cfg = config.settings(conn)
        if cfg["secret"] and request.headers.get("authorization"):
            sessions.check_jwt(request.headers["authorization"].removeprefix("Bearer ").strip(), cfg)
        table = store.get_table(conn, s["table_id"])
        if s["convert_from"] and not original:
            path = CONVERTED_DIR / f"{s['id']}.{s['file_type']}"
            if not path.exists():
                raise HTTPException(404, "Not found")
            return FileResponse(path, media_type="application/octet-stream")
        if s["linked"]:
            from app.sharing import linked as remote
            return remote.file(conn, table, s["record_id"], "content", download=True)
        rec = conn.execute("SELECT * FROM records WHERE id = %s AND NOT deleted", (s["record_id"],)).fetchone()
        if not rec:
            raise HTTPException(404, "Not found")
        keys = files._keys(store.get_fields(conn, table["id"]))
    return files.stream(table, rec, keys, download=True)


@router.post("/api/office/callback/{token}", include_in_schema=False)
async def callback(token: str, request: Request):
    body = await request.json()
    return JSONResponse(await run_in_threadpool(sessions.handle_callback, token, request.headers.get("authorization") or "", body))


# ---------------------------------------------------------------- admin: Settings → Document editing

class SettingsIn(BaseModel):
    url: str = ""
    secret: str | None = None        # empty = keep the saved one
    probyte_url: str = ""
    server_url: str = ""
    via_probyte: bool = True
    browser_scheme: str = ""         # "", "https" or "http"
    verify_tls: bool = True


def _admin(user):
    if not user.get("is_admin") or user.get("api_key"):
        raise HTTPException(403, "Only the server's admin account can do this")


@router.get("/api/office/settings")
def get_settings(user=User):
    _admin(user)
    with connection() as conn:
        return config.public(config.settings(conn))


@router.put("/api/office/settings")
def put_settings(body: SettingsIn, user=User):
    _admin(user)
    with connection() as conn:
        config.save(conn, url=body.url, probyte_url=body.probyte_url, server_url=body.server_url, via_probyte=body.via_probyte,
                    browser_scheme=body.browser_scheme, verify_tls=body.verify_tls, secret=body.secret)
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
        cfg = config.settings(conn)
    if not cfg["url"]:
        raise HTTPException(400, "Enter the document server's address first")
    docserver.healthcheck(cfg)
    r = docserver.command(cfg, {"c": "version"})
    if r.get("error") not in (0, None):
        raise HTTPException(400, "The document server refused Probyte's signature: check the secret (JWT)" if r.get("error") == 6
                            else f"The document server answered error {r.get('error')}")
    # the other way: the Document Server downloads a small file from Probyte (as it does every document)
    token = secrets.token_urlsafe(24)
    _probes[token] = time.time() + 120
    probyte = _probyte_base(cfg, request)
    try:
        c = docserver.convert(cfg, {"async": False, "filetype": "txt", "outputtype": "docx", "key": f"probe-{token[:20]}",
                                    "title": "probe.txt", "url": f"{probyte}/api/office/probe/{token}"})
    finally:
        _probes.pop(token, None)
    if c.get("error"):
        raise HTTPException(400, f"The document server can't download files from Probyte at {probyte} (error {c['error']}). "
                                 "Set “Probyte's address for the document server” (network settings) to an address it can reach, "
                                 "e.g. http://probyte:8000 in the same Docker network or http://<this machine's IP>:<Probyte's port>; "
                                 "a Document Server on a private network may also need ALLOW_PRIVATE_IP_ADDRESS=true.")
    return {"ok": True, "version": r.get("version")}
