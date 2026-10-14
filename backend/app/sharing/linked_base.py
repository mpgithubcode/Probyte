"""A whole base shared by a public link, added as a base of linked tables on this side.

The base remembers where it comes from (bases.linked): the link, whether its views stay updated, and which linked
table stands for which shared table. Checking the link again (sync, also in the background when the workspace is
loaded and the last check is a few minutes old) adds the tables shared since; a table no longer shared stays, saying
so (its link answers "not found"), until it's deleted here.
"""
import logging
import time

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from app.core import jobs
from app.core.db import connection
from app.sharing import linked

log = logging.getLogger("probyte.linked")
CHECK_EVERY = 180  # seconds between background checks of a linked base


def public(b) -> dict | None:
    """What the browser may know about a linked base (never the access cookie)."""
    li = b.get("linked") if b else None
    if not li:
        return None
    return {"url": li.get("url"), "name": li.get("name"), "follow_views": li.get("follow_views", True), "error": li.get("error"),
            "checked_at": li.get("checked_at"), "tables": len(li.get("tables") or {}), "local": li.get("local")}


def add(conn, store, user_id, url: str, password: str | None, name: str | None, follow_views: bool = True) -> dict:
    """A new base from a link to a whole base (or to several tables): one linked table for each."""
    link = linked.parse_link(url)
    link.update(url=url.strip(), local=linked.is_local(conn, link["token"]))
    if password:
        link["cookie"] = linked.unlock(conn, link, password)
    meta = linked.fetch_meta(conn, link)
    if meta.get("needs_password"):
        raise HTTPException(401, "password_required")
    if not meta.get("items"):
        raise HTTPException(400, "That link doesn't share any tables")
    pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM bases WHERE user_id = %s", (user_id,)).fetchone()["p"]
    info = {"url": link["url"], "local": link["local"], "follow_views": bool(follow_views), "tables": {},
            "name": meta.get("base_name") or meta.get("name"), **({"cookie": link["cookie"]} if link.get("cookie") else {})}
    b = conn.execute("INSERT INTO bases (user_id, name, color, position, linked) VALUES (%s,%s,%s,%s,%s) RETURNING *",
                     (user_id, (name or "").strip() or info["name"] or "Linked base", meta.get("color") or "#6366f1", pos, Jsonb(info))).fetchone()
    sync(conn, store, b, meta=meta)
    return conn.execute("SELECT * FROM bases WHERE id = %s", (b["id"],)).fetchone()


def sync(conn, store, base, meta=None) -> dict:
    """Add linked tables for what the link shares now. Returns {"added": [names], "gone": [names]}."""
    info = dict(base["linked"] or {})
    link = {**linked.parse_link(info["url"]), "url": info["url"], "local": info.get("local"), **({"cookie": info["cookie"]} if info.get("cookie") else {})}
    added, gone = [], []
    try:
        meta = meta or linked.fetch_meta(conn, link)
        if meta.get("needs_password"):
            raise HTTPException(401, "The link needs its password again (it may have changed)")
        have = {k: v for k, v in (info.get("tables") or {}).items()
                if conn.execute("SELECT 1 FROM tables WHERE id = %s AND trashed_at IS NULL", (v,)).fetchone()}
        shared = {i["id"]: i for i in meta.get("items") or []}
        for iid, item in shared.items():
            if iid in have or not item.get("views"):
                continue
            t = linked.create_table(conn, store, base["user_id"], base["id"], item["name"], info["url"], None, iid, item["views"][0]["id"],
                                    cookie=info.get("cookie"), follow_views=info.get("follow_views", True))
            have[iid] = str(t["id"])
            added.append(item["name"])
        for iid, tid in have.items():
            if iid not in shared:
                row = conn.execute("SELECT name FROM tables WHERE id = %s", (tid,)).fetchone()
                gone.append(row["name"] if row else iid)
        info.update(tables=have, error=None, name=meta.get("base_name") or meta.get("name") or info.get("name"))
    except HTTPException as e:
        info["error"] = str(e.detail)
    info["checked_at"] = time.time()
    conn.execute("UPDATE bases SET linked = %s WHERE id = %s", (Jsonb(info), base["id"]))
    return {"added": added, "gone": gone, "error": info.get("error")}


def set_follow_views(conn, base, on: bool):
    """The base's setting, for all of its linked tables (and ones added later)."""
    conn.execute("UPDATE bases SET linked = linked || %s WHERE id = %s", (Jsonb({"follow_views": bool(on)}), base["id"]))
    for t in conn.execute("SELECT * FROM tables WHERE base_id = %s AND kind = 'remote'", (base["id"],)).fetchall():
        linked.set_follow_views(conn, t, on)


def check_soon(conn, bases):
    """Queue a check of the linked bases whose last one is a few minutes old (when the workspace is loaded)."""
    for b in bases:
        li = b.get("linked")
        if li and time.time() - (li.get("checked_at") or 0) > CHECK_EVERY:
            jobs.enqueue(conn, "linked_base_sync", {"base_id": str(b["id"])}, key=f"linked-base:{b['id']}", max_attempts=1)


@jobs.handler("linked_base_sync")
def _job(payload):
    from app.data import store
    with connection() as conn:
        b = conn.execute("SELECT * FROM bases WHERE id = %s", (payload["base_id"],)).fetchone()
        if b and b["linked"]:
            r = sync(conn, store, b)
            if r["added"]:
                log.info("linked base %s: added %s", b["id"], ", ".join(r["added"]))
