"""The sidebar: bases and sync accounts."""
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core import webdav as dav
from app.core.context import api_bases
from app.core.db import connection
from app.core.secretbox import seal, unseal
from app.data import access, store
from app.dav import devices, sync
from app.formats.ical import PALETTE
from app.web.common import _account_public, _own_account, _table_out, AccountIn, AccountTest, BaseIn, BasePatch, User

router = APIRouter()


# ======================================================================== sidebar

@router.get("/api/workspace")
def workspace(user=User):
    with connection() as conn:
        store.ensure_base(conn, user["id"])
        if not user.get("api_key"):
            devices.ensure(conn, user["id"])  # the built-in account: phones' login for Probyte's own calendar server
        bases, hidden = [], {}
        for i, b in enumerate(access.bases(conn, user["id"])):
            if not store.base_allowed(b["id"]):
                continue
            bases.append({"id": b["id"], "name": b["name"], "color": b["color"], "position": i, "role": b["role"],
                          "follow_views": b["follow_views"], "owner": b["owner_name"]})
            if b["role"] != "owner":
                row = conn.execute("SELECT hidden FROM base_members WHERE base_id = %s AND user_id = %s", (b["id"], user["id"])).fetchone()
                hidden[str(b["id"])] = row["hidden"] if row else {}
        ids = [str(b["id"]) for b in bases]

        def own_sidebar(x):  # a member's own hide / show wins over the owner's
            h = hidden.get(str(x["base_id"]), {})
            return {**x, "hidden": h[str(x["id"])]} if str(x["id"]) in h else x
        tables = [_table_out(own_sidebar(t)) for t in conn.execute(
            "SELECT id, base_id, name, kind, account_id, calendar_url, last_synced_at, last_sync_error, remote, hidden, icon, dav_slug "
            "FROM tables WHERE base_id = ANY(%s::uuid[]) AND trashed_at IS NULL ORDER BY created_at", (ids,)).fetchall()]
        combos = [own_sidebar(c) for c in conn.execute(
            "SELECT id, base_id, name, sources, hidden, icon FROM combos WHERE base_id = ANY(%s::uuid[]) ORDER BY created_at", (ids,)).fetchall()]
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
    if api_bases.get() is not None:
        raise HTTPException(403, "This API key is limited to specific bases and can't create new ones")
    with connection() as conn:
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM bases WHERE user_id = %s", (user["id"],)).fetchone()["p"]
        return conn.execute("INSERT INTO bases (user_id, name, color, position) VALUES (%s,%s,%s,%s) RETURNING id, name, color, position",
                            (user["id"], body.name.strip() or "Untitled base", body.color or PALETTE[pos % len(PALETTE)], pos)).fetchone()


@router.patch("/api/bases/{base_id}")
def update_base(base_id: str, body: BasePatch, user=User):
    with connection() as conn:
        b = store.get_base(conn, base_id, user["id"], need="viewer")
        if body.position is not None:  # move to that place in this user's list; the others keep their order
            order = [str(x["id"]) for x in access.bases(conn, user["id"]) if str(x["id"]) != str(base_id)]
            order.insert(max(0, min(body.position, len(order))), str(base_id))
            for i, bid in enumerate(order):
                conn.execute("UPDATE bases SET position = %s WHERE id = %s AND user_id = %s", (i, bid, user["id"]))
                conn.execute("UPDATE base_members SET position = %s WHERE base_id = %s AND user_id = %s", (i, bid, user["id"]))
        if body.name is not None or body.color is not None:
            access.check(conn, base_id, user["id"], "admin", "Base")
            conn.execute("UPDATE bases SET name = %s, color = %s WHERE id = %s",
                         ((body.name or b["name"]).strip(), body.color or b["color"], base_id))
        return conn.execute("SELECT id, name, color, position FROM bases WHERE id = %s", (base_id,)).fetchone()


@router.get("/api/bases/{base_id}/views")
def base_views(base_id: str, user=User):
    """Tables and combined databases of a base with their views (for the public-link editor)."""
    with connection() as conn:
        store.get_base(conn, base_id, user["id"])
        out = []
        for kind, tbl, col in (("table", "tables", "table_id"), ("combo", "combos", "combo_id")):
            for t in conn.execute(f"SELECT id, name FROM {tbl} WHERE base_id = %s ORDER BY created_at", (base_id,)).fetchall():
                views = conn.execute(f"SELECT id, name, type FROM views WHERE {col} = %s AND user_id IS NULL ORDER BY position",
                                     (t["id"],)).fetchall()
                out.append({"kind": kind, "id": t["id"], "name": t["name"], "views": views})
        return out


@router.delete("/api/bases/{base_id}")
def delete_base(base_id: str, user=User):
    """Deletes the base with its tables and combined databases (CalDAV calendars themselves are untouched). Only its
    owner can; someone it's shared with leaves it instead (DELETE /api/bases/{id}/members/{their id})."""
    with connection() as conn:
        store.get_base(conn, base_id, user["id"], need="owner")
        if conn.execute("SELECT count(*) AS n FROM bases WHERE user_id = %s", (user["id"],)).fetchone()["n"] <= 1:
            raise HTTPException(400, "You need at least one base")
        conn.execute("DELETE FROM bases WHERE id = %s", (base_id,))
    return {"ok": True}


# ======================================================================== sharing a base with other users

class MemberIn(BaseModel):
    username: str
    role: str = "editor"


class MemberPatch(BaseModel):
    role: str


class MyMembership(BaseModel):
    follow_views: bool


def _role(r: str) -> str:
    if r not in access.ROLES:
        raise HTTPException(400, "The role is viewer, editor or admin")
    return r


@router.get("/api/bases/{base_id}/members")
def list_members(base_id: str, user=User):
    """Who the base is shared with (its owner first)."""
    with connection() as conn:
        b = store.get_base(conn, base_id, user["id"])
        owner = conn.execute("SELECT id, username FROM users WHERE id = %s", (b["user_id"],)).fetchone()
        members = conn.execute("SELECT u.id, u.username, m.role, m.follow_views, m.created_at FROM base_members m "
                               "JOIN users u ON u.id = m.user_id WHERE m.base_id = %s ORDER BY u.username", (base_id,)).fetchall()
    return {"owner": {"id": owner["id"], "username": owner["username"], "role": "owner"}, "members": members, "you": b["role"]}


@router.post("/api/bases/{base_id}/members")
def add_member(base_id: str, body: MemberIn, user=User):
    """Share the base with another user of this server (by username)."""
    with connection() as conn:
        b = store.get_base(conn, base_id, user["id"], need="admin")
        other = conn.execute("SELECT id, username FROM users WHERE username = %s", (body.username.strip().lower(),)).fetchone()
        if not other:
            raise HTTPException(404, f"There's no user called “{body.username.strip()}” on this server")
        if str(other["id"]) == str(b["user_id"]):
            raise HTTPException(400, "That's the base's owner")
        conn.execute("INSERT INTO base_members (base_id, user_id, role, added_by) VALUES (%s,%s,%s,%s) "
                     "ON CONFLICT (base_id, user_id) DO UPDATE SET role = EXCLUDED.role", (base_id, other["id"], _role(body.role), user["id"]))
    return list_members(base_id, user)


@router.patch("/api/bases/{base_id}/members/{member_id}")
def change_member(base_id: str, member_id: str, body: MemberPatch, user=User):
    with connection() as conn:
        store.get_base(conn, base_id, user["id"], need="admin")
        conn.execute("UPDATE base_members SET role = %s WHERE base_id = %s AND user_id = %s", (_role(body.role), base_id, member_id))
    return list_members(base_id, user)


@router.delete("/api/bases/{base_id}/members/{member_id}")
def remove_member(base_id: str, member_id: str, user=User):
    """Stop sharing the base with someone (an admin), or leave it (yourself)."""
    with connection() as conn:
        store.get_base(conn, base_id, user["id"], need="viewer" if str(member_id) == str(user["id"]) else "admin")
        conn.execute("DELETE FROM view_overrides o USING views v LEFT JOIN tables t ON t.id = v.table_id LEFT JOIN combos c ON "
                     "c.id = v.combo_id WHERE o.view_id = v.id AND o.user_id = %s AND COALESCE(t.base_id, c.base_id) = %s", (member_id, base_id))
        conn.execute("DELETE FROM views v USING tables t WHERE v.table_id = t.id AND v.user_id = %s AND t.base_id = %s", (member_id, base_id))
        conn.execute("DELETE FROM views v USING combos c WHERE v.combo_id = c.id AND v.user_id = %s AND c.base_id = %s", (member_id, base_id))
        conn.execute("DELETE FROM base_members WHERE base_id = %s AND user_id = %s", (base_id, member_id))
    return {"ok": True}


@router.patch("/api/bases/{base_id}/me")
def my_membership(base_id: str, body: MyMembership, user=User):
    """For someone the base is shared with: whether their views stay those of the base (follow_views), or become their
    own copies, which later changes to the base's views don't touch."""
    with connection() as conn:
        b = store.get_base(conn, base_id, user["id"], need="viewer")
        if b["role"] == "owner":
            raise HTTPException(400, "The base's views are yours")
        views_of_base = ("SELECT v.id, v.config, v.updated_at FROM views v LEFT JOIN tables t ON t.id = v.table_id LEFT JOIN combos c "
                         "ON c.id = v.combo_id WHERE v.user_id IS NULL AND COALESCE(t.base_id, c.base_id) = %s")
        if body.follow_views:  # back to the base's views (their own extra views stay)
            conn.execute(f"DELETE FROM view_overrides WHERE user_id = %s AND view_id IN (SELECT id FROM ({views_of_base}) x)", (user["id"], base_id))
        else:  # copies of the views as they see them now
            conn.execute(f"DELETE FROM view_overrides o USING ({views_of_base}) v WHERE o.view_id = v.id AND o.user_id = %s "
                         "AND o.updated_at <= v.updated_at", (base_id, user["id"]))
            conn.execute(f"INSERT INTO view_overrides (view_id, user_id, config) SELECT id, %s, config FROM ({views_of_base}) v "
                         "ON CONFLICT DO NOTHING", (user["id"], base_id))
        conn.execute("UPDATE base_members SET follow_views = %s WHERE base_id = %s AND user_id = %s", (body.follow_views, base_id, user["id"]))
    return {"ok": True, "follow_views": body.follow_views}


# ======================================================================== caldav accounts

def _checked(body: AccountIn):
    try:
        body.url = dav.check_url(body.url)
        if (body.carddav_url or "").strip():
            body.carddav_url = dav.check_url(body.carddav_url)
    except dav.DavError as e:
        raise HTTPException(400, str(e))
    return body


@router.post("/api/accounts")
def create_account(body: AccountIn, user=User):
    body = _checked(body)
    with connection() as conn:
        a = conn.execute(
            "INSERT INTO caldav_accounts (user_id, name, url, carddav_url, username, password) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
            (user["id"], body.name, body.url, (body.carddav_url or "").strip() or None, body.username, seal(body.password)),
        ).fetchone()
    return _account_public(a)


@router.patch("/api/accounts/{account_id}")
def update_account(account_id: str, body: AccountIn, user=User):
    with connection() as conn:
        a = _own_account(conn, account_id, user)
        if a["is_builtin"]:
            conn.execute("UPDATE caldav_accounts SET name = %s WHERE id = %s", (body.name, account_id))
        else:
            body = _checked(body)
            conn.execute(
                "UPDATE caldav_accounts SET name=%s, url=%s, carddav_url=%s, username=%s, password=COALESCE(%s, password) WHERE id=%s",
                (body.name, body.url, (body.carddav_url or "").strip() or None, body.username, seal(body.password) or None, account_id),
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
    account = _checked(body).model_dump()
    if body.id and not body.password:
        with connection() as conn:
            account["password"] = unseal(_own_account(conn, body.id, user)["password"])
    errors = []

    def attempt(what, fn):
        try:
            return fn(account)
        except Exception as e:
            errors.append(f"{what}: {e}")
            return None
    with ThreadPoolExecutor(2) as pool:  # both at once: an unreachable server costs one timeout, not two
        cals_f = pool.submit(attempt, "calendars", sync.list_calendars)
        books_f = pool.submit(attempt, "contacts", sync.list_addressbooks)
        cals, books = cals_f.result(), books_f.result()
    errors.sort()
    if cals is None and books is None:
        raise HTTPException(400, "Connection failed — " + "; ".join(errors))
    return {"ok": True, "calendars": cals or [], "addressbooks": books, "calendar_error": None if cals is not None else errors[0]}


@router.get("/api/accounts/{account_id}/calendars")
def account_calendars(account_id: str, user=User):
    with connection() as conn:
        a = _own_account(conn, account_id, user)
    if a["is_builtin"]:
        return []  # on Probyte's own server each table is its own calendar
    try:
        return sync.list_calendars(a)
    except Exception as e:
        raise HTTPException(400, f"Could not list calendars: {e}")


@router.get("/api/accounts/{account_id}/addressbooks")
def account_addressbooks(account_id: str, user=User):
    with connection() as conn:
        a = _own_account(conn, account_id, user)
    if a["is_builtin"]:
        return []
    try:
        return sync.list_addressbooks(a)
    except Exception as e:
        raise HTTPException(400, f"Could not list address books: {e}")


# ======================================================================== sharing a base with a link

class InviteIn(BaseModel):
    role: str = "editor"
    follow_views: bool = True
    expires_days: int | None = None   # none: until it's turned off


class InvitePatch(BaseModel):
    role: str | None = None
    follow_views: bool | None = None
    enabled: bool | None = None


def _invite_out(i) -> dict:
    return {k: i[k] for k in ("id", "token", "role", "follow_views", "enabled", "expires_at", "uses", "created_at")}


def _invite_role(b, role: str) -> str:
    role = _role(role)
    if role == "admin" and b["role"] != "owner":
        raise HTTPException(403, "Only the base's owner can make a link that joins people as admins")
    return role


@router.get("/api/bases/{base_id}/invites")
def list_invites(base_id: str, user=User):
    """The base's invite links (its owner and admins)."""
    with connection() as conn:
        store.get_base(conn, base_id, user["id"], need="admin")
        return [_invite_out(i) for i in conn.execute("SELECT * FROM base_invites WHERE base_id = %s ORDER BY created_at", (base_id,)).fetchall()]


@router.post("/api/bases/{base_id}/invites")
def create_invite(base_id: str, body: InviteIn, user=User):
    """A link that adds whoever opens it (signed in on this server) to the base."""
    import secrets
    with connection() as conn:
        b = store.get_base(conn, base_id, user["id"], need="admin")
        if body.expires_days is not None and not 1 <= body.expires_days <= 365:
            raise HTTPException(400, "A link lasts 1 to 365 days, or until it's turned off")
        i = conn.execute(
            "INSERT INTO base_invites (base_id, token, role, follow_views, expires_at, created_by) "
            "VALUES (%s, %s, %s, %s, CASE WHEN %s::int IS NULL THEN NULL ELSE now() + make_interval(days => %s::int) END, %s) RETURNING *",
            (base_id, secrets.token_urlsafe(16), _invite_role(b, body.role), body.follow_views, body.expires_days, body.expires_days, user["id"])).fetchone()
    return _invite_out(i)


@router.patch("/api/bases/{base_id}/invites/{invite_id}")
def change_invite(base_id: str, invite_id: str, body: InvitePatch, user=User):
    with connection() as conn:
        b = store.get_base(conn, base_id, user["id"], need="admin")
        i = conn.execute("SELECT * FROM base_invites WHERE id = %s AND base_id = %s", (invite_id, base_id)).fetchone() or store.not_found("Link not found")
        i = conn.execute("UPDATE base_invites SET role = %s, follow_views = %s, enabled = %s WHERE id = %s RETURNING *",
                         (_invite_role(b, body.role) if body.role else i["role"],
                          i["follow_views"] if body.follow_views is None else body.follow_views,
                          i["enabled"] if body.enabled is None else body.enabled, invite_id)).fetchone()
    return _invite_out(i)


@router.delete("/api/bases/{base_id}/invites/{invite_id}")
def delete_invite(base_id: str, invite_id: str, user=User):
    """The link stops working (people who joined with it stay members)."""
    with connection() as conn:
        store.get_base(conn, base_id, user["id"], need="admin")
        conn.execute("DELETE FROM base_invites WHERE id = %s AND base_id = %s", (invite_id, base_id))
    return {"ok": True}


def _usable_invite(conn, token: str):
    i = conn.execute("SELECT i.*, b.name AS base_name, b.user_id AS owner_id, u.username AS owner FROM base_invites i "
                     "JOIN bases b ON b.id = i.base_id JOIN users u ON u.id = b.user_id WHERE i.token = %s", (token,)).fetchone()
    if not i or not i["enabled"] or (i["expires_at"] and conn.execute("SELECT now() > %s AS x", (i["expires_at"],)).fetchone()["x"]):
        raise HTTPException(404, "This invite link doesn't work any more: ask for a new one")
    return i


@router.get("/api/join/{token}")
def invite_info(token: str, user=User):
    """What an invite link joins (for the page that asks before joining)."""
    with connection() as conn:
        i = _usable_invite(conn, token)
        mine = str(i["owner_id"]) == str(user["id"])
        member = conn.execute("SELECT role FROM base_members WHERE base_id = %s AND user_id = %s", (i["base_id"], user["id"])).fetchone()
    return {"base_id": i["base_id"], "base": i["base_name"], "owner": i["owner"], "role": i["role"], "follow_views": i["follow_views"],
            "already": "owner" if mine else (member["role"] if member else None)}


@router.post("/api/join/{token}")
def join_with_invite(token: str, user=User):
    """Join the base with the link's role (someone who's already in it keeps their role, unless the link's is higher)."""
    if user.get("api_key"):
        raise HTTPException(403, "Join a base in the app, not with an API key")
    with connection() as conn:
        i = _usable_invite(conn, token)
        if str(i["owner_id"]) == str(user["id"]):
            return {"base_id": i["base_id"], "role": "owner"}
        rank = {"viewer": 1, "editor": 2, "admin": 3}
        have = conn.execute("SELECT role FROM base_members WHERE base_id = %s AND user_id = %s", (i["base_id"], user["id"])).fetchone()
        if have and rank[have["role"]] >= rank[i["role"]]:
            return {"base_id": i["base_id"], "role": have["role"]}
        conn.execute("INSERT INTO base_members (base_id, user_id, role, follow_views, added_by) VALUES (%s,%s,%s,%s,%s) "
                     "ON CONFLICT (base_id, user_id) DO UPDATE SET role = EXCLUDED.role",
                     (i["base_id"], user["id"], i["role"], i["follow_views"], i["created_by"]))
        conn.execute("UPDATE base_invites SET uses = uses + 1 WHERE id = %s", (i["id"],))
    if not i["follow_views"] and not have:   # their own copies of the views, as they are now
        my_membership(str(i["base_id"]), MyMembership(follow_views=False), user)
    return {"base_id": i["base_id"], "role": i["role"]}
