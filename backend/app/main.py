import json
import re
import logging
import os
import threading
import uuid
from datetime import date, datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import psycopg
import requests
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response as RawResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import attachments, auth, builtin_dav, mcp_server, oauth, recordops, remote, rest_v1, shares, store, sync, webhooks
from . import formula as F
from .auth import current_user
from .compute import COMPUTED_TYPES, Engine
from .db import Jsonb, connection, init_db
from .ical import PALETTE

logging.basicConfig(level=logging.INFO)


def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    raise TypeError(f"Not JSON serialisable: {type(o).__name__}")


class FastJSON(JSONResponse):
    """json.dumps with UUID/datetime support: much quicker than FastAPI's generic encoder for big tables."""

    def render(self, content) -> bytes:
        return json.dumps(content, default=_json_default, ensure_ascii=False, separators=(",", ":")).encode()

COMPONENTS = {"tasks": ["VTODO"], "events": ["VEVENT"], "mixed": ["VEVENT", "VTODO"]}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with connection() as conn:
        store.ensure_system_fields(conn)
    builtin_dav.sync_on_startup()
    stop = threading.Event()
    if os.environ.get("RUN_WORKER", "").lower() == "embedded":  # single-container setups: run the worker in here too
        from . import worker
        worker.start(stop)
    yield
    stop.set()


app = FastAPI(title="Probyte", lifespan=lifespan)

# Endpoints used with bearer tokens (never cookies) can be called from any origin: browser-based MCP clients,
# OAuth flows in web apps, and API calls from web tools.
CORS_PREFIXES = ("/api/v1/", "/mcp", "/oauth/token", "/oauth/register", "/.well-known/oauth", "/.well-known/openid")
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, X-API-Key, Mcp-Session-Id, Mcp-Protocol-Version, Last-Event-ID",
    "Access-Control-Expose-Headers": "Mcp-Session-Id, WWW-Authenticate",
    "Access-Control-Max-Age": "86400",
}


@app.middleware("http")
async def cors(request: Request, call_next):
    if not request.url.path.startswith(CORS_PREFIXES):
        return await call_next(request)
    if request.method == "OPTIONS":
        return RawResponse(status_code=204, headers=CORS_HEADERS)
    response = await call_next(request)
    for k, v in CORS_HEADERS.items():
        response.headers.setdefault(k, v)
    return response

User = Depends(current_user)


@app.exception_handler(psycopg.errors.InvalidTextRepresentation)
def _bad_id(request: Request, exc):
    # Malformed UUIDs in the URL: treat like any other missing object.
    return JSONResponse({"detail": "Not found"}, status_code=404)


# ======================================================================== models

class Credentials(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class AccountIn(BaseModel):
    name: str
    url: str
    carddav_url: str | None = None  # contacts address, when the provider uses a different one (iCloud)
    username: str | None = None
    password: str | None = None


class AccountTest(AccountIn):
    id: str | None = None  # reuse the stored password of an existing account when none is given


class BaseIn(BaseModel):
    name: str
    color: str | None = None


class BasePatch(BaseModel):
    name: str | None = None
    color: str | None = None
    position: int | None = None


class RemoteIn(BaseModel):
    url: str
    password: str | None = None
    item_id: str | None = None
    view_id: str | None = None


class RelinkIn(BaseModel):
    url: str | None = None
    password: str | None = None


class TableIn(BaseModel):
    base_id: str | None = None
    name: str
    kind: Literal["generic", "tasks", "events", "mixed", "contacts", "remote"] = "generic"
    account_id: str | None = None
    calendar_url: str | None = None
    new_calendar_name: str | None = None
    remote: RemoteIn | None = None


class TablePatch(BaseModel):
    name: str | None = None
    base_id: str | None = None


class FieldIn(BaseModel):
    name: str
    type: str
    options: dict[str, Any] = {}


class FieldPatch(BaseModel):
    name: str | None = None
    type: str | None = None
    options: dict[str, Any] | None = None


class FormulaCheck(BaseModel):
    expression: str
    field_id: str | None = None


class RecordIn(BaseModel):
    data: dict[str, Any] = {}
    table_id: str | None = None  # combined databases: the record's source table (needed for linked tables)


class ComboRecordIn(BaseModel):
    table_id: str
    link_table_id: str | None = None  # source is a linked combined database: which of its tables
    data: dict[str, Any] = {}


class ViewIn(BaseModel):
    name: str
    type: Literal["grid", "kanban", "timeline", "calendar", "list", "gallery", "chart"]
    config: dict[str, Any] = {}


class ViewPatch(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None


class ComboSource(BaseModel):
    table_id: str
    color: str | None = None


class ComboField(BaseModel):
    id: str
    name: str
    mapping: dict[str, str] = {}


class ComboIn(BaseModel):
    base_id: str | None = None
    name: str
    sources: list[ComboSource]


class ComboPatch(BaseModel):
    name: str | None = None
    base_id: str | None = None
    sources: list[ComboSource] | None = None
    fields: list[ComboField] | None = None
    remap: bool = False  # rebuild the field mapping automatically from the sources


# ======================================================================== helpers

def _table_out(t):
    """A table row for the browser: a linked table's access cookie and token never leave the server."""
    if t is None:
        return t
    out = {k: v for k, v in dict(t).items() if k != "remote"}
    out["remote"] = remote.public_info(t)
    return out


def _after_write(table):
    if table["kind"] in store.DAV_KINDS and table["account_id"] and table["calendar_url"]:
        sync.schedule_sync(table["id"])


def _account_public(a):
    return {k: a[k] for k in ("id", "name", "url", "carddav_url", "username", "is_builtin", "created_at")} | {"has_password": bool(a["password"])}


def _own_account(conn, account_id, user):
    a = conn.execute("SELECT * FROM caldav_accounts WHERE id = %s AND user_id = %s", (account_id, user["id"])).fetchone()
    return a or store.not_found("Account not found")


def _own_field(conn, field_id, user):
    f = conn.execute(
        "SELECT f.* FROM fields f JOIN tables t ON t.id = f.table_id WHERE f.id = %s AND t.user_id = %s", (field_id, user["id"])
    ).fetchone()
    return f or store.not_found("Field not found")


def _own_view(conn, view_id, user):
    v = conn.execute(
        "SELECT v.* FROM views v LEFT JOIN tables t ON t.id = v.table_id LEFT JOIN combos c ON c.id = v.combo_id "
        "WHERE v.id = %s AND (t.user_id = %s OR c.user_id = %s)", (view_id, user["id"], user["id"])
    ).fetchone()
    return v or store.not_found("View not found")


# ======================================================================== auth

@app.get("/api/auth/config")
def auth_config():
    with connection() as conn:
        has_users = conn.execute("SELECT EXISTS (SELECT 1 FROM users) AS e").fetchone()["e"]
    return {"allow_signup": auth.ALLOW_SIGNUP or not has_users, "has_users": has_users}


@app.post("/api/auth/register")
def register(body: Credentials, request: Request, response: Response):
    username = auth.validate_credentials(body.username, body.password)
    with connection() as conn:
        first = not conn.execute("SELECT EXISTS (SELECT 1 FROM users) AS e").fetchone()["e"]
        if not first and not auth.ALLOW_SIGNUP:
            raise HTTPException(403, "Registration is disabled")
        if conn.execute("SELECT 1 FROM users WHERE username = %s", (username,)).fetchone():
            raise HTTPException(409, "That username is taken")
        user = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (%s,%s,%s) RETURNING *",
            (username, auth.hash_password(body.password), first),
        ).fetchone()
        if first:
            # Data created before multi-user support belongs to the first account.
            for t in ("caldav_accounts", "tables", "combos"):
                conn.execute(f"UPDATE {t} SET user_id = %s WHERE user_id IS NULL", (user["id"],))
        if builtin_dav.enabled():
            builtin_dav.provision(conn, user["id"])
        auth.start_session(conn, user["id"], request, response)
    return auth.public_user(user)


@app.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response):
    with connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = %s", (body.username.strip().lower(),)).fetchone()
        if not user or not auth.verify_password(body.password, user["password_hash"]):
            raise HTTPException(401, "Wrong username or password")
        auth.start_session(conn, user["id"], request, response)
    return auth.public_user(user)


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    with connection() as conn:
        auth.end_session(conn, request, response)
    return {"ok": True}


@app.get("/api/auth/me")
def me(user=User):
    return auth.public_user(user)


@app.post("/api/auth/password")
def change_password(body: PasswordChange, request: Request, response: Response, user=User):
    auth.validate_credentials(user["username"], body.new_password)
    with connection() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = %s", (user["id"],)).fetchone()
        if not auth.verify_password(body.current_password, row["password_hash"]):
            raise HTTPException(400, "Current password is wrong")
        conn.execute("UPDATE users SET password_hash = %s WHERE id = %s", (auth.hash_password(body.new_password), user["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id = %s", (user["id"],))  # log out other devices
        auth.start_session(conn, user["id"], request, response)
    return {"ok": True}


# ======================================================================== built-in CalDAV (bundled Radicale)

@app.get("/api/dav")
def dav_info(user=User):
    with connection() as conn:
        return builtin_dav.info(conn, user["id"])


@app.post("/api/dav/check")
def dav_check(user=User):
    """Test the public CalDAV address the way a phone would."""
    with connection() as conn:
        acc = conn.execute("SELECT username, password FROM caldav_accounts WHERE user_id = %s AND is_builtin", (user["id"],)).fetchone()
    if not acc:
        raise HTTPException(400, "Enable your CalDAV login first")
    return builtin_dav.check_public(acc["username"], acc["password"])


@app.post("/api/dav/reset")
def dav_reset(user=User):
    """Create the user's Radicale login, or replace its device password."""
    if not builtin_dav.enabled():
        raise HTTPException(400, "The built-in CalDAV server is not configured")
    with connection() as conn:
        builtin_dav.provision(conn, user["id"])
        return builtin_dav.info(conn, user["id"])


# ======================================================================== API keys

class KeyIn(BaseModel):
    name: str
    scope: Literal["read", "write"] = "read"
    base_ids: list[str] | None = None   # None = all bases
    expires_at: datetime | None = None


def _key_public(k):
    return {x: k[x] for x in ("id", "name", "prefix", "scope", "base_ids", "expires_at", "last_used_at", "created_at", "source")}


@app.get("/api/keys")
def list_keys(user=User):
    with connection() as conn:
        return [_key_public(k) for k in conn.execute("SELECT * FROM api_keys WHERE user_id = %s ORDER BY created_at DESC", (user["id"],)).fetchall()]


@app.post("/api/keys")
def create_key(body: KeyIn, user=User):
    """Creates a key. The full key is returned only this once; Probyte stores just its hash."""
    with connection() as conn:
        for b in body.base_ids or []:
            store.get_base(conn, b, user["id"])
        key, key_hash = auth.new_api_key()
        k = conn.execute(
            "INSERT INTO api_keys (user_id, name, prefix, key_hash, scope, base_ids, expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (user["id"], body.name.strip() or "API key", key[:12], key_hash, body.scope,
             Jsonb(body.base_ids) if body.base_ids is not None else None, body.expires_at),
        ).fetchone()
    return _key_public(k) | {"key": key}


@app.patch("/api/keys/{key_id}")
def update_key(key_id: str, body: KeyIn, user=User):
    with connection() as conn:
        for b in body.base_ids or []:
            store.get_base(conn, b, user["id"])
        k = conn.execute("UPDATE api_keys SET name = %s, scope = %s, base_ids = %s, expires_at = %s WHERE id = %s AND user_id = %s RETURNING *",
                         (body.name.strip() or "API key", body.scope, Jsonb(body.base_ids) if body.base_ids is not None else None,
                          body.expires_at, key_id, user["id"])).fetchone() or store.not_found("API key not found")
    return _key_public(k)


@app.delete("/api/keys/{key_id}")
def delete_key(key_id: str, user=User):
    with connection() as conn:
        conn.execute("DELETE FROM api_keys WHERE id = %s AND user_id = %s", (key_id, user["id"]))
    return {"ok": True}


# ======================================================================== records API (keyed by field id or name)

def _by_name(fields, data: dict) -> dict:
    names = {str(f["id"]): f["name"] for f in fields}
    return {names.get(k, k): v for k, v in data.items()}


def _from_names(fields, data: dict) -> dict:
    ids = {f["name"]: str(f["id"]) for f in fields}
    return {ids.get(k, k): v for k, v in data.items()}


@app.get("/api/tables/{table_id}/records")
def api_list_records(table_id: str, field_key: Literal["id", "name"] = "id", view: str | None = None, user=User):
    """Records with computed values. ?view=<id> applies that view's filters and sort; ?field_key=name keys data by field name."""
    from .viewfilter import apply_view
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
        eng = Engine(conn)
        fields = eng.describe(table_id)
        records = eng.records(table_id)
        if view:
            v = conn.execute("SELECT config FROM views WHERE id = %s AND table_id = %s", (view, table_id)).fetchone() or store.not_found("View not found")
            records = apply_view(eng, fields, records, v["config"])
        out = [store.clean_record(r) for r in records]
    if field_key == "name":
        out = [{**r, "data": _by_name(fields, r["data"])} for r in out]
    return FastJSON({"records": out})


@app.get("/api/records/{record_id}")
def api_get_record(record_id: str, field_key: Literal["id", "name"] = "id", user=User):
    with connection() as conn:
        r = conn.execute("SELECT table_id FROM records WHERE id = %s AND NOT deleted", (record_id,)).fetchone() or store.not_found("Record not found")
        store.get_table(conn, r["table_id"], user["id"])
        out = store.computed_record(conn, record_id, r["table_id"])
        fields = store.get_fields(conn, r["table_id"])
    return {**out, "data": _by_name(fields, out["data"])} if field_key == "name" else out


# ======================================================================== attachments & users

@app.post("/api/attachments")
async def upload_attachment(file: UploadFile = File(...), user=User):
    with connection() as conn:
        return await attachments.save_upload(conn, user["id"], file)


@app.get("/api/attachments/{attachment_id}")
def get_attachment(attachment_id: str, download: bool = False, user=User):
    with connection() as conn:
        return attachments.serve(conn, user["id"], attachment_id, download)


@app.get("/api/users/lookup")
def lookup_user(username: str, user=User):
    """Exact-username lookup for user fields (no listing, so accounts can't be enumerated)."""
    with connection() as conn:
        u = conn.execute("SELECT id, username FROM users WHERE username = %s", (username.strip().lower(),)).fetchone()
    if not u:
        raise HTTPException(404, "No user with that username")
    return u


# ======================================================================== sidebar

@app.get("/api/workspace")
def workspace(user=User):
    with connection() as conn:
        store.ensure_base(conn, user["id"])
        bases = [b for b in conn.execute("SELECT id, name, color, position FROM bases WHERE user_id = %s ORDER BY position, created_at",
                                         (user["id"],)).fetchall() if store.base_allowed(b["id"])]
        allowed = {str(b["id"]) for b in bases}
        tables = [_table_out(t) for t in conn.execute(
            "SELECT id, base_id, name, kind, account_id, calendar_url, last_synced_at, last_sync_error, remote FROM tables "
            "WHERE user_id = %s ORDER BY created_at", (user["id"],)
        ).fetchall() if str(t["base_id"]) in allowed]
        combos = [c for c in conn.execute("SELECT id, base_id, name, sources FROM combos WHERE user_id = %s ORDER BY created_at",
                                          (user["id"],)).fetchall() if str(c["base_id"]) in allowed]
        accounts = [] if user.get("api_key") else conn.execute(
            "SELECT * FROM caldav_accounts WHERE user_id = %s ORDER BY is_builtin DESC, created_at", (user["id"],)
        ).fetchall()
    return {"bases": bases, "tables": tables, "combos": combos, "accounts": [_account_public(a) for a in accounts]}


# ======================================================================== bases

@app.get("/api/bases")
def list_bases(user=User):
    return workspace(user)["bases"]


@app.post("/api/bases")
def create_base(body: BaseIn, user=User):
    if store.api_bases.get() is not None:
        raise HTTPException(403, "This API key is limited to specific bases and can't create new ones")
    with connection() as conn:
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM bases WHERE user_id = %s", (user["id"],)).fetchone()["p"]
        return conn.execute("INSERT INTO bases (user_id, name, color, position) VALUES (%s,%s,%s,%s) RETURNING id, name, color, position",
                            (user["id"], body.name.strip() or "Untitled base", body.color or PALETTE[pos % len(PALETTE)], pos)).fetchone()


@app.patch("/api/bases/{base_id}")
def update_base(base_id: str, body: BasePatch, user=User):
    with connection() as conn:
        b = store.get_base(conn, base_id, user["id"])
        return conn.execute("UPDATE bases SET name = %s, color = %s, position = %s WHERE id = %s RETURNING id, name, color, position",
                            ((body.name or b["name"]).strip(), body.color or b["color"],
                             b["position"] if body.position is None else body.position, base_id)).fetchone()


@app.get("/api/bases/{base_id}/views")
def base_views(base_id: str, user=User):
    """Tables and combined databases of a base with their views (for the public-link editor)."""
    with connection() as conn:
        store.get_base(conn, base_id, user["id"])
        out = []
        for kind, tbl, col in (("table", "tables", "table_id"), ("combo", "combos", "combo_id")):
            for t in conn.execute(f"SELECT id, name FROM {tbl} WHERE base_id = %s ORDER BY created_at", (base_id,)).fetchall():
                views = conn.execute(f"SELECT id, name, type FROM views WHERE {col} = %s ORDER BY position", (t["id"],)).fetchall()
                out.append({"kind": kind, "id": t["id"], "name": t["name"], "views": views})
        return out


@app.delete("/api/bases/{base_id}")
def delete_base(base_id: str, user=User):
    """Deletes the base with its tables and combined databases (CalDAV calendars themselves are untouched)."""
    with connection() as conn:
        store.get_base(conn, base_id, user["id"])
        if conn.execute("SELECT count(*) AS n FROM bases WHERE user_id = %s", (user["id"],)).fetchone()["n"] <= 1:
            raise HTTPException(400, "You need at least one base")
        conn.execute("DELETE FROM bases WHERE id = %s", (base_id,))
    return {"ok": True}


# ======================================================================== caldav accounts

@app.post("/api/accounts")
def create_account(body: AccountIn, user=User):
    with connection() as conn:
        a = conn.execute(
            "INSERT INTO caldav_accounts (user_id, name, url, carddav_url, username, password) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
            (user["id"], body.name, body.url, (body.carddav_url or "").strip() or None, body.username, body.password),
        ).fetchone()
    return _account_public(a)


@app.patch("/api/accounts/{account_id}")
def update_account(account_id: str, body: AccountIn, user=User):
    with connection() as conn:
        a = _own_account(conn, account_id, user)
        if a["is_builtin"]:
            conn.execute("UPDATE caldav_accounts SET name = %s WHERE id = %s", (body.name, account_id))
        else:
            conn.execute(
                "UPDATE caldav_accounts SET name=%s, url=%s, carddav_url=%s, username=%s, password=COALESCE(%s, password) WHERE id=%s",
                (body.name, body.url, (body.carddav_url or "").strip() or None, body.username, body.password or None, account_id),
            )
        return _account_public(_own_account(conn, account_id, user))


@app.delete("/api/accounts/{account_id}")
def delete_account(account_id: str, user=User):
    with connection() as conn:
        if _own_account(conn, account_id, user)["is_builtin"]:
            raise HTTPException(400, "The built-in CalDAV account can't be removed")
        conn.execute("DELETE FROM caldav_accounts WHERE id = %s", (account_id,))
    return {"ok": True}


@app.post("/api/accounts/test")
def test_account(body: AccountTest, user=User):
    account = body.model_dump()
    if body.id and not body.password:
        with connection() as conn:
            account["password"] = _own_account(conn, body.id, user)["password"]
    cals = books = None
    errors = []
    try:
        cals = sync.list_calendars(account)
    except Exception as e:
        errors.append(f"calendars: {e}")
    try:
        books = sync.list_addressbooks(account)
    except Exception as e:
        errors.append(f"contacts: {e}")
    if cals is None and books is None:
        raise HTTPException(400, "Connection failed — " + "; ".join(errors))
    return {"ok": True, "calendars": cals or [], "addressbooks": books, "calendar_error": None if cals is not None else errors[0]}


@app.get("/api/accounts/{account_id}/calendars")
def account_calendars(account_id: str, user=User):
    with connection() as conn:
        a = _own_account(conn, account_id, user)
    try:
        return sync.list_calendars(a)
    except Exception as e:
        raise HTTPException(400, f"Could not list calendars: {e}")


@app.get("/api/accounts/{account_id}/addressbooks")
def account_addressbooks(account_id: str, user=User):
    with connection() as conn:
        a = _own_account(conn, account_id, user)
    try:
        return sync.list_addressbooks(a)
    except Exception as e:
        raise HTTPException(400, f"Could not list address books: {e}")


# ======================================================================== tables

@app.post("/api/tables")
def create_table(body: TableIn, user=User):
    with connection() as conn:
        base = store.get_base(conn, body.base_id, user["id"]) if body.base_id else store.ensure_base(conn, user["id"])
        if body.kind == "remote":
            if not body.remote:
                raise HTTPException(400, "Paste the public link to add")
            r = body.remote
            return _table_out(remote.create_table(conn, store, user["id"], base["id"], body.name, r.url, r.password, r.item_id, r.view_id))
    calendar_url = body.calendar_url
    if body.kind != "generic":
        if not body.account_id:
            raise HTTPException(400, "An account is required")
        with connection() as conn:
            a = _own_account(conn, body.account_id, user)
        book = body.kind == "contacts"
        if body.new_calendar_name:
            try:
                calendar_url = (sync.create_addressbook(a, body.new_calendar_name) if book
                                else sync.create_calendar(a, body.new_calendar_name, COMPONENTS[body.kind]))
            except Exception as e:
                raise HTTPException(400, f"Could not create {'address book' if book else 'calendar'}: {e}")
        if not calendar_url:
            raise HTTPException(400, "Choose an address book" if book else "Choose a calendar")
    with connection() as conn:
        t = store.create_table(conn, user["id"], base["id"], body.name, body.kind,
                               body.account_id if body.kind != "generic" else None, calendar_url)
    if body.kind != "generic":
        sync.schedule_sync(t["id"], 0)
    return t


@app.get("/api/tables/{table_id}")
def get_table(table_id: str, user=User):
    with connection() as conn:
        t = store.get_table(conn, table_id, user["id"])
        eng = Engine(conn)
        records = store.list_records(conn, table_id, eng)
        refs = eng.refs(table_id, eng.records(table_id))
        if t["kind"] == "remote":
            t = conn.execute("SELECT * FROM tables WHERE id = %s", (table_id,)).fetchone()  # load() may have updated its status
        return FastJSON({"table": _table_out(t), "fields": eng.describe(table_id), "views": store.get_views(conn, table_id=table_id),
                         "records": records, "refs": refs,
                         "link_sources": remote.sources(conn, t) if t["kind"] == "remote" else None,
                         "dav": _dav_info(conn, t)})


# ======================================================================== linked tables (from a public link)

class InspectIn(BaseModel):
    url: str
    password: str | None = None


@app.post("/api/remote/inspect")
def inspect_link(body: InspectIn, user=User):
    """Look at a public link before adding it: its name, permissions and shared tables/views."""
    with connection() as conn:
        link = remote.parse_link(body.url)
        link["local"] = remote.is_local(conn, link["token"])
        if body.password:
            link["cookie"] = remote.unlock(conn, link, body.password)
        meta = remote.fetch_meta(conn, link)
    return {**meta, "local": link["local"], "origin": link["origin"], "view_hint": link["view_id"]}


@app.post("/api/tables/{table_id}/relink")
def relink_table(table_id: str, body: RelinkIn, user=User):
    """Re-enter a linked table's password, or point it at a different link."""
    with connection() as conn:
        t = store.get_table(conn, table_id, user["id"])
        if t["kind"] != "remote":
            raise HTTPException(400, "Only linked tables have a link")
        return _table_out(remote.relink(conn, t, body.url, body.password))


@app.post("/api/tables/{table_id}/refresh")
def refresh_table(table_id: str, user=User):
    with connection() as conn:
        t = store.get_table(conn, table_id, user["id"])
        if t["kind"] == "remote":
            remote.load(conn, t, force=True)
    return {"ok": True}


@app.get("/api/remote/{table_id}/attachments/{attachment_id}")
def remote_attachment(table_id: str, attachment_id: str, download: bool = False, user=User):
    with connection() as conn:
        t = store.get_table(conn, table_id, user["id"])
        if t["kind"] != "remote":
            store.not_found()
        return remote.attachment(conn, t, attachment_id, download)


@app.get("/api/tables/{table_id}/titles")
def table_titles(table_id: str, user=User):
    """Record titles of a table, for the link picker."""
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
        titles = Engine(conn).titles(table_id)
    return [{"id": k, "title": v} for k, v in titles.items()]


@app.post("/api/tables/{table_id}/formula")
def check_formula(table_id: str, body: FormulaCheck, user=User):
    """Validate a formula and preview its result on the first few records."""
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
        try:
            compiled = store.compile_formula(conn, table_id, body.expression, body.field_id)
        except HTTPException as e:
            return {"ok": False, "error": e.detail}
        eng = Engine(conn)
        temp = {"id": "__preview", "table_id": table_id, "type": "formula", "options": {"expression": compiled}}
        recs = [{**r, "data": dict(r["data"])} for r in eng.records(table_id)[:3]]
        eng._compute_field(temp, recs)
        titles = eng.titles(table_id)
        preview = [{"title": titles.get(str(r["id"]), "Untitled"), "value": r["data"].get("__preview")} for r in recs]
        return {"ok": True, "resultType": eng.value_type(temp), "preview": preview}


@app.patch("/api/tables/{table_id}")
def update_table(table_id: str, body: TablePatch, user=User):
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
        if body.name:
            conn.execute("UPDATE tables SET name = %s WHERE id = %s", (body.name, table_id))
        if body.base_id:
            store.get_base(conn, body.base_id, user["id"])
            conn.execute("UPDATE tables SET base_id = %s WHERE id = %s", (body.base_id, table_id))
        return _table_out(store.get_table(conn, table_id))


def _dav_info(conn, t):
    """For a synced table: which account, whether it's Probyte's own server, and whether another table uses the same collection."""
    if t["kind"] not in store.DAV_KINDS or not t["account_id"] or not t["calendar_url"]:
        return None
    a = conn.execute("SELECT name, is_builtin FROM caldav_accounts WHERE id = %s", (t["account_id"],)).fetchone()
    shared = conn.execute("SELECT count(*) AS n FROM tables WHERE id <> %s AND account_id = %s AND calendar_url = %s",
                          (t["id"], t["account_id"], t["calendar_url"])).fetchone()["n"]
    items = conn.execute("SELECT count(*) AS n FROM records WHERE table_id = %s AND caldav_href IS NOT NULL", (t["id"],)).fetchone()["n"]
    return {"account": a["name"] if a else None, "builtin": bool(a and a["is_builtin"]), "shared": shared > 0, "items": items,
            "collection": "address book" if t["kind"] == "contacts" else "calendar"}


def _delete_on_server(conn, t) -> str:
    """Remove a table's data from its DAV server: the whole collection on Probyte's own server, else each item."""
    info = _dav_info(conn, t)
    if not info:
        return "none"
    if info["shared"]:
        return "kept (another table uses the same " + info["collection"] + ")"
    a = conn.execute("SELECT * FROM caldav_accounts WHERE id = %s", (t["account_id"],)).fetchone()
    session = requests.Session()
    if a["username"]:
        session.auth = (a["username"], a["password"] or "")
    try:
        if info["builtin"]:
            r = session.delete(t["calendar_url"], timeout=30)
            if r.status_code not in (200, 204, 404):
                raise HTTPException(502, f"The server refused to delete the {info['collection']} (HTTP {r.status_code})")
            return f"deleted the {info['collection']}"
        hrefs = [x["caldav_href"] for x in conn.execute(
            "SELECT caldav_href FROM records WHERE table_id = %s AND caldav_href IS NOT NULL", (t["id"],)).fetchall()]
        for h in hrefs:
            r = session.delete(h, timeout=30)
            if r.status_code not in (200, 204, 404):
                raise HTTPException(502, f"The server refused to delete an item (HTTP {r.status_code}); nothing else was deleted locally")
        return f"deleted {len(hrefs)} item(s)"
    except requests.RequestException as e:
        raise HTTPException(502, f"Couldn't reach the server to delete the data there: {e}")


@app.delete("/api/tables/{table_id}")
def delete_table(table_id: str, delete_on_server: bool | None = None, user=User):
    """Delete a table. For a synced table, its data on the DAV server goes too: by default when it's on Probyte's own
    server (the whole calendar / address book), and for other accounts only with ?delete_on_server=true (each item)."""
    with connection() as conn:
        t = store.get_table(conn, table_id, user["id"])
        info = _dav_info(conn, t)
        server = "none"
        if info and (delete_on_server if delete_on_server is not None else info["builtin"]):
            server = _delete_on_server(conn, t)
        conn.execute("DELETE FROM tables WHERE id = %s", (table_id,))
        remote.invalidate(table_id)
    return {"ok": True, "server": server}


@app.post("/api/tables/{table_id}/sync")
def sync_now(table_id: str, user=User):
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
    try:
        return sync.sync_table(table_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Sync failed: {e}")


# ======================================================================== fields

def _describe_field(conn, field_id, table_id):
    return next(f for f in Engine(conn).describe(table_id) if str(f["id"]) == str(field_id))


@app.post("/api/tables/{table_id}/fields")
def create_field(table_id: str, body: FieldIn, user=User):
    with connection() as conn:
        table = store.get_table(conn, table_id, user["id"])
        if table["kind"] == "remote":
            raise HTTPException(400, "A linked table's fields come from its source link")
        opts = store.prepare_field(conn, table, body.type, body.options, user["id"])
        if body.type == "link":
            f = store.create_link_pair(conn, table, body.name, {**opts, "symmetric": body.options.get("symmetric", True)}, user["id"])
        else:
            f = store.create_field(conn, table_id, body.name, body.type, opts)
        return _describe_field(conn, f["id"], table_id)


@app.patch("/api/fields/{field_id}")
def update_field(field_id: str, body: FieldPatch, user=User):
    with connection() as conn:
        f = _own_field(conn, field_id, user)
        if f["system_key"] and f["system_key"] != "remote":
            raise HTTPException(400, "This field is locked: it's part of the synced calendar / contact item (or built in). "
                                     "Hide it in a view, or add your own field next to it.")
        table = store.get_table(conn, f["table_id"], user["id"])
        new_type = body.type or f["type"]
        if new_type != f["type"] and f["system_key"]:
            raise HTTPException(400, "The type of a CalDAV field can't be changed")
        options = f["options"]
        if new_type != f["type"] or body.options is not None:
            options = store.prepare_field(conn, table, new_type, f["options"] if body.options is None else body.options,
                                          user["id"], field=f)
        snapshot = store.snapshot_values(conn, f) if new_type != f["type"] else None
        if new_type != f["type"] and f["type"] == "link":
            store.delete_field_cleanup(conn, f)  # drops the counterpart field (values were captured above)
        updated = conn.execute(
            "UPDATE fields SET name = %s, type = %s, options = %s WHERE id = %s RETURNING *",
            (body.name or f["name"], new_type, Jsonb(options), field_id),
        ).fetchone()
        if new_type == "link" and f["type"] != "link" and (body.options or {}).get("symmetric", True) \
                and str(options["foreignTableId"]) != str(table["id"]) and not store.is_remote_table(conn, options["foreignTableId"]):
            sym = store.create_field(conn, options["foreignTableId"], table["name"], "link",
                                     {"foreignTableId": str(table["id"]), "relationship": "many", "symmetricFieldId": str(f["id"])})
            updated = conn.execute("UPDATE fields SET options = options || %s WHERE id = %s RETURNING *",
                                   (Jsonb({"symmetricFieldId": str(sym["id"])}), f["id"])).fetchone()
        if snapshot is not None:
            store.convert_field_values(conn, f, updated, snapshot)
        return _describe_field(conn, updated["id"], f["table_id"])


@app.delete("/api/fields/{field_id}")
def delete_field(field_id: str, user=User):
    with connection() as conn:
        f = _own_field(conn, field_id, user)
        if f["system_key"] == "remote":
            raise HTTPException(400, "A linked table's fields come from its source link")
        if f["system_key"]:
            raise HTTPException(400, "CalDAV fields can't be deleted (hide them in the view instead)")
        if f["is_primary"]:
            raise HTTPException(400, "The primary field can't be deleted")
        store.delete_field_cleanup(conn, f)
        conn.execute("DELETE FROM fields WHERE id = %s", (field_id,))
    return {"ok": True}


# ======================================================================== records

@app.post("/api/tables/{table_id}/records")
def create_record(table_id: str, body: RecordIn, field_key: Literal["id", "name"] = "id", user=User):
    with connection() as conn:
        data = _from_names(store.get_fields(conn, table_id), body.data) if field_key == "name" else body.data
        rid, table = recordops.create(conn, table_id, data, user["id"], link_table_id=body.table_id)
        out = _record_out(conn, table_id, rid)
        if field_key == "name":
            out = {**out, "data": _by_name(store.get_fields(conn, table_id), out["data"])}
    _after_write(table)
    return out


@app.patch("/api/records/{record_id}")
def update_record(record_id: str, body: RecordIn, field_key: Literal["id", "name"] = "id", user=User):
    with connection() as conn:
        data = body.data
        if field_key == "name":
            row = conn.execute("SELECT table_id FROM records WHERE id = %s", (record_id,)).fetchone() or store.not_found("Record not found")
            data = _from_names(store.get_fields(conn, row["table_id"]), data)
        r, table = store.update_record(conn, record_id, data, user["id"])
        out = store.computed_record(conn, record_id, r["table_id"])
        if field_key == "name":
            out = {**out, "data": _by_name(store.get_fields(conn, r["table_id"]), out["data"])}
    _after_write(table)
    return out


def _record_out(conn, table_id, record_id):
    """The record as it now looks (computed fields filled in; fresh from the source for linked tables)."""
    for r in Engine(conn).records(table_id):
        if str(r["id"]) == str(record_id):
            return store.clean_record(r)
    return {"id": str(record_id), "table_id": str(table_id), "data": {}}


class BulkItem(BaseModel):
    id: str
    table_id: str


class BulkIn(BaseModel):
    action: Literal["update", "delete"]
    items: list[BulkItem]
    data: dict[str, Any] = {}
    combo_id: str | None = None  # items come from this combined database: `data` uses its field ids


@app.post("/api/records/bulk")
def bulk_records(body: BulkIn, user=User):
    """Update or delete many records at once (any table kind, or a combined database's records)."""
    if len(body.items) > 5000:
        raise HTTPException(400, "Select at most 5000 records at a time")
    done, errors, touched = 0, [], {}
    with connection() as conn:
        combo = store.get_combo(conn, body.combo_id, user["id"]) if body.combo_id else None
        sources = {s["table_id"] for s in combo["sources"]} if combo else None
        for it in body.items:
            try:
                with conn.transaction():  # one bad record doesn't undo the others
                    if sources is not None and it.table_id not in sources:
                        store.not_found()
                    if body.action == "delete":
                        t = recordops.delete(conn, it.table_id, it.id, user["id"])
                    else:
                        data = store.unified_to_source(conn, combo, it.table_id, body.data) if combo else body.data
                        t = recordops.update(conn, it.table_id, it.id, data, user["id"])
                touched[str(t["id"])] = t
                done += 1
            except HTTPException as e:
                errors.append({"id": it.id, "error": e.detail})
    for t in touched.values():
        _after_write(t)
    return {"ok": not errors, "done": done, "errors": errors[:20]}


class ImportColumn(BaseModel):
    target: str | None = None                 # existing field id, or None to skip the column
    new: dict[str, str] | None = None         # {"name": ..., "type": ...}: create a field for it


class ImportIn(BaseModel):
    columns: list[ImportColumn]
    rows: list[list[Any]]


IMPORT_NEW_TYPES = {"text", "long_text", "number", "checkbox", "single_select", "multi_select", "date", "url", "email", "rating"}


@app.post("/api/tables/{table_id}/import")
def import_rows(table_id: str, body: ImportIn, user=User):
    """Add rows from a spreadsheet. Each column goes into an existing field, a new field, or nowhere."""
    if len(body.rows) > 50000:
        raise HTTPException(400, "That file has too many rows (50,000 at most)")
    with connection() as conn:
        table = store.get_table(conn, table_id, user["id"])
        existing = {str(f["id"]): f for f in store.get_fields(conn, table_id)}
        names = {f["name"].lower() for f in existing.values()}
        targets = []
        for col in body.columns:
            f = None
            if col.new:
                if table["kind"] == "remote":
                    raise HTTPException(400, "A linked table's fields come from its source; map columns to its existing fields")
                ftype = col.new.get("type") if col.new.get("type") in IMPORT_NEW_TYPES else "text"
                name, n = (col.new.get("name") or "Imported").strip()[:100] or "Imported", 2
                base = name
                while name.lower() in names:
                    name, n = f"{base} {n}", n + 1
                names.add(name.lower())
                raw_opts = {"choices": []} if ftype in store.SELECT_TYPES else {}
                if ftype == "date":  # keep times when the column has them
                    i = len(targets)
                    raw_opts["includeTime"] = any(isinstance(r[i] if i < len(r) else None, str) and re.search(r"[T ]\d{1,2}:\d{2}", r[i])
                                                  for r in body.rows[:500])
                opts = store.prepare_field(conn, table, ftype, raw_opts, user["id"])
                f = store.create_field(conn, table_id, name, ftype, opts)
            elif col.target:
                f = existing.get(col.target)
                if not f:
                    raise HTTPException(400, "A column is mapped to a field that no longer exists")
                if f["type"] in COMPUTED_TYPES or f["type"] == "attachment":
                    raise HTTPException(400, f"{f['name']} can't be filled from a file")
            targets.append(f)
        lookups = {}
        for f in targets:
            if f and f["type"] == "link" and str(f["id"]) not in lookups:
                lookups[str(f["id"])] = {v.lower(): k for k, v in Engine(conn).titles(f["options"]["foreignTableId"]).items()}
            elif f and f["type"] == "user":
                lookups[str(f["id"])] = {r["username"].lower(): str(r["id"]) for r in conn.execute("SELECT id, username FROM users").fetchall()}
        created, errors = 0, []
        for n, row in enumerate(body.rows, start=1):
            data = {}
            for f, cell in zip(targets, row):
                if f is None or cell is None or (isinstance(cell, str) and not cell.strip()):
                    continue
                v = store.import_value(f, cell, lookups.get(str(f["id"]), {}))
                if v is not None:
                    data[str(f["id"])] = v
            if not data:
                continue
            try:
                with conn.transaction():
                    recordops.create(conn, table_id, data, user["id"])
                created += 1
            except HTTPException as e:
                errors.append(f"Row {n}: {e.detail}")
    if created:
        _after_write(table)
    # The fields the columns went to, so the rest of a large file can be sent in further requests.
    return {"created": created, "errors": errors[:20], "error_count": len(errors),
            "targets": [str(f["id"]) if f else None for f in targets]}


@app.patch("/api/tables/{table_id}/records/{record_id}")
def update_table_record(table_id: str, record_id: str, body: RecordIn, user=User):
    """Update a record of any table — local, CalDAV or linked."""
    with connection() as conn:
        table = recordops.update(conn, table_id, record_id, body.data, user["id"])
        out = _record_out(conn, table_id, record_id)
    _after_write(table)
    return out


@app.delete("/api/tables/{table_id}/records/{record_id}")
def delete_table_record(table_id: str, record_id: str, user=User):
    with connection() as conn:
        table = recordops.delete(conn, table_id, record_id, user["id"])
    _after_write(table)
    return {"ok": True}


@app.delete("/api/records/{record_id}")
def delete_record(record_id: str, user=User):
    with connection() as conn:
        table = store.delete_record(conn, record_id, user["id"])
    _after_write(table)
    return {"ok": True}


# ======================================================================== views

@app.post("/api/tables/{table_id}/views")
def create_table_view(table_id: str, body: ViewIn, user=User):
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
        return store.create_view(conn, table_id=table_id, name=body.name, type=body.type, config=body.config)


@app.post("/api/combos/{combo_id}/views")
def create_combo_view(combo_id: str, body: ViewIn, user=User):
    with connection() as conn:
        store.get_combo(conn, combo_id, user["id"])
        return store.create_view(conn, combo_id=combo_id, name=body.name, type=body.type, config=body.config)


@app.patch("/api/views/{view_id}")
def update_view(view_id: str, body: ViewPatch, user=User):
    with connection() as conn:
        v = _own_view(conn, view_id, user)
        return conn.execute(
            "UPDATE views SET name = %s, config = %s WHERE id = %s RETURNING *",
            (body.name or v["name"], Jsonb(v["config"] if body.config is None else body.config), view_id),
        ).fetchone()


@app.delete("/api/views/{view_id}")
def delete_view(view_id: str, user=User):
    with connection() as conn:
        _own_view(conn, view_id, user)
        conn.execute("DELETE FROM views WHERE id = %s", (view_id,))
    return {"ok": True}


# ======================================================================== combined databases

def _combo_payload(conn, combo):
    eng = Engine(conn)
    sources, fields = store.resolve_combo(conn, combo, eng)
    for s in sources:  # a source that is a linked combined database: where its new records can go
        if s["kind"] == "remote":
            s["link_sources"] = remote.sources(conn, store.get_table(conn, s["table_id"]))
    return {"combo": {"id": combo["id"], "name": combo["name"], "base_id": combo["base_id"]}, "sources": sources, "fields": fields,
            "views": store.get_views(conn, combo_id=combo["id"]), "records": store.combo_records(conn, combo, eng),
            "refs": store.combo_refs(conn, combo, eng)}


def _sources_json(conn, sources: list[ComboSource], user):
    for s in sources:
        store.get_table(conn, s.table_id, user["id"])
    return [{"table_id": s.table_id, "color": s.color or PALETTE[i % len(PALETTE)]} for i, s in enumerate(sources)]


@app.post("/api/combos")
def create_combo(body: ComboIn, user=User):
    if len(body.sources) < 1:
        raise HTTPException(400, "Pick at least one source table")
    with connection() as conn:
        base = store.get_base(conn, body.base_id, user["id"]) if body.base_id else store.ensure_base(conn, user["id"])
        sources = _sources_json(conn, body.sources, user)
        fields = store.build_combo_fields(conn, [s["table_id"] for s in sources])
        c = conn.execute(
            "INSERT INTO combos (user_id, base_id, name, sources, fields) VALUES (%s,%s,%s,%s,%s) RETURNING *",
            (user["id"], base["id"], body.name, Jsonb(sources), Jsonb(fields)),
        ).fetchone()
        store.create_view(conn, combo_id=c["id"], name="Table", type="grid")
        sem = {s: next((f["id"] for f in fields if f["name"].lower() in names), None)
               for s, names in {"start": ("start",), "end": ("due / end", "end / due", "end", "due")}.items()}
        if sem["start"] or sem["end"]:
            store.create_view(conn, combo_id=c["id"], name="Calendar", type="calendar",
                              config={"dateField": sem["start"] or sem["end"], "endField": sem["end"] if sem["start"] else None,
                                      "colorField": "__source"})
        return {"id": c["id"], "name": c["name"]}


@app.get("/api/combos/{combo_id}")
def get_combo(combo_id: str, user=User):
    with connection() as conn:
        return FastJSON(_combo_payload(conn, store.get_combo(conn, combo_id, user["id"])))


@app.patch("/api/combos/{combo_id}")
def update_combo(combo_id: str, body: ComboPatch, user=User):
    with connection() as conn:
        c = store.get_combo(conn, combo_id, user["id"])
        if body.base_id:
            store.get_base(conn, body.base_id, user["id"])
            conn.execute("UPDATE combos SET base_id = %s WHERE id = %s", (body.base_id, combo_id))
        name = body.name or c["name"]
        sources = _sources_json(conn, body.sources, user) if body.sources is not None else c["sources"]
        if body.remap:
            fields = store.build_combo_fields(conn, [s["table_id"] for s in sources])
        elif body.fields is not None:
            fields = [f.model_dump() for f in body.fields]
        else:
            fields = c["fields"]
        c = conn.execute(
            "UPDATE combos SET name = %s, sources = %s, fields = %s WHERE id = %s RETURNING *",
            (name, Jsonb(sources), Jsonb(fields), combo_id),
        ).fetchone()
        return _combo_payload(conn, c)


@app.delete("/api/combos/{combo_id}")
def delete_combo(combo_id: str, user=User):
    with connection() as conn:
        store.get_combo(conn, combo_id, user["id"])
        conn.execute("DELETE FROM combos WHERE id = %s", (combo_id,))
    return {"ok": True}


@app.post("/api/combos/{combo_id}/records")
def create_combo_record(combo_id: str, body: ComboRecordIn, user=User):
    with connection() as conn:
        c = store.get_combo(conn, combo_id, user["id"])
        if body.table_id not in {s["table_id"] for s in c["sources"]}:
            raise HTTPException(400, "Table is not a source of this combined database")
        data = store.unified_to_source(conn, c, body.table_id, body.data)
        rid, table = recordops.create(conn, body.table_id, data, user["id"], link_table_id=body.link_table_id)
        out = _combo_record_out(conn, c, body.table_id, rid)
    _after_write(table)
    return out


def _combo_record_out(conn, combo, table_id, record_id):
    sources, fields = store.resolve_combo(conn, combo)
    src = next((x for x in sources if x["table_id"] == str(table_id)), None)
    return store._project(fields, src["name"] if src else None, str(table_id), _record_out(conn, table_id, record_id))


@app.patch("/api/combos/{combo_id}/records/{record_id}")
def update_combo_record(combo_id: str, record_id: str, body: RecordIn, user=User):
    with connection() as conn:
        c = store.get_combo(conn, combo_id, user["id"])
        if body.table_id:
            tid = body.table_id
        else:
            rec = conn.execute("SELECT table_id FROM records WHERE id = %s", (record_id,)).fetchone() or store.not_found()
            tid = str(rec["table_id"])
        if tid not in {s["table_id"] for s in c["sources"]}:
            store.not_found()
        patch = store.unified_to_source(conn, c, tid, body.data)
        table = recordops.update(conn, tid, record_id, patch, user["id"])
        out = _combo_record_out(conn, c, tid, record_id)
    _after_write(table)
    return out


app.include_router(shares.router)
app.include_router(oauth.router)
app.include_router(mcp_server.router)
app.mount("/api/v1", rest_v1.v1)


# ======================================================================== frontend + CalDAV pass-through
#
# The web app only uses "/", "/assets/*" and "/api/*" (navigation is in the #fragment). Everything
# else, and every non-GET method such as PROPFIND / REPORT, goes to the bundled Radicale, so a
# phone can be pointed at the same address as the web app.

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@app.api_route("/{path:path}", methods=builtin_dav.DAV_METHODS, include_in_schema=False)
async def frontend_or_dav(path: str, request: Request):
    if path.startswith("api/"):
        raise HTTPException(404)
    if request.method in ("GET", "HEAD") and STATIC_DIR.exists():
        f = (STATIC_DIR / path).resolve()
        if path in ("", "index.html"):
            return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
        if f.is_file() and f.is_relative_to(STATIC_DIR):  # sw.js and other files from frontend/public
            return FileResponse(f, headers={"Cache-Control": "no-cache"} if path == "sw.js" else None)
    if not builtin_dav.enabled():
        if not STATIC_DIR.exists() or request.method not in ("GET", "HEAD"):
            raise HTTPException(404)
        return FileResponse(STATIC_DIR / "index.html")
    body = await request.body()
    status, headers, data = await run_in_threadpool(
        builtin_dav.forward, request.method, path, request.url.query, dict(request.headers), body,
        request.client.host if request.client else "", request.url.scheme,
    )
    resp = RawResponse(content=data, status_code=status)
    for k, v in headers:
        resp.headers.append(k, v)
    return resp
