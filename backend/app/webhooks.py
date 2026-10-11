"""Webhooks: record.created / record.updated / record.deleted events POSTed to user-chosen URLs.

Events are queued as jobs in the same transaction as the record change (from the app, the API, MCP, public
links, imports or a CalDAV sync), so a change that's rolled back sends nothing. The worker turns each event into
one delivery job per matching webhook; failed deliveries are retried with backoff. Several changes to one record
within a second become one event. Each delivery is signed:
X-Probyte-Signature: sha256=<hex HMAC of the body with the webhook's secret>.
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import HTTPException

from psycopg.types.json import Jsonb

from . import jobs
from .db import connection

log = logging.getLogger("probyte.webhooks")

EVENTS = ("record.created", "record.updated", "record.deleted")
# Like linked tables: no deliveries to private/internal addresses unless the server allows it
# (e.g. n8n or Home Assistant on your own network).
ALLOW_PRIVATE = os.environ.get("WEBHOOK_ALLOW_PRIVATE", os.environ.get("REMOTE_ALLOW_PRIVATE", "false")).lower() in ("1", "true", "yes")
TIMEOUT = 10

COALESCE_SECONDS = 1.0


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(24)


def emit(conn, table_id, event: str, record_id) -> None:
    """Queue a record event in the caller's transaction (merged with other changes to the record in the next second)."""
    conn.execute(
        "INSERT INTO jobs (kind, payload, dedupe_key, run_at, max_attempts) "
        "VALUES ('webhook_event', %s, %s, now() + make_interval(secs => %s), 3) "
        "ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL AND locked_until IS NULL AND NOT failed DO UPDATE SET payload = "
        "jsonb_set(jobs.payload, '{event}', to_jsonb(CASE WHEN jobs.payload->>'event' = 'record.created' "
        "AND EXCLUDED.payload->>'event' = 'record.updated' THEN 'record.created' ELSE EXCLUDED.payload->>'event' END))",
        (Jsonb({"table_id": str(table_id), "record_id": str(record_id), "event": event, "at": time.time()}),
         f"wh:{table_id}:{record_id}", COALESCE_SECONDS))


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


def deliver(hook, event: str, payload: dict, delivery: str | None = None) -> tuple[int | None, str | None]:
    """Send one event once and record the outcome on the webhook."""
    delivery = delivery or str(uuid.uuid4())
    body = json.dumps({"id": delivery, "event": event, "created_at": datetime.now(timezone.utc).isoformat(), **payload},
                      default=str, ensure_ascii=False).encode()
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
        rec = eng.record(table["id"], record_id)
        if rec is None:
            return {}
        out["record"] = apiv1.record_out(eng, fields, rec)
    return out


@jobs.handler("webhook_event")
def _job_event(p):
    """A record event: one delivery job per webhook that wants it."""
    with connection() as conn:
        table = conn.execute("SELECT id, name, base_id, user_id FROM tables WHERE id = %s", (p["table_id"],)).fetchone()
        if not table:
            return
        hooks = [h for h in conn.execute("SELECT * FROM webhooks WHERE user_id = %s AND enabled", (table["user_id"],)).fetchall()
                 if p["event"] in (h["events"] or EVENTS) and (h["table_ids"] is None or p["table_id"] in h["table_ids"])
                 and h["created_at"].timestamp() <= p.get("at", time.time())]  # only hooks that existed at the time
        if not hooks:
            return
        payload = _payload(conn, table, p["event"], p["record_id"])
        if not payload:
            return  # the record vanished before we got to it
        payload = json.loads(json.dumps(payload, default=str))  # dates/UUIDs as the JSON they'd be sent as
        for h in hooks:
            jobs.enqueue(conn, "webhook_deliver", {"hook_id": str(h["id"]), "event": p["event"], "payload": payload,
                                                   "delivery": str(uuid.uuid4())}, max_attempts=4)


@jobs.handler("webhook_deliver")
def _job_deliver(p):
    with connection() as conn:
        hook = conn.execute("SELECT * FROM webhooks WHERE id = %s AND enabled", (p["hook_id"],)).fetchone()
    if not hook:
        return
    status, err = deliver(dict(hook), p["event"], p["payload"], p.get("delivery"))
    if status is None or status >= 500 or status == 429:
        raise RuntimeError(err or f"HTTP {status}")  # retried with backoff by the job queue
