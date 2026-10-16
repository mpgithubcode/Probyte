"""Two-way sync between a Probyte table and a collection on another DAV server (Nextcloud, iCloud, Google…):
tasks/events with a CalDAV calendar, contacts with a CardDAV address book. (Tables on Probyte's own server need
none of this: phones read and write them directly, see dav/server.py.)

Incremental: when the collection's change tag (CTag / sync-token) is the same as last time, nothing is
downloaded; otherwise only items whose ETag changed are fetched. Conflict policy: a record edited locally
since the last sync (sync_status = 'dirty') wins; otherwise the server copy wins.
Runs in the worker (job "sync_table"): a few seconds after a local edit (on_record_change, through the change feed),
when someone opens the table (sync_if_stale), every SYNC_INTERVAL, or directly for "Sync now".
"""
import logging
import os
from urllib.parse import urljoin

import caldav

from app.core import events, jobs
from app.core.db import connection, Jsonb
from app.core.secretbox import unseal
from app.core.webdav import check_url, Collection, path_of, TIMEOUT as DAV_TIMEOUT
from app.data import audit
from app.data.store import DAV_KINDS, get_fields, get_table, insert_record, is_synced
from app.dav.carddav import CardDAV
from app.dav.items import build_body, item_name, Mapper, parse_item

log = logging.getLogger("probyte.sync")

SYNC_INTERVAL = int(os.environ.get("SYNC_INTERVAL", "120"))      # seconds between background syncs; 0 disables
AUTO_PUSH_DELAY = float(os.environ.get("AUTO_PUSH_DELAY", "2"))  # seconds after an edit before it is pushed; <0 disables
STALE_AFTER = float(os.environ.get("SYNC_ON_OPEN_AFTER", "20"))  # opening a table syncs it when its last sync is older


# ---------------------------------------------------------------- accounts: discovery

def dav_client(account) -> caldav.DAVClient:
    return caldav.DAVClient(url=check_url(account["url"]), username=account["username"] or None,
                            password=unseal(account["password"]) or None, timeout=DAV_TIMEOUT)


def card_client(account) -> CardDAV:
    return CardDAV(check_url(account.get("carddav_url") or account["url"]), account["username"] or None, unseal(account["password"]) or None)


def list_addressbooks(account) -> list[dict]:
    return card_client(account).addressbooks()


def create_addressbook(account, name: str) -> str:
    return card_client(account).create_addressbook(name)


def list_calendars(account) -> list[dict]:
    out = []
    for cal in dav_client(account).principal().calendars():
        try:
            comps = cal.get_supported_components()
        except Exception:
            comps = ["VEVENT", "VTODO"]
        out.append({"url": str(cal.url), "name": cal.name or str(cal.url).rstrip("/").rsplit("/", 1)[-1], "components": comps})
    return out


def create_calendar(account, name: str, components: list[str]) -> str:
    cal = dav_client(account).principal().make_calendar(name=name, supported_calendar_component_set=components)
    return str(cal.url)


# ---------------------------------------------------------------- items

def build_item(kind: str, collection_url: str, uid: str, vals: dict, url: str | None, raw: str | None) -> tuple[str, str]:
    """(item URL, body) to write for a record."""
    return url or urljoin(collection_url, item_name(kind, uid)), build_body(kind, uid, vals, raw)


# ---------------------------------------------------------------- sync

def sync_table(table_id, full: bool = False) -> dict:
    """Sync one table now. Only one sync per table runs at a time (also across processes)."""
    table_id = str(table_id)
    with connection() as lock_conn:
        lock_conn.execute("SET statement_timeout = '120s'")
        lock_conn.execute("SELECT pg_advisory_lock(hashtext('sync:' || %s))", (table_id,))
        lock_conn.commit()   # (the lock is the session's: no transaction stays open, idle, while the sync runs)
        try:
            stats = _sync(table_id, full)
            with connection() as conn:
                conn.execute("UPDATE tables SET last_synced_at = now(), last_sync_error = %s WHERE id = %s",
                             ("; ".join(stats["errors"]) or None, table_id))
            return stats
        except Exception as e:
            log.exception("sync of table %s failed", table_id)
            with connection() as conn:
                conn.execute("UPDATE tables SET last_sync_error = %s WHERE id = %s", (str(e)[:1000], table_id))
            raise
        finally:
            lock_conn.execute("SELECT pg_advisory_unlock(hashtext('sync:' || %s))", (table_id,))


def _sync(table_id: str, full: bool) -> dict:
    audit.set_actor("Phone / calendar sync")  # changes pulled from the server, in record history
    stats = {"pulled": 0, "updated": 0, "removed": 0, "pushed": 0, "deleted": 0, "fetched": 0, "skipped": False, "errors": []}

    with connection() as conn:
        table = get_table(conn, table_id)
        if table["kind"] == "files":
            from app.files import catalog as files
            return files.sync(table, stats, full)
        if table["kind"] not in DAV_KINDS or table.get("dav_slug"):
            return stats  # on Probyte's own server: nothing to sync
        if not table["account_id"] or not table["calendar_url"]:
            raise ValueError("Table is not linked to an address book" if table["kind"] == "contacts"
                             else "Table is not linked to a CalDAV calendar")
        account = conn.execute("SELECT * FROM caldav_accounts WHERE id = %s", (table["account_id"],)).fetchone()
        if not account:
            raise ValueError("CalDAV account no longer exists")
        fields = get_fields(conn, table_id)
        has_local = conn.execute("SELECT EXISTS (SELECT 1 FROM records WHERE table_id = %s AND (sync_status = 'dirty' OR deleted)) AS e",
                                 (table_id,)).fetchone()["e"]
    kind = table["kind"]
    sync_state = dict(table.get("sync_state") or {})
    url = table["calendar_url"] if table["calendar_url"].endswith("/") else table["calendar_url"] + "/"
    coll = Collection(url, account["username"] or None, unseal(account["password"]) or None,
                      "addressbook" if kind == "contacts" else "calendar")

    # ---- pull: only when the collection changed, and only the items that changed
    state = coll.state()
    if not full and state and state == sync_state.get("state"):
        stats["skipped"] = True
    else:
        # what changed: since the last sync-token when the server keeps them (only the changes come back), otherwise
        # every item's ETag
        delta = coll.changes_since(sync_state["token"]) if not full and sync_state.get("token") and coll.token else None
        if delta:
            _, etags, removed = delta
        else:
            etags, removed = coll.etags(), None
        with connection() as conn:
            rows = conn.execute("SELECT * FROM records WHERE table_id = %s AND caldav_uid IS NOT NULL", (table_id,)).fetchall()
            by_uid = {r["caldav_uid"]: r for r in rows}
            by_path = {path_of(r["caldav_href"]): r for r in rows if r["caldav_href"]}
            ignored = {p: e for p, e in (sync_state.get("ignored") or {}).items()}   # other kinds of items in a shared calendar
            todo = [u for u, e in etags.items()
                    if (by_path.get(path_of(u)) or {}).get("caldav_etag") != e and ignored.get(path_of(u)) != e]
            fetched = coll.fetch(todo) if todo else {}
            mapper = Mapper(conn, table, fields)
            stats["fetched"] = len(fetched)
            for item_url, (etag, raw) in fetched.items():
                p = path_of(item_url)
                try:
                    uid, vals = parse_item(kind, item_url, raw)
                except Exception as e:
                    stats["errors"].append(f"parse {item_url}: {e}")
                    continue
                if not uid:
                    ignored[p] = etag
                    continue
                rec = by_uid.get(uid)
                if rec is None:
                    new = insert_record(conn, table_id, mapper.to_data(vals, {}), caldav_uid=uid, caldav_href=item_url, caldav_raw=raw)
                    conn.execute("UPDATE records SET caldav_etag = %s WHERE id = %s", (etag, new["id"]))
                    audit.note(conn, table_id, new["id"], "created")
                    events.record_changed(conn, table, "record.created", new["id"], source="sync")
                    by_uid[uid] = {**new, "caldav_etag": etag}
                    stats["pulled"] += 1
                elif rec["deleted"] or rec["sync_status"] == "dirty":
                    conn.execute("UPDATE records SET caldav_href = %s WHERE id = %s", (item_url, rec["id"]))  # local edit wins
                elif rec["caldav_raw"] != raw:
                    # Only if nobody edited it here since we read it: a newer local edit wins (and is pushed next).
                    new_data = mapper.to_data(vals, rec["data"])
                    cur = conn.execute("UPDATE records SET data = %s, caldav_href = %s, caldav_raw = %s, caldav_etag = %s, updated_at = now() "
                                       "WHERE id = %s AND updated_at = %s AND sync_status = 'synced' AND NOT deleted",
                                       (Jsonb(new_data), item_url, raw, etag, rec["id"], rec["updated_at"]))
                    if cur.rowcount:
                        audit.note(conn, table_id, rec["id"], "updated", rec["data"], new_data, fields)
                        events.record_changed(conn, table, "record.updated", rec["id"], source="sync", old=rec["data"])
                        stats["updated"] += 1
                else:
                    conn.execute("UPDATE records SET caldav_href = %s, caldav_etag = %s WHERE id = %s", (item_url, etag, rec["id"]))
            # Removed on the server since our last sync (and not edited locally).
            gone = ({path_of(u) for u in removed} if removed is not None
                    else {path_of(r["caldav_href"]) for r in rows if r["caldav_href"]} - {path_of(u) for u in etags})
            for rec in rows:
                if rec["caldav_href"] and path_of(rec["caldav_href"]) in gone and rec["sync_status"] == "synced" and not rec["deleted"]:
                    cur = conn.execute("DELETE FROM records WHERE id = %s AND updated_at = %s AND sync_status = 'synced'",
                                       (rec["id"], rec["updated_at"]))  # edited here meanwhile: keep it (it's pushed again)
                    if cur.rowcount:
                        audit.note(conn, table_id, rec["id"], "deleted")
                        events.record_changed(conn, table, "record.deleted", rec["id"], source="sync")
                        stats["removed"] += 1
            sync_state["ignored"] = ({p: e for p, e in ignored.items() if p not in gone} if removed is not None
                                     else {p: e for p, e in ignored.items() if p in {path_of(u) for u in etags}})
        # the token from before our own pushes: they come back next time, and are skipped (their ETags are known)
        sync_state["token"] = coll.token

    # ---- push
    if has_local or not stats["skipped"]:
        with connection() as conn:
            pending = conn.execute("SELECT * FROM records WHERE table_id = %s AND (sync_status = 'dirty' OR deleted)", (table_id,)).fetchall()
        for rec in pending:
            try:
                if rec["deleted"]:
                    if rec["caldav_href"]:
                        coll.delete(rec["caldav_href"])
                    with connection() as conn:
                        conn.execute("DELETE FROM records WHERE id = %s", (rec["id"],))
                    stats["deleted"] += 1
                    continue
                item_url, body = build_item(kind, url, rec["caldav_uid"], Mapper(None, table, fields).from_data(rec["data"]), rec["caldav_href"], rec["caldav_raw"])
                etag = coll.put(item_url, body)
                with connection() as conn:
                    # Only mark synced if nobody edited the record while we were pushing.
                    conn.execute("UPDATE records SET caldav_href = %s, caldav_raw = %s, caldav_etag = %s, "
                                 "sync_status = CASE WHEN updated_at = %s THEN 'synced' ELSE sync_status END WHERE id = %s",
                                 (item_url, body, etag, rec["updated_at"], rec["id"]))
                stats["pushed"] += 1
            except Exception as e:
                log.warning("push of record %s failed: %s", rec["id"], e)
                stats["errors"].append(f"push {rec['id']}: {e}")
        if (stats["pushed"] or stats["deleted"]) and not coll.token:
            # our own writes changed the tag; remember it so the next sync can skip (with a sync-token the next sync
            # asks for the changes since before them instead: ours come back with known ETags, and nothing made
            # on another device meanwhile is missed)
            state = coll.state()

    sync_state["state"] = state
    with connection() as conn:
        conn.execute("UPDATE tables SET sync_state = %s WHERE id = %s", (Jsonb(sync_state), table_id))
    return stats


# ---------------------------------------------------------------- scheduling (through the job queue)

def schedule(table_id, delay: float | None = None, conn=None):
    """Sync a table soon (debounced). With `conn`, the job is queued in that transaction, so it only exists once
    the change that asked for it is committed."""
    delay = AUTO_PUSH_DELAY if delay is None else delay
    if delay < 0:
        return
    jobs.enqueue(conn, "sync_table", {"table_id": str(table_id)}, key=f"sync:{table_id}", delay=delay, max_attempts=3)


def on_record_change(conn, table, event, record_id, source, old=None):
    """Change feed: a record of a table linked to another server changed here; push it there shortly."""
    if source != "sync" and is_synced(table):
        schedule(table["id"], conn=conn)


def sync_if_stale(conn, table):
    """Someone opened the table: catch up with the other server now if the last sync is a while ago (the check is
    one request when nothing changed there), so what was just changed on a phone shows up."""
    if not is_synced(table) and not (table["kind"] == "files" and table.get("storage_id")):
        return
    last = table.get("last_synced_at")
    if last is None or (conn.execute("SELECT now() - %s > make_interval(secs => %s) AS stale", (last, STALE_AFTER)).fetchone()["stale"]):
        schedule(table["id"], 0, conn=conn)


@jobs.handler("sync_table")
def _job_sync(payload):
    with connection() as conn:
        t = conn.execute("SELECT kind, account_id, calendar_url, storage_id FROM tables WHERE id = %s AND trashed_at IS NULL", (payload["table_id"],)).fetchone()
    if t and t["kind"] == "files" and t["storage_id"]:
        pass
    elif not t or t["kind"] not in DAV_KINDS or not t["account_id"] or not t["calendar_url"]:
        return  # deleted or unlinked since it was queued: nothing to do
    try:
        sync_table(payload["table_id"], bool(payload.get("full")))
    except ValueError:
        pass  # the account went away meanwhile: nothing to retry
    except Exception as e:
        if e.__class__.__name__ == "StorageError":
            return  # recorded on the table (last_sync_error); tried again at the next interval
        raise


def schedule_all():
    """Queue a sync of every synced table (the worker runs this every SYNC_INTERVAL)."""
    with connection() as conn:
        for t in conn.execute("SELECT id FROM tables WHERE trashed_at IS NULL AND ((kind = ANY(%s) AND account_id IS NOT NULL AND calendar_url IS NOT NULL) "
                              "OR (kind = 'files' AND storage_id IS NOT NULL))", (list(DAV_KINDS),)).fetchall():
            jobs.enqueue(conn, "sync_table", {"table_id": str(t["id"])}, key=f"sync:{t['id']}", max_attempts=2)
