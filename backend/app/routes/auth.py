"""Sign-in, the built-in DAV account, and API keys."""
from datetime import datetime
from typing import Literal

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from .. import auth, builtin_dav, store
from ..db import Jsonb, connection
from .common import Credentials, PasswordChange, User

router = APIRouter()


# ======================================================================== auth

@router.get("/api/auth/config")
def auth_config():
    with connection() as conn:
        has_users = conn.execute("SELECT EXISTS (SELECT 1 FROM users) AS e").fetchone()["e"]
    return {"allow_signup": auth.ALLOW_SIGNUP or not has_users, "has_users": has_users}


@router.post("/api/auth/register")
def register(body: Credentials, request: Request, response: Response):
    username = auth.validate_credentials(body.username, body.password)
    with connection() as conn:
        first = not conn.execute("SELECT EXISTS (SELECT 1 FROM users) AS e").fetchone()["e"]
        if not first and not auth.ALLOW_SIGNUP:
            raise HTTPException(403, "Registration is disabled")
        if conn.execute("SELECT 1 FROM users WHERE username = %s", (username,)).fetchone():
            raise HTTPException(409, "That username is taken")
        try:
            user = conn.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (%s,%s,%s) RETURNING *",
                (username, auth.hash_password(body.password), first),
            ).fetchone()
        except psycopg.errors.UniqueViolation:  # registered by a simultaneous request
            raise HTTPException(409, "That username is taken")
        if first:
            # Data created before multi-user support belongs to the first account.
            for t in ("caldav_accounts", "tables", "combos"):
                conn.execute(f"UPDATE {t} SET user_id = %s WHERE user_id IS NULL", (user["id"],))
        if builtin_dav.enabled():
            builtin_dav.provision(conn, user["id"])
        auth.start_session(conn, user["id"], request, response)
    return auth.public_user(user)


@router.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response):
    with connection() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = %s", (body.username.strip().lower(),)).fetchone()
        if not user or not auth.verify_password(body.password, user["password_hash"]):
            raise HTTPException(401, "Wrong username or password")
        auth.start_session(conn, user["id"], request, response)
    return auth.public_user(user)


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    with connection() as conn:
        auth.end_session(conn, request, response)
    return {"ok": True}


@router.get("/api/auth/me")
def me(user=User):
    return auth.public_user(user)


@router.post("/api/auth/password")
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

@router.get("/api/dav")
def dav_info(user=User):
    with connection() as conn:
        return builtin_dav.info(conn, user["id"])


@router.post("/api/dav/check")
def dav_check(user=User):
    """Test the public CalDAV address the way a phone would."""
    with connection() as conn:
        acc = conn.execute("SELECT username, password FROM caldav_accounts WHERE user_id = %s AND is_builtin", (user["id"],)).fetchone()
    if not acc:
        raise HTTPException(400, "Enable your CalDAV login first")
    return builtin_dav.check_public(acc["username"], acc["password"])


@router.post("/api/dav/reset")
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


@router.get("/api/keys")
def list_keys(user=User):
    with connection() as conn:
        return [_key_public(k) for k in conn.execute("SELECT * FROM api_keys WHERE user_id = %s ORDER BY created_at DESC", (user["id"],)).fetchall()]


@router.post("/api/keys")
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


@router.patch("/api/keys/{key_id}")
def update_key(key_id: str, body: KeyIn, user=User):
    with connection() as conn:
        for b in body.base_ids or []:
            store.get_base(conn, b, user["id"])
        k = conn.execute("UPDATE api_keys SET name = %s, scope = %s, base_ids = %s, expires_at = %s WHERE id = %s AND user_id = %s RETURNING *",
                         (body.name.strip() or "API key", body.scope, Jsonb(body.base_ids) if body.base_ids is not None else None,
                          body.expires_at, key_id, user["id"])).fetchone() or store.not_found("API key not found")
    return _key_public(k)


@router.delete("/api/keys/{key_id}")
def delete_key(key_id: str, user=User):
    with connection() as conn:
        conn.execute("DELETE FROM api_keys WHERE id = %s AND user_id = %s", (key_id, user["id"]))
    return {"ok": True}
