"""Who may do what in a base: its owner, and the users it's shared with (base_members) as viewer, editor or admin.

    viewer  reads everything in the base
    editor  also adds, changes and deletes records, and changes the base's views (or their own copies)
    admin   also changes fields and tables, and who the base is shared with
    owner   also deletes the base

Every lookup of a base, table or combined database for a user goes through here (schema.get_table and friends), so
a shared base works everywhere a user's own does: the app, REST, MCP, files, phones. What a request needs is said by
the caller, or follows from its HTTP method (core.context.writing): a viewer's GET is fine, their POST isn't.
"""
from fastapi import HTTPException

from app.core.context import writing

RANK = {"viewer": 1, "editor": 2, "admin": 3, "owner": 4}
ROLES = ("viewer", "editor", "admin")


def membership(conn, base_id, user_id) -> dict | None:
    """{"role", "follow_views", "owner"} of a user in a base, or None."""
    row = conn.execute("SELECT b.user_id AS owner, m.role, m.follow_views FROM bases b "
                       "LEFT JOIN base_members m ON m.base_id = b.id AND m.user_id = %s WHERE b.id = %s", (user_id, base_id)).fetchone()
    if not row:
        return None
    if str(row["owner"]) == str(user_id):
        return {"role": "owner", "follow_views": True, "owner": row["owner"]}
    if row["role"]:
        return {"role": row["role"], "follow_views": row["follow_views"], "owner": row["owner"]}
    return None


def role(conn, base_id, user_id) -> str | None:
    m = membership(conn, base_id, user_id)
    return m["role"] if m else None


def need_for_request(need: str | None) -> str:
    return need or ("editor" if writing.get() else "viewer")


def check(conn, base_id, user_id, need: str | None = None, what: str = "Table") -> str:
    """The user's role in the base, if it's at least `need` (default: from the request's method); else 404 (no access
    at all) or 403."""
    r = role(conn, base_id, user_id)
    if r is None:
        raise HTTPException(404, f"{what} not found")
    n = need_for_request(need)
    if RANK[r] < RANK[n]:
        raise HTTPException(403, "You can see this base but not change it" if r == "viewer" else
                            "Only the base's owner and admins can change its tables and fields" if n == "admin" else
                            "Only the base's owner can do this")
    return r


def bases(conn, user_id) -> list[dict]:
    """The bases a user owns or is a member of: [{base row…, role, follow_views, owner_name}]."""
    return conn.execute(
        "SELECT b.*, 'owner' AS role, true AS follow_views, NULL::text AS owner_name, b.position AS sidebar_position FROM bases b "
        "WHERE b.user_id = %s UNION ALL "
        "SELECT b.*, m.role, m.follow_views, u.username AS owner_name, m.position AS sidebar_position FROM base_members m "
        "JOIN bases b ON b.id = m.base_id JOIN users u ON u.id = b.user_id WHERE m.user_id = %s "
        "ORDER BY sidebar_position, created_at", (user_id, user_id)).fetchall()


def base_ids(conn, user_id) -> list[str]:
    return [str(r["id"]) for r in conn.execute(
        "SELECT id FROM bases WHERE user_id = %s UNION SELECT base_id FROM base_members WHERE user_id = %s", (user_id, user_id)).fetchall()]


def audience(conn, base_id) -> set[str]:
    """Everyone who sees a base (for live updates)."""
    rows = conn.execute("SELECT user_id FROM bases WHERE id = %s UNION SELECT user_id FROM base_members WHERE base_id = %s",
                        (base_id, base_id)).fetchall()
    return {str(r["user_id"]) for r in rows}
