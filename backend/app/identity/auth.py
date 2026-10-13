"""Username/password accounts with cookie sessions."""
import base64
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool

from app.core.context import api_bases, set_actor, writing
from app.core.db import connection

SESSION_COOKIE = "probyte_session"
SESSION_DAYS = int(os.environ.get("SESSION_DAYS", "30"))
ALLOW_SIGNUP = os.environ.get("ALLOW_SIGNUP", "true").lower() in ("1", "true", "yes")
# "auto": Secure cookie when the request arrived over HTTPS (X-Forwarded-Proto from the reverse proxy).
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "auto").lower()

# Usernames double as Radicale logins and collection paths, so keep them path-safe.
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
MIN_PASSWORD = 8


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${_b64(salt)}${_b64(h)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt, h = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


def validate_credentials(username: str, password: str) -> str:
    username = username.strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(400, "Username must be 3–32 characters: lowercase letters, digits, '.', '_' or '-'")
    if len(password) < MIN_PASSWORD:
        raise HTTPException(400, f"Password must be at least {MIN_PASSWORD} characters")
    return username


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(conn, user_id, request: Request, response: Response):
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    conn.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s,%s,%s)", (_token_hash(token), user_id, expires))
    conn.execute("DELETE FROM sessions WHERE expires_at < now()")
    secure = request.url.scheme == "https" if COOKIE_SECURE == "auto" else COOKIE_SECURE in ("1", "true", "yes")
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=secure, path="/")


def end_session(conn, request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_token_hash(token),))
    response.delete_cookie(SESSION_COOKIE, path="/")


# ---------------------------------------------------------------- API keys

API_KEY_PREFIX = "pbk_"
# API keys work for data (bases, tables, records, views…), never for account management.
KEY_BLOCKED_PATHS = ("/api/auth", "/api/keys", "/api/shares", "/api/dav", "/api/accounts", "/api/attachments")
# Bases an API key is limited to, for the current request (None = no limit / logged-in session).


def new_api_key() -> tuple[str, str]:
    key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return key, _token_hash(key)


def _key_user(key: str, method: str, path: str) -> tuple[dict, set | None]:
    with connection() as conn:
        row = conn.execute(
            "SELECT k.id AS key_id, k.name AS key_name, k.source AS key_source, k.scope, k.base_ids, k.last_used_at, "
            "u.id, u.username, u.is_admin "
            "FROM api_keys k JOIN users u ON u.id = k.user_id "
            "WHERE k.key_hash = %s AND (k.expires_at IS NULL OR k.expires_at > now())", (_token_hash(key),)
        ).fetchone()
        if not row:
            raise HTTPException(401, "Invalid or expired API key")
        if path.startswith(KEY_BLOCKED_PATHS):
            raise HTTPException(403, "API keys can't be used for account settings, keys or public links")
        if row["scope"] == "read" and method not in ("GET", "HEAD"):
            raise HTTPException(403, "This API key is read-only")
        if not row["last_used_at"] or (datetime.now(timezone.utc) - row["last_used_at"]).total_seconds() > 60:
            conn.execute("UPDATE api_keys SET last_used_at = now() WHERE id = %s", (row["key_id"],))
    user = {k: row[k] for k in ("id", "username", "is_admin")} | {"api_key": True, "api_scope": row["scope"],
                                                                                   "api_key_id": str(row["key_id"])}
    # for record history: an app connected with OAuth goes by its name, other keys by theirs
    user["actor"] = row["key_name"] if row["key_source"] == "oauth" else f"{row['username']} via API key “{row['key_name']}”"
    return user, set(row["base_ids"]) if row["base_ids"] is not None else None


def key_user(key: str, path: str = "/api/v1") -> tuple[dict, set | None]:
    """Check an API key without applying the read-only rule (callers check `api_scope` per action)."""
    return _key_user(key, "GET", path)


def request_key(request: Request) -> str | None:
    """An API key from `Authorization: Bearer …` or `X-API-Key: …`."""
    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        return authz[7:].strip()
    return (request.headers.get("x-api-key") or "").strip() or None


def _session_user(token: str):
    with connection() as conn:
        return conn.execute(
            "SELECT u.id, u.username, u.is_admin FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = %s AND s.expires_at > now()",
            (_token_hash(token),),
        ).fetchone()


async def current_user(request: Request) -> dict:
    """FastAPI dependency: the logged-in user (session cookie or API key), or 401.

    Async so the API-key base limit, kept in a context variable, carries over into the endpoint."""
    writing.set(request.method not in ("GET", "HEAD", "OPTIONS"))
    key = request_key(request)
    if key:
        user, bases = await run_in_threadpool(_key_user, key, request.method, request.url.path)
        api_bases.set(bases)
        set_actor(user["actor"], user["id"])
        return user
    api_bases.set(None)
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        user = await run_in_threadpool(_session_user, token)
        if user:
            set_actor(user["username"], user["id"])
            return user
    raise HTTPException(401, "Not logged in")


def public_user(u: dict) -> dict:
    return {"id": u["id"], "username": u["username"], "is_admin": u["is_admin"]}


User = Depends(current_user)  # in a route's parameters: the signed-in user (session cookie or API key)
