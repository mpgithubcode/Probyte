"""Two-way sync between a Probyte table and a DAV collection: tasks/events with a CalDAV calendar,
contacts with a CardDAV address book.

Conflict policy: a record edited locally since the last sync (sync_status = 'dirty') wins;
otherwise the server copy wins.
"""
import logging
import os
import threading
import time
from urllib.parse import urljoin

import caldav
from caldav.lib.error import NotFoundError

from . import vcard, webhooks
from .carddav import CardDAV
from .db import Jsonb, connection
from .ical import build_ical, component_kind, from_mixed, ical_to_values, to_mixed
from .store import apply_task_auto, ensure_choices, get_fields, get_table, insert_record

log = logging.getLogger("probyte.sync")

SYNC_INTERVAL = int(os.environ.get("SYNC_INTERVAL", "300"))  # seconds; 0 disables background sync
AUTO_PUSH_DELAY = float(os.environ.get("AUTO_PUSH_DELAY", "2"))  # seconds after an edit; <0 disables

_locks: dict[str, threading.Lock] = {}
_timers: dict[str, threading.Timer] = {}
_guard = threading.Lock()


def dav_client(account) -> caldav.DAVClient:
    return caldav.DAVClient(url=account["url"], username=account["username"] or None, password=account["password"] or None)


def card_client(account) -> CardDAV:
    return CardDAV(account.get("carddav_url") or account["url"], account["username"] or None, account["password"] or None)


def list_addressbooks(account) -> list[dict]:
    return card_client(account).addressbooks()


def create_addressbook(account, name: str) -> str:
    return card_client(account).create_addressbook(name)


def list_calendars(account) -> list[dict]:
    client = dav_client(account)
    out = []
    for cal in client.principal().calendars():
        try:
            comps = cal.get_supported_components()
        except Exception:
            comps = ["VEVENT", "VTODO"]
        out.append({"url": str(cal.url), "name": cal.name or str(cal.url).rstrip("/").rsplit("/", 1)[-1], "components": comps})
    return out


def create_calendar(account, name: str, components: list[str]) -> str:
    client = dav_client(account)
    cal = client.principal().make_calendar(name=name, supported_calendar_component_set=components)
    return str(cal.url)


class _Calendar:
    """VTODO / VEVENT objects of a CalDAV calendar."""

    def __init__(self, account, url, kind):
        self.client = dav_client(account)
        self.calendar = self.client.calendar(url=url)
        self.kind = kind

    def fetch(self) -> list[tuple[str, str]]:
        objs = []
        if self.kind in ("tasks", "mixed"):
            objs += self.calendar.search(todo=True, include_completed=True)
        if self.kind in ("events", "mixed"):
            objs += self.calendar.search(event=True)
        return [(str(o.url), o.data) for o in objs]

    def parse(self, href, raw):
        if self.kind == "mixed":
            obj_kind = component_kind(raw)
            uid, vals = ical_to_values(obj_kind, raw)
            return uid, to_mixed(obj_kind, vals)
        return ical_to_values(self.kind, raw)

    def save(self, uid, vals, href, raw) -> tuple[str, str]:
        obj_kind, vals = from_mixed(vals) if self.kind == "mixed" else (self.kind, vals)
        body = build_ical(obj_kind, uid, vals, raw)
        cls = caldav.Todo if obj_kind == "tasks" else caldav.Event
        href = href or urljoin(str(self.calendar.url).rstrip("/") + "/", _safe(uid) + ".ics")
        cls(client=self.client, url=href, data=body, parent=self.calendar).save()
        return href, body

    def delete(self, href):
        try:
            caldav.CalendarObjectResource(client=self.client, url=href, parent=self.calendar).delete()
        except NotFoundError:
            pass


class _Contacts:
    """vCards of a CardDAV address book."""

    def __init__(self, account, url):
        self.client = card_client(account)
        self.url = url if url.endswith("/") else url + "/"

    def fetch(self):
        return self.client.cards(self.url)

    def parse(self, href, raw):
        uid, vals = vcard.card_to_values(raw)
        return uid or f"href:{href.rstrip('/').rsplit('/', 1)[-1]}", vals  # a few clients write cards without a UID

    def save(self, uid, vals, href, raw):
        if uid.startswith("href:") and raw:
            uid = vcard.Card(raw).get("UID") or uid[5:].removesuffix(".vcf")
        body = vcard.build_vcard(uid, vals, raw)
        href = href or urljoin(self.url, _safe(uid) + ".vcf")
        self.client.put(href, body)
        return href, body

    def delete(self, href):
        self.client.delete(href)


def sync_table(table_id) -> dict:
    table_id = str(table_id)
    with _guard:
        lock = _locks.setdefault(table_id, threading.Lock())
    with lock:
        try:
            stats = _sync(table_id)
            with connection() as conn:
                conn.execute("UPDATE tables SET last_synced_at = now(), last_sync_error = %s WHERE id = %s",
                             ("; ".join(stats["errors"]) or None, table_id))
            return stats
        except Exception as e:
            log.exception("sync of table %s failed", table_id)
            with connection() as conn:
                conn.execute("UPDATE tables SET last_sync_error = %s WHERE id = %s", (str(e)[:1000], table_id))
            raise


def _sync(table_id: str) -> dict:
    stats = {"pulled": 0, "updated": 0, "removed": 0, "pushed": 0, "deleted": 0, "errors": []}

    with connection() as conn:
        table = get_table(conn, table_id)
        if table["kind"] in ("generic", "remote"):
            return stats
        if not table["account_id"] or not table["calendar_url"]:
            raise ValueError("Table is not linked to an address book" if table["kind"] == "contacts"
                             else "Table is not linked to a CalDAV calendar")
        account = conn.execute("SELECT * FROM caldav_accounts WHERE id = %s", (table["account_id"],)).fetchone()
        if not account:
            raise ValueError("CalDAV account no longer exists")
        fields = get_fields(conn, table_id)
    kind = table["kind"]
    by_key = {f["system_key"]: f for f in fields if f["system_key"]}

    dav = _Contacts(account, table["calendar_url"]) if kind == "contacts" else _Calendar(account, table["calendar_url"], kind)
    remote = dav.fetch()

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

    # ---- pull
    with connection() as conn:
        local = {r["caldav_uid"]: r for r in conn.execute(
            "SELECT * FROM records WHERE table_id = %s AND caldav_uid IS NOT NULL", (table_id,)).fetchall()}
        seen = set()
        for href, raw in remote:
            try:
                uid, vals = dav.parse(href, raw)
            except Exception as e:
                stats["errors"].append(f"parse {href}: {e}")
                continue
            if not uid or uid in seen:
                continue
            seen.add(uid)
            rec = local.get(uid)
            if rec is None:
                new = insert_record(conn, table_id, to_data(vals, {}), caldav_uid=uid, caldav_href=href, caldav_raw=raw)
                webhooks.emit(table_id, "record.created", new["id"])
                stats["pulled"] += 1
            elif rec["deleted"] or rec["sync_status"] == "dirty":
                if rec["caldav_href"] != href:
                    conn.execute("UPDATE records SET caldav_href = %s WHERE id = %s", (href, rec["id"]))
            elif rec["caldav_raw"] != raw:
                conn.execute(
                    "UPDATE records SET data = %s, caldav_href = %s, caldav_raw = %s, updated_at = now() WHERE id = %s",
                    (Jsonb(to_data(vals, rec["data"])), href, raw, rec["id"]),
                )
                webhooks.emit(table_id, "record.updated", rec["id"])
                stats["updated"] += 1

        # Removed on the server since our last sync (and not edited locally).
        for uid, rec in local.items():
            if uid not in seen and rec["caldav_href"] and rec["sync_status"] == "synced":
                conn.execute("DELETE FROM records WHERE id = %s", (rec["id"],))
                webhooks.emit(table_id, "record.deleted", rec["id"])
                stats["removed"] += 1

        pending = conn.execute(
            "SELECT * FROM records WHERE table_id = %s AND (sync_status = 'dirty' OR deleted)", (table_id,)
        ).fetchall()

    # ---- push
    for rec in pending:
        try:
            if rec["deleted"]:
                if rec["caldav_href"]:
                    dav.delete(rec["caldav_href"])
                with connection() as conn:
                    conn.execute("DELETE FROM records WHERE id = %s", (rec["id"],))
                stats["deleted"] += 1
                continue

            vals = from_data(rec["data"])
            href, body = dav.save(rec["caldav_uid"], vals, rec["caldav_href"], rec["caldav_raw"])
            with connection() as conn:
                # Only mark synced if nobody edited the record while we were pushing.
                conn.execute(
                    "UPDATE records SET caldav_href = %s, caldav_raw = %s, "
                    "sync_status = CASE WHEN updated_at = %s THEN 'synced' ELSE sync_status END WHERE id = %s",
                    (href, body, rec["updated_at"], rec["id"]),
                )
            stats["pushed"] += 1
        except Exception as e:
            log.warning("push of record %s failed: %s", rec["id"], e)
            stats["errors"].append(f"push {rec['id']}: {e}")
    return stats


def _safe(uid: str) -> str:
    return "".join(c if c.isalnum() or c in "-_.@" else "_" for c in uid)


# ---------------------------------------------------------------- scheduling

def schedule_sync(table_id, delay: float | None = None):
    """Debounced background sync, used to push local edits shortly after they happen."""
    delay = AUTO_PUSH_DELAY if delay is None else delay
    if delay < 0:
        return
    table_id = str(table_id)

    def run():
        with _guard:
            _timers.pop(table_id, None)
        try:
            sync_table(table_id)
        except Exception:
            pass

    with _guard:
        if (t := _timers.pop(table_id, None)) is not None:
            t.cancel()
        t = threading.Timer(delay, run)
        t.daemon = True
        _timers[table_id] = t
        t.start()


def background_loop(stop: threading.Event):
    if SYNC_INTERVAL <= 0:
        return
    while not stop.wait(SYNC_INTERVAL):
        try:
            with connection() as conn:
                ids = [r["id"] for r in conn.execute(
                    "SELECT id FROM tables WHERE kind NOT IN ('generic', 'remote') AND account_id IS NOT NULL AND calendar_url IS NOT NULL"
                ).fetchall()]
            for tid in ids:
                try:
                    sync_table(tid)
                except Exception:
                    pass
        except Exception:
            log.exception("background sync loop error")
            time.sleep(5)
