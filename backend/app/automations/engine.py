"""Automations: when something happens in a table, and the record matches the conditions, do the actions.

    trigger      created | updated (optionally: when one of these fields changes) | matches (when a record starts
                 matching the conditions) | deleted | schedule (every hour / day / week / month, at a time)
    conditions   a filter, as a view's (calc/viewfilter.py)
    actions      update (this record) | create (a record in a table) | webhook (POST the record) | email | trash

Values in actions are written as a person would type them, and may name the record's fields: "{Status}",
"Invoice for {Client}", or "{today}", "{now}", "{record_id}".

How it runs: the change feed (on_record_change) queues a job for each automation watching the table, in the writing
transaction; the worker evaluates the trigger and conditions on the computed record and does the actions, with the
rights of whoever made the automation (access is checked at run time), and logs the run. Changes an automation makes
don't start automations (no loops). Schedules are checked every minute.
"""
import json
import logging
import os
import re
import smtplib
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import unquote, urlparse

from fastapi import HTTPException

from app.adapters import apiv1, webhooks
from app.calc.compute import Engine
from app.calc.viewfilter import display_text, matches
from app.core import jobs
from app.core.context import set_actor
from app.core.db import connection
from app.data import access, store, values
from app.formats.ical import DEFAULT_TZ

log = logging.getLogger("probyte.automations")

TRIGGERS = ("created", "updated", "matches", "deleted", "schedule")
ACTIONS = ("update", "create", "webhook", "email", "trash")
EVERY = ("hour", "day", "week", "month")
SCHEDULE_LIMIT = 500        # records one scheduled run goes through
CACHE_SECONDS = 10
SMTP_URL = os.environ.get("SMTP_URL", "").strip()   # smtp(s)://user:password@host:port (empty: no email action)
SMTP_FROM = os.environ.get("SMTP_FROM", "").strip()

_watching: dict[str, tuple[float, list]] = {}   # table id -> (until, [automation rows])


def email_enabled() -> bool:
    return bool(SMTP_URL and SMTP_FROM)


def invalidate():
    _watching.clear()


def _for_table(conn, table_id) -> list:
    hit = _watching.get(str(table_id))
    if hit and hit[0] > time.monotonic():
        return hit[1]
    rows = conn.execute("SELECT id, trigger FROM automations WHERE table_id = %s AND enabled", (table_id,)).fetchall()
    _watching[str(table_id)] = (time.monotonic() + CACHE_SECONDS, rows)
    return rows


EVENT_TRIGGERS = {"record.created": ("created", "matches"), "record.updated": ("updated", "matches"), "record.deleted": ("deleted",)}


def on_record_change(conn, table, event, record_id, source, old=None):
    """Change feed: queue a run of each automation watching the table for this kind of change."""
    if source == "automation" or table.get("kind") == "remote":
        return
    wanted = EVENT_TRIGGERS.get(event, ())
    for a in _for_table(conn, table["id"]):
        if (a["trigger"] or {}).get("type") in wanted:
            jobs.enqueue(conn, "automation_run", {"automation_id": str(a["id"]), "record_id": str(record_id), "event": event,
                                                  "old": old}, max_attempts=2)


# ---------------------------------------------------------------- running

class Failed(Exception):
    pass


def _render(template, rec, fields, eng) -> str:
    """A value with {Field} names replaced by the record's values (as text)."""
    if not isinstance(template, str):
        return template
    by_name = {f["name"].lower(): f for f in fields}

    def sub(m):
        key = m.group(1).strip()
        low = key.lower()
        if low == "today":
            return datetime.now(DEFAULT_TZ).date().isoformat()
        if low == "now":
            return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if low == "record_id":
            return str(rec["id"]) if rec else ""
        f = by_name.get(low)
        if not f or not rec:
            return m.group(0)
        return display_text(eng, f, rec["data"].get(str(f["id"])))
    return re.sub(r"\{([^{}]+)\}", sub, template)


def _values_for(conn, table_id, raw: dict, rec, src_fields, eng) -> dict:
    """{field id or name: value as typed, with {Field} names filled in} -> stored values of the target table."""
    target = eng.describe(table_id)
    filled = {k: _render(v, rec, src_fields, eng) for k, v in (raw or {}).items()}
    try:
        return apiv1.coerce(target, filled, values.Lookups(conn))
    except HTTPException as e:
        raise Failed(str(e.detail))


def _record_json(eng, fields, rec) -> dict:
    return {"id": str(rec["id"]), "fields": {f["name"]: display_text(eng, f, rec["data"].get(str(f["id"]))) for f in fields}}


def _post_json(url: str, body: dict):
    try:
        url = webhooks.check_url(url)
    except HTTPException as e:
        raise Failed(e.detail)
    req = urllib.request.Request(url, data=json.dumps(body, default=str, ensure_ascii=False).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "Probyte-Automations/1"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        raise Failed(f"the webhook answered HTTP {e.code}")
    except Exception as e:
        raise Failed(f"the webhook couldn't be reached ({str(e)[:120]})")


def _send_email(to: str, subject: str, body: str):
    if not email_enabled():
        raise Failed("Email isn't set up on this server (SMTP_URL and SMTP_FROM)")
    u = urlparse(SMTP_URL)
    host, port = u.hostname, u.port or (465 if u.scheme == "smtps" else 587)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = SMTP_FROM, to, subject[:200]
    msg.set_content(body)
    try:
        if u.scheme == "smtps":
            s = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
        else:
            s = smtplib.SMTP(host, port, timeout=20)
            s.starttls(context=ssl.create_default_context())
        with s:
            if u.username:
                s.login(unquote(u.username), unquote(u.password or ""))
            s.send_message(msg)
    except Exception as e:
        raise Failed(f"the email couldn't be sent ({str(e)[:160]})")


def _act(conn, a, table, rec, fields, eng, actor_id, deleted=False) -> list[str]:
    """Do an automation's actions for one record (or none: a scheduled run without records). What was done, as text."""
    done = []
    for act in a["actions"] or []:
        kind = act.get("type")
        if kind == "update":
            if not rec or deleted:
                continue
            patch = _values_for(conn, table["id"], act.get("fields"), rec, fields, eng)
            store.update_record(conn, rec["id"], patch, actor_id, source="automation")
            done.append(f"changed {len(patch)} field(s)")
        elif kind == "create":
            target = store.get_table(conn, act.get("table_id") or table["id"])
            access.check(conn, target["base_id"], actor_id, "editor")
            if target["kind"] in ("remote", "files"):
                raise Failed("Automations can't add records to linked or files tables")
            data = _values_for(conn, target["id"], act.get("fields"), rec, fields, eng)
            new, _ = store.create_record(conn, target["id"], data, actor_id, source="automation")
            done.append(f"created a record in {target['name']}")
        elif kind == "webhook":
            body = {"automation": a["name"], "table": table["name"], "record": _record_json(eng, fields, rec) if rec else None}
            _post_json(act.get("url") or "", body)
            done.append("called the webhook")
        elif kind == "email":
            to = _render(act.get("to") or "", rec, fields, eng).strip()
            if not to:
                raise Failed("The email has no address to go to")
            _send_email(to, _render(act.get("subject") or a["name"], rec, fields, eng), _render(act.get("body") or "", rec, fields, eng))
            done.append(f"emailed {to}")
        elif kind == "trash":
            if rec and not deleted:
                store.delete_record(conn, rec["id"], actor_id, source="automation")
                done.append("moved the record to the trash")
    return done


def _log(conn, a, record_id, ok: bool, message: str, started: float):
    ms = int((time.monotonic() - started) * 1000)
    conn.execute("INSERT INTO automation_runs (automation_id, record_id, ok, message, ms) VALUES (%s,%s,%s,%s,%s)",
                 (a["id"], record_id, ok, message[:1000], ms))
    conn.execute("UPDATE automations SET last_run_at = now(), last_ok = %s, last_message = %s, runs = runs + 1 WHERE id = %s",
                 (ok, message[:1000], a["id"]))


def _computed(eng, table_id, record_id):
    r = eng.record(table_id, record_id)
    return {**r, "data": dict(r["data"])} if r else None


def run(automation_id: str, record_id: str | None, event: str | None, old: dict | None = None, *, test=False) -> dict:
    """One run for one record (a change), or a scheduled run (record_id None). Returns {"ok", "message", "ran"}."""
    started = time.monotonic()
    with connection() as conn:
        a = conn.execute("SELECT * FROM automations WHERE id = %s", (automation_id,)).fetchone()
        if not a or (not a["enabled"] and not test):
            return {"ok": True, "message": "off", "ran": False}
        table = conn.execute("SELECT * FROM tables WHERE id = %s AND trashed_at IS NULL", (a["table_id"],)).fetchone()
        actor = a["created_by"]
        try:
            if not table:
                raise Failed("Its table is gone (or in the trash)")
            if not actor:
                raise Failed("Whoever made it no longer has an account")
            access.check(conn, table["base_id"], actor, "editor")
        except (Failed, HTTPException) as e:
            msg = str(e.detail if isinstance(e, HTTPException) else e)
            _log(conn, a, record_id, False, msg, started)
            return {"ok": False, "message": msg, "ran": True}
        set_actor(f"Automation “{a['name']}”", actor)
        eng = Engine(conn)
        fields = eng.describe(table["id"])
        trig = a["trigger"] or {}
        try:
            if record_id is None:  # a schedule
                if trig.get("once"):
                    done = _act(conn, a, table, None, fields, eng, actor)
                    msg = "; ".join(done) or "nothing to do"
                else:
                    recs = [{**r, "data": dict(r["data"])} for r in eng.records(table["id"])]
                    hits = [r for r in recs if matches(eng, fields, r, a["conditions"])][:SCHEDULE_LIMIT]
                    for r in hits:
                        with conn.transaction():
                            _act(conn, a, table, r, fields, eng, actor)
                    msg = f"ran for {len(hits)} record(s)"
                _log(conn, a, None, True, msg, started)
                return {"ok": True, "message": msg, "ran": True}
            if event == "record.deleted":
                rec = {"id": record_id, "data": dict(old or {})}
            else:
                rec = _computed(eng, table["id"], record_id)
                if rec is None:
                    return {"ok": True, "message": "the record is gone", "ran": False}
            if not test and not _triggered(eng, fields, a, trig, event, rec, old):
                return {"ok": True, "message": "not triggered", "ran": False}
            with conn.transaction():
                done = _act(conn, a, table, rec, fields, eng, actor, deleted=event == "record.deleted")
            msg = "; ".join(done) or "nothing to do"
            _log(conn, a, record_id, True, msg, started)
            return {"ok": True, "message": msg, "ran": True}
        except (Failed, HTTPException) as e:
            msg = str(e.detail if isinstance(e, HTTPException) else e)
            _log(conn, a, record_id, False, msg, started)
            return {"ok": False, "message": msg, "ran": True}


def _triggered(eng, fields, a, trig, event, rec, old) -> bool:
    now_matches = matches(eng, fields, rec, a["conditions"])
    t = trig.get("type")
    if t == "matches":
        if not now_matches:
            return False
        if event == "record.created" or old is None:
            return event == "record.created"
        before = {**rec, "data": {**rec["data"], **{k: v for k, v in old.items()}}}
        return not matches(eng, fields, before, a["conditions"])
    if not now_matches:
        return False
    if t == "updated":
        watch = [str(x) for x in trig.get("fields") or []]
        if watch and old is not None:
            return any(old.get(f) != rec["data"].get(f) for f in watch)
        return event == "record.updated"
    return (t == "created" and event == "record.created") or (t == "deleted" and event == "record.deleted")


@jobs.handler("automation_run")
def _job(payload):
    run(payload["automation_id"], payload.get("record_id"), payload.get("event"), payload.get("old"))


# ---------------------------------------------------------------- schedules

def next_run(trig: dict, after: datetime | None = None) -> datetime | None:
    """When a scheduled automation runs next (in the server's time zone, DEFAULT_TZ)."""
    if (trig or {}).get("type") != "schedule":
        return None
    every = trig.get("every") if trig.get("every") in EVERY else "day"
    now = (after or datetime.now(timezone.utc)).astimezone(DEFAULT_TZ)
    hh, mm = 8, 0
    m = re.match(r"^(\d{1,2}):(\d{2})$", str(trig.get("at") or ""))
    if m:
        hh, mm = min(23, int(m.group(1))), min(59, int(m.group(2)))
    if every == "hour":
        nxt = now.replace(minute=mm, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(hours=1)
    elif every == "day":
        nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
    elif every == "week":
        wd = int(trig.get("weekday") or 0) % 7  # 0 = Monday
        nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0) + timedelta(days=(wd - now.weekday()) % 7)
        if nxt <= now:
            nxt += timedelta(days=7)
    else:
        day = max(1, min(28, int(trig.get("day") or 1)))
        nxt = now.replace(day=day, hour=hh, minute=mm, second=0, microsecond=0)
        if nxt <= now:
            nxt = (nxt.replace(day=1) + timedelta(days=32)).replace(day=day)
    return nxt.astimezone(timezone.utc)


def run_due():
    """Worker, every minute: queue the scheduled automations whose time has come."""
    with connection() as conn:
        for a in conn.execute("SELECT id, trigger FROM automations WHERE enabled AND next_run_at <= now() FOR UPDATE SKIP LOCKED").fetchall():
            conn.execute("UPDATE automations SET next_run_at = %s WHERE id = %s", (next_run(a["trigger"]), a["id"]))
            jobs.enqueue(conn, "automation_run", {"automation_id": str(a["id"]), "record_id": None, "event": None}, max_attempts=1)


def prune(conn):
    conn.execute("DELETE FROM automation_runs WHERE at < now() - interval '30 days'")


def validate(conn, base_id, body: dict) -> dict:
    """Checked settings of an automation (from the editor or the API)."""
    trig = dict(body.get("trigger") or {})
    if trig.get("type") not in TRIGGERS:
        raise HTTPException(400, "Choose when it runs")
    t = conn.execute("SELECT * FROM tables WHERE id = %s AND base_id = %s AND trashed_at IS NULL", (body.get("table_id"), base_id)).fetchone()
    if not t:
        raise HTTPException(400, "Choose one of this base's tables to watch")
    if t["kind"] == "remote":
        raise HTTPException(400, "Linked tables change at their source; automations can't watch them")
    acts = []
    for act in body.get("actions") or []:
        kind = act.get("type")
        if kind not in ACTIONS:
            raise HTTPException(400, "Unknown action")
        if kind == "webhook":
            webhooks.check_url(act.get("url") or "")
        if kind == "email" and not email_enabled():
            raise HTTPException(400, "Email isn't set up on this server (SMTP_URL and SMTP_FROM)")
        if kind == "create":
            tt = conn.execute("SELECT kind, base_id FROM tables WHERE id = %s AND trashed_at IS NULL", (act.get("table_id"),)).fetchone()
            if not tt or str(tt["base_id"]) != str(base_id):
                raise HTTPException(400, "Choose a table of this base to add records to")
            if tt["kind"] in ("remote", "files"):
                raise HTTPException(400, "Automations can't add records to linked or files tables")
        if kind in ("update", "trash") and trig["type"] in ("deleted",):
            raise HTTPException(400, "A deleted record can't be changed")
        acts.append({k: v for k, v in act.items() if k in ("type", "fields", "table_id", "url", "to", "subject", "body")})
    if not acts:
        raise HTTPException(400, "Add at least one action")
    if trig["type"] == "schedule":
        trig = {k: trig.get(k) for k in ("type", "every", "at", "weekday", "day", "once") if trig.get(k) is not None}
    elif trig["type"] == "updated":
        trig = {"type": "updated", "fields": [str(x) for x in trig.get("fields") or []]}
    else:
        trig = {"type": trig["type"]}
    cond = body.get("conditions") if isinstance(body.get("conditions"), dict) else {"conjunction": "and", "items": []}
    return {"name": (body.get("name") or "").strip()[:120] or "Automation", "enabled": body.get("enabled", True) is not False,
            "table_id": str(t["id"]), "trigger": trig, "conditions": cond, "actions": acts, "next_run_at": next_run(trig)}
