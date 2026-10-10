"""Webhooks: record.created / record.updated / record.deleted events POSTed to user-chosen URLs.

Events are queued by the store when records change (from the app, the API, MCP, public links, imports or a
CalDAV sync) and delivered by a background thread. Several changes to one record within a moment become one
event. Each delivery is signed: X-Probyte-Signature: sha256=<hex HMAC of the body with the webhook's secret>.
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import queue
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import HTTPException

from .db import connection

log = logging.getLogger("probyte.webhooks")

EVENTS = ("record.created", "record.updated", "record.deleted")
# Like linked tables: no deliveries to private/internal addresses unless the server allows it
# (e.g. n8n or Home Assistant on your own network).
ALLOW_PRIVATE = os.environ.get("WEBHOOK_ALLOW_PRIVATE", os.environ.get("REMOTE_ALLOW_PRIVATE", "false")).lower() in ("1", "true", "yes")
TIMEOUT = 10
RETRIES = (2, 10, 60)  # seconds to wait before each retry
COALESCE = 1.0         # seconds to collect changes before sending

_q: "queue.Queue[tuple[str, str, str, float]]" = queue.Queue(maxsize=100000)


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(24)


def emit(table_id, event: str, record_id) -> None:
    """Queue an event (cheap; safe to call inside a transaction)."""
    try:
        _q.put_nowait((str(table_id), event, str(record_id), time.time()))
    except queue.Full:
        log.warning("webhook queue full; dropping %s %s", event, record_id)


# ---------------------------------------------------------------- URL safety

def check_url(url: str) -> str:
    u = urlparse((url or "").strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise HTTPException(400, "The webhook URL must start with http:// or https://")
    if not ALLOW_PRIVATE:
        try:
            infos = socket.getaddrinfo(u.hostname, None)
        except OSError:
            raise HTTPException(400, f"Can't find the server {u.hostname}")
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
                raise HTTPException(400, f"{u.hostname} is on a private network address. To send webhooks to your own network "
                                         "(e.g. n8n or Home Assistant), set WEBHOOK_ALLOW_PRIVATE=true on this server.")
    return url.strip()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _post(hook, body: bytes, event: str, delivery: str) -> tuple[int | None, str | None]:
    try:
        check_url(hook["url"])
    except HTTPException as e:
        return None, e.detail
    sig = hmac.new(hook["secret"].encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(hook["url"], data=body, method="POST", headers={
        "Content-Type": "application/json", "User-Agent": "Probyte-Webhooks/1",
        "X-Probyte-Event": event, "X-Probyte-Delivery": delivery, "X-Probyte-Signature": f"sha256={sig}",
    })
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=TIMEOUT) as r:
            return r.status, None
    except urllib.error.HTTPError as e:
        return e.code, f"HTTP {e.code}"
    except Exception as e:  # network errors, timeouts
        return None, str(e)[:300]


def deliver(hook, event: str, payload: dict, retry: bool = True) -> tuple[int | None, str | None]:
    """Send one event (with retries) and record the outcome on the webhook."""
    delivery = str(uuid.uuid4())
    body = json.dumps({"id": delivery, "event": event, "created_at": datetime.now(timezone.utc).isoformat(), **payload},
                      default=str, ensure_ascii=False).encode()
    status, err = _post(hook, body, event, delivery)
    for wait in RETRIES if retry else ():
        if status is not None and status < 500 and status != 429:
            break
        time.sleep(wait)
        status, err = _post(hook, body, event, delivery)
    ok = status is not None and 200 <= status < 300
    try:
        with connection() as conn:
            conn.execute("UPDATE webhooks SET last_status = %s, last_error = %s, last_delivery_at = now() WHERE id = %s",
                         (status, None if ok else (err or f"HTTP {status}"), hook["id"]))
    except Exception:
        log.exception("could not record webhook result")
    return status, err


# ---------------------------------------------------------------- worker

def _payload(conn, table, event, record_id) -> dict:
    from . import apiv1
    from .compute import Engine
    base = conn.execute("SELECT id, name FROM bases WHERE id = %s", (table["base_id"],)).fetchone()
    out = {"table": {"id": str(table["id"]), "name": table["name"]},
           "base": {"id": str(base["id"]), "name": base["name"]} if base else None,
           "record": {"id": record_id}}
    if event != "record.deleted":
        eng = Engine(conn)
        fields = eng.describe(table["id"])
        rec = next((r for r in eng.records(table["id"]) if str(r["id"]) == record_id), None)
        if rec is None:
            return {}
        out["record"] = apiv1.record_out(eng, fields, rec)
    return out


def _process(batch: list[tuple[str, str, str, float]]):
    # created + updated = created; anything + deleted = deleted
    merged: dict[tuple[str, str], str] = {}
    first_at: dict[tuple[str, str], float] = {}
    for tid, event, rid, at in batch:
        first_at.setdefault((tid, rid), at)
        prev = merged.get((tid, rid))
        if prev == "record.created" and event == "record.updated":
            continue
        merged[(tid, rid)] = event
    with connection() as conn:
        tables = {}
        for (tid, rid), event in merged.items():
            if tid not in tables:
                tables[tid] = conn.execute("SELECT id, name, base_id, user_id FROM tables WHERE id = %s", (tid,)).fetchone()
            table = tables[tid]
            if not table:
                continue
            hooks = [h for h in conn.execute("SELECT * FROM webhooks WHERE user_id = %s AND enabled", (table["user_id"],)).fetchall()
                     if event in (h["events"] or EVENTS) and (h["table_ids"] is None or tid in h["table_ids"])
                     and h["created_at"].timestamp() <= first_at[(tid, rid)]]  # only hooks that existed at the time
            if not hooks:
                continue
            payload = _payload(conn, table, event, rid)
            if not payload:
                continue  # the record vanished before we got to it
            for h in hooks:
                threading.Thread(target=deliver, args=(dict(h), event, payload), daemon=True).start()


def worker(stop: threading.Event):
    while not stop.is_set():
        try:
            first = _q.get(timeout=1)
        except queue.Empty:
            continue
        time.sleep(COALESCE)
        batch = [first]
        while True:
            try:
                batch.append(_q.get_nowait())
            except queue.Empty:
                break
        try:
            _process(batch)
        except Exception:
            log.exception("webhook processing failed")
