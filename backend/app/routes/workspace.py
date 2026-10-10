"""The sidebar: bases and sync accounts."""

from fastapi import APIRouter, HTTPException

from .. import store, sync
from ..db import connection
from ..ical import PALETTE
from .common import AccountIn, AccountTest, BaseIn, BasePatch, User, _account_public, _own_account, _table_out

router = APIRouter()


# ======================================================================== sidebar

@router.get("/api/workspace")
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

@router.get("/api/bases")
def list_bases(user=User):
    return workspace(user)["bases"]


@router.post("/api/bases")
def create_base(body: BaseIn, user=User):
    if store.api_bases.get() is not None:
        raise HTTPException(403, "This API key is limited to specific bases and can't create new ones")
    with connection() as conn:
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM bases WHERE user_id = %s", (user["id"],)).fetchone()["p"]
        return conn.execute("INSERT INTO bases (user_id, name, color, position) VALUES (%s,%s,%s,%s) RETURNING id, name, color, position",
                            (user["id"], body.name.strip() or "Untitled base", body.color or PALETTE[pos % len(PALETTE)], pos)).fetchone()


@router.patch("/api/bases/{base_id}")
def update_base(base_id: str, body: BasePatch, user=User):
    with connection() as conn:
        b = store.get_base(conn, base_id, user["id"])
        return conn.execute("UPDATE bases SET name = %s, color = %s, position = %s WHERE id = %s RETURNING id, name, color, position",
                            ((body.name or b["name"]).strip(), body.color or b["color"],
                             b["position"] if body.position is None else body.position, base_id)).fetchone()


@router.get("/api/bases/{base_id}/views")
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


@router.delete("/api/bases/{base_id}")
def delete_base(base_id: str, user=User):
    """Deletes the base with its tables and combined databases (CalDAV calendars themselves are untouched)."""
    with connection() as conn:
        store.get_base(conn, base_id, user["id"])
        if conn.execute("SELECT count(*) AS n FROM bases WHERE user_id = %s", (user["id"],)).fetchone()["n"] <= 1:
            raise HTTPException(400, "You need at least one base")
        conn.execute("DELETE FROM bases WHERE id = %s", (base_id,))
    return {"ok": True}


# ======================================================================== caldav accounts

@router.post("/api/accounts")
def create_account(body: AccountIn, user=User):
    with connection() as conn:
        a = conn.execute(
            "INSERT INTO caldav_accounts (user_id, name, url, carddav_url, username, password) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
            (user["id"], body.name, body.url, (body.carddav_url or "").strip() or None, body.username, body.password),
        ).fetchone()
    return _account_public(a)


@router.patch("/api/accounts/{account_id}")
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


@router.delete("/api/accounts/{account_id}")
def delete_account(account_id: str, user=User):
    with connection() as conn:
        if _own_account(conn, account_id, user)["is_builtin"]:
            raise HTTPException(400, "The built-in CalDAV account can't be removed")
        conn.execute("DELETE FROM caldav_accounts WHERE id = %s", (account_id,))
    return {"ok": True}


@router.post("/api/accounts/test")
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


@router.get("/api/accounts/{account_id}/calendars")
def account_calendars(account_id: str, user=User):
    with connection() as conn:
        a = _own_account(conn, account_id, user)
    try:
        return sync.list_calendars(a)
    except Exception as e:
        raise HTTPException(400, f"Could not list calendars: {e}")


@router.get("/api/accounts/{account_id}/addressbooks")
def account_addressbooks(account_id: str, user=User):
    with connection() as conn:
        a = _own_account(conn, account_id, user)
    try:
        return sync.list_addressbooks(a)
    except Exception as e:
        raise HTTPException(400, f"Could not list address books: {e}")
