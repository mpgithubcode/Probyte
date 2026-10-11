"""Two-way sync between a Probyte table and a DAV collection: tasks/events with a CalDAV calendar,
contacts with a CardDAV address book.

Incremental: when the collection's change tag (CTag / sync-token) is the same as last time, nothing is
downloaded; otherwise only items whose ETag changed are fetched. Conflict policy: a record edited locally
since the last sync (sync_status = 'dirty') wins; otherwise the server copy wins.
Runs in the worker (job "sync_table"), or directly for "Sync now".
"""
import logging
import os
from urllib.parse import urljoin

import caldav

from . import jobs, vcard, webhooks
from .carddav import CardDAV
from .dav import TIMEOUT as DAV_TIMEOUT, Collection, check_url, path_of
from .db import Jsonb, connection
from .ical import build_ical, component_kind, from_mixed, ical_to_values, to_mixed
from .secretbox import unseal
from .store import DAV_KINDS, apply_task_auto, ensure_choices, get_fields, get_table, insert_record

log = logging.getLogger("probyte.sync")

SYNC_INTERVAL = int(os.environ.get("SYNC_INTERVAL", "300"))      # seconds between background syncs; 0 disables


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


# ---------------------------------------------------------------- item <-> values per table kind

def _safe(uid: str) -> str:
    return "".join(c if c.isalnum() or c in "-_.@" else "_" for c in uid)


def parse_item(kind: str, url: str, raw: str):
    """(uid, values) of a server item, or (None, None) when it isn't the kind of item this table holds."""
    if kind == "contacts":
        uid, vals = vcard.card_to_values(raw)
        return uid or f"href:{url.rstrip('/').rsplit('/', 1)[-1]}", vals  # a few clients write cards without a UID
    if kind == "mixed":
        obj_kind = component_kind(raw)
        uid, vals = ical_to_values(obj_kind, raw)
        return (uid, to_mixed(obj_kind, vals)) if uid else (None, None)
    uid, vals = ical_to_values(kind, raw)
    return (uid, vals) if uid else (None, None)


def build_item(kind: str, collection_url: str, uid: str, vals: dict, url: str | None, raw: str | None) -> tuple[str, str]:
    """(item URL, body) to write for a record."""
    if kind == "contacts":
        if uid.startswith("href:") and raw:
            uid = vcard.Card(raw).get("UID") or uid[5:].removesuffix(".vcf")
        return url or urljoin(collection_url, _safe(uid) + ".vcf"), vcard.build_vcard(uid, vals, raw)
    obj_kind, vals = from_mixed(vals) if kind == "mixed" else (kind, vals)
    return url or urljoin(collection_url, _safe(uid) + ".ics"), build_ical(obj_kind, uid, vals, raw)


# ---------------------------------------------------------------- sync

def sync_table(table_id, full: bool = False) -> dict:
    """Sync one table now. Only one sync per table runs at a time (also across processes)."""
    table_id = str(table_id)
    with connection() as lock_conn:
        lock_conn.execute("SET statement_timeout = '120s'")
        lock_conn.execute("SELECT pg_advisory_lock(hashtext('sync:' || %s))", (table_id,))
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
    stats = {"pulled": 0, "updated": 0, "removed": 0, "pushed": 0, "deleted": 0, "fetched": 0, "skipped": False, "errors": []}

    with connection() as conn:
        table = get_table(conn, table_id)
        if table["kind"] not in DAV_KINDS:
            return stats
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
    by_key = {f["system_key"]: f for f in fields if f["system_key"]}
    sync_state = dict(table.get("sync_state") or {})
    url = table["calendar_url"] if table["calendar_url"].endswith("/") else table["calendar_url"] + "/"
    coll = Collection(url, account["username"] or None, unseal(account["password"]) or None,
                      "addressbook" if kind == "contacts" else "calendar")

    def to_data(vals: dict, base: dict) -> dict:
        data = dict(base)
        vals = dict(vals)
        # Many clients drop unknown properties; don't let a missing X-PROBYTE-STARTED wipe a local value.
        if vals.get("started") is None and "started" in by_key and base.get(str(by_key["started"]["id"])):
            vals.pop("started")
        changed_at = vals.pop("last_modified", None)
        if vals.get("status") == "Completed" and "completed" in by_key:
            cid, sid = str(by_key["completed"]["id"]), str(by_key["status"]["id"]) if "status" in by_key else None
            if base.get(cid) and base.get(sid) == "Completed":
                vals.pop("completed", None)  # already done here: keep when that was
            elif base:
                # Done on another device since the last sync. Some clients (iOS Reminders) put the reminder's
                # scheduled time in COMPLETED, so use when the change was made instead.
                vals["completed"] = changed_at or vals.get("completed")
        for key, val in vals.items():
            f = by_key.get(key)
            if f:
                ensure_choices(conn, f, val)
                data[str(f["id"])] = val
        # Status changed on another device: fill the task dates the server doesn't carry.
        if "status" in by_key:
            st = str(by_key["status"]["id"])
            explicit = {str(by_key[k]["id"]) for k in ("completed",) if k in by_key and vals.get(k)}
            apply_task_auto(table, fields, base if base else {st: "To Do"}, data, {st: data.get(st), **{k: data[k] for k in explicit}})
        return data

    def from_data(data: dict) -> dict:
        return {key: data.get(str(f["id"])) for key, f in by_key.items()}

    # ---- pull: only when the collection changed, and only the items that changed
    state = coll.state()
    if not full and state and state == sync_state.get("state"):
        stats["skipped"] = True
    else:
        etags = coll.etags()
        with connection() as conn:
            rows = conn.execute("SELECT * FROM records WHERE table_id = %s AND caldav_uid IS NOT NULL", (table_id,)).fetchall()
            by_uid = {r["caldav_uid"]: r for r in rows}
            by_path = {path_of(r["caldav_href"]): r for r in rows if r["caldav_href"]}
            ignored = {p: e for p, e in (sync_state.get("ignored") or {}).items()}   # other kinds of items in a shared calendar
            todo = [u for u, e in etags.items()
                    if (by_path.get(path_of(u)) or {}).get("caldav_etag") != e and ignored.get(path_of(u)) != e]
            fetched = coll.fetch(todo) if todo else {}
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
                    new = insert_record(conn, table_id, to_data(vals, {}), caldav_uid=uid, caldav_href=item_url, caldav_raw=raw)
                    conn.execute("UPDATE records SET caldav_etag = %s WHERE id = %s", (etag, new["id"]))
                    webhooks.emit(conn, table_id, "record.created", new["id"])
                    by_uid[uid] = {**new, "caldav_etag": etag}
                    stats["pulled"] += 1
                elif rec["deleted"] or rec["sync_status"] == "dirty":
                    conn.execute("UPDATE records SET caldav_href = %s WHERE id = %s", (item_url, rec["id"]))  # local edit wins
                elif rec["caldav_raw"] != raw:
                    # Only if nobody edited it here since we read it: a newer local edit wins (and is pushed next).
                    cur = conn.execute("UPDATE records SET data = %s, caldav_href = %s, caldav_raw = %s, caldav_etag = %s, updated_at = now() "
                                       "WHERE id = %s AND updated_at = %s AND sync_status = 'synced' AND NOT deleted",
                                       (Jsonb(to_data(vals, rec["data"])), item_url, raw, etag, rec["id"], rec["updated_at"]))
                    if cur.rowcount:
                        webhooks.emit(conn, table_id, "record.updated", rec["id"])
                        stats["updated"] += 1
                else:
                    conn.execute("UPDATE records SET caldav_href = %s, caldav_etag = %s WHERE id = %s", (item_url, etag, rec["id"]))
            # Removed on the server since our last sync (and not edited locally).
            present = {path_of(u) for u in etags}
            for rec in rows:
                if rec["caldav_href"] and path_of(rec["caldav_href"]) not in present and rec["sync_status"] == "synced" and not rec["deleted"]:
                    cur = conn.execute("DELETE FROM records WHERE id = %s AND updated_at = %s AND sync_status = 'synced'",
                                       (rec["id"], rec["updated_at"]))  # edited here meanwhile: keep it (it's pushed again)
                    if cur.rowcount:
                        webhooks.emit(conn, table_id, "record.deleted", rec["id"])
                        stats["removed"] += 1
            sync_state["ignored"] = {p: e for p, e in ignored.items() if p in present}

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
                item_url, body = build_item(kind, url, rec["caldav_uid"], from_data(rec["data"]), rec["caldav_href"], rec["caldav_raw"])
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
        if stats["pushed"] or stats["deleted"]:
            state = coll.state()  # our own writes changed the tag; remember it so the next sync can skip

    sync_state["state"] = state
    with connection() as conn:
        conn.execute("UPDATE tables SET sync_state = %s WHERE id = %s", (Jsonb(sync_state), table_id))
    return stats


# ---------------------------------------------------------------- scheduling (through the job queue)

@jobs.handler("sync_table")
def _job_sync(payload):
    with connection() as conn:
        t = conn.execute("SELECT kind, account_id, calendar_url FROM tables WHERE id = %s AND trashed_at IS NULL", (payload["table_id"],)).fetchone()
    if not t or t["kind"] not in DAV_KINDS or not t["account_id"] or not t["calendar_url"]:
        return  # deleted or unlinked since it was queued: nothing to do
    try:
        sync_table(payload["table_id"], bool(payload.get("full")))
    except ValueError:
        pass  # the account went away meanwhile: nothing to retry


def schedule_all():
    """Queue a sync of every synced table (the worker runs this every SYNC_INTERVAL)."""
    with connection() as conn:
        for t in conn.execute("SELECT id FROM tables WHERE kind = ANY(%s) AND account_id IS NOT NULL AND calendar_url IS NOT NULL AND trashed_at IS NULL",
                              (list(DAV_KINDS),)).fetchall():
            jobs.enqueue(conn, "sync_table", {"table_id": str(t["id"])}, key=f"sync:{t['id']}", max_attempts=2)
