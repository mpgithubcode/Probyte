"""Calendar subscription links: a secret URL serving a table's (or a combined database's) records as an iCalendar
feed, read-only, for calendar apps to subscribe to (Apple Calendar, Google Calendar, Outlook…).

A feed can follow a view: then only that view's records (its filters) are in it, and a calendar or timeline view's
date fields are used. Otherwise the start / end dates are the table's synced Start and End / Due, or its first
date fields. Records without a date are left out.
"""
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from icalendar import Calendar, Event
from icalendar.prop import vRecur
from pydantic import BaseModel

from app.data import store

from app.calc.compute import Engine
from app.calc.viewfilter import apply_view, display_text
from app.core.db import connection
from app.formats import recur
from app.formats.ical import as_date, parse_value_dt
from app.identity.auth import User
from app.identity.oauth import public_base

router = APIRouter()


class FeedIn(BaseModel):
    table_id: str | None = None
    combo_id: str | None = None
    view_id: str | None = None
    name: str | None = None


def _out(f, request: Request) -> dict:
    url = f"{public_base(request)}/ics/{f['token']}.ics"
    return {"id": str(f["id"]), "name": f["name"], "view_id": str(f["view_id"]) if f["view_id"] else None, "url": url,
            "webcal": "webcal://" + url.split("://", 1)[1], "created_at": f["created_at"], "last_fetched_at": f["last_fetched_at"]}


@router.get("/api/feeds")
def list_feeds(request: Request, table_id: str | None = None, combo_id: str | None = None, user=User):
    with connection() as conn:
        rows = conn.execute("SELECT * FROM calendar_feeds WHERE user_id = %s AND (table_id = %s OR combo_id = %s) ORDER BY created_at",
                            (user["id"], table_id, combo_id)).fetchall()
    return {"feeds": [_out(f, request) for f in rows]}


@router.post("/api/feeds")
def create_feed(body: FeedIn, request: Request, user=User):
    with connection() as conn:
        if body.table_id:
            src = store.get_table(conn, body.table_id, user["id"], need="viewer")
        elif body.combo_id:
            src = store.get_combo(conn, body.combo_id, user["id"], need="viewer")
        else:
            raise HTTPException(400, "Say which table the calendar is for")
        if body.view_id and not conn.execute("SELECT 1 FROM views WHERE id = %s AND (table_id = %s OR combo_id = %s)",
                                             (body.view_id, body.table_id, body.combo_id)).fetchone():
            store.not_found("View not found")
        f = conn.execute(
            "INSERT INTO calendar_feeds (token, user_id, table_id, combo_id, view_id, name) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
            (secrets.token_urlsafe(24), user["id"], body.table_id, body.combo_id, body.view_id, (body.name or src["name"]).strip()[:100])).fetchone()
    return _out(f, request)


@router.post("/api/feeds/{feed_id}/regenerate")
def regenerate_feed(feed_id: str, request: Request, user=User):
    """A new secret address; the old one stops working (subscribers have to subscribe again)."""
    with connection() as conn:
        f = conn.execute("UPDATE calendar_feeds SET token = %s WHERE id = %s AND user_id = %s RETURNING *",
                         (secrets.token_urlsafe(24), feed_id, user["id"])).fetchone() or store.not_found("Link not found")
    return _out(f, request)


@router.delete("/api/feeds/{feed_id}")
def delete_feed(feed_id: str, user=User):
    with connection() as conn:
        conn.execute("DELETE FROM calendar_feeds WHERE id = %s AND user_id = %s", (feed_id, user["id"]))
    return {"ok": True}


# ---------------------------------------------------------------- the feed

def _date_fields(fields, config) -> tuple[dict | None, dict | None]:
    by_id = {str(f["id"]): f for f in fields}
    if config.get("dateField") in by_id:
        return by_id[config["dateField"]], by_id.get(config.get("endField") or "")
    keys = {f.get("system_key"): f for f in fields if f.get("system_key")}
    if "dtstart" in keys or "due" in keys or "dtend" in keys:
        return keys.get("dtstart") or keys.get("due"), keys.get("dtend") or keys.get("due")
    dates = [f for f in fields if f["type"] == "date"]
    return (dates[0] if dates else None), (dates[1] if len(dates) > 1 else None)


def _pick(fields, *keys, types=("long_text", "text")):
    """A synced field by key, else the first field whose name suggests it."""
    for f in fields:
        if f.get("system_key") in keys:
            return f
    names = {"description": ("description", "notes", "details"), "location": ("location", "place", "where")}
    for k in keys:
        for f in fields:
            if f["type"] in types and f["name"].strip().lower() in names.get(k, ()):
                return f
    return None


def build_feed(conn, feed) -> bytes:
    eng = Engine(conn)
    if feed["table_id"]:
        table = store.get_table(conn, feed["table_id"])
        fields = eng.describe(table["id"])
        records = eng.records(table["id"])
        name_field = next((f for f in fields if f["is_primary"]), None)
    else:
        combo = store.get_combo(conn, feed["combo_id"])
        _, fields = store.resolve_combo(conn, combo, eng)
        records = store.combo_records(conn, combo, eng)
        name_field = fields[0] if fields else None
    config = {}
    if feed["view_id"]:
        v = conn.execute("SELECT config FROM views WHERE id = %s", (feed["view_id"],)).fetchone()
        config = v["config"] if v else {}
        records = apply_view(eng, fields, records, config)
    start_f, end_f = _date_fields(fields, config)
    desc_f, loc_f = _pick(fields, "description", "note"), _pick(fields, "location")
    status_f = next((f for f in fields if f.get("system_key") == "status"), None)
    repeat_f = next((f for f in fields if f["type"] == "recurrence"), None)

    cal = Calendar()
    cal.add("prodid", "-//Probyte//Calendar feed//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", feed["name"])
    cal.add("refresh-interval", timedelta(hours=1), parameters={"VALUE": "DURATION"})
    cal.add("x-published-ttl", "PT1H")
    now = datetime.now(timezone.utc)
    for r in records:
        d = r["data"]
        start = parse_value_dt(d.get(str(start_f["id"]))) if start_f else None
        end = parse_value_dt(d.get(str(end_f["id"]))) if end_f else None
        if start is None:
            start, end = end, None  # e.g. a task with only a due date
        if start is None:
            continue
        ev = Event()
        ev.add("uid", f"{r['id']}@probyte")
        ev.add("dtstamp", now)
        ev.add("summary", display_text(eng, name_field, d.get(str(name_field["id"]))) if name_field else "Untitled")
        all_day = not isinstance(start, datetime)
        if all_day:
            s, e = as_date(start), as_date(end) if end is not None else as_date(start)
            ev.add("dtstart", s)
            ev.add("dtend", max(e, s) + timedelta(days=1))  # exclusive
        else:
            e = end if isinstance(end, datetime) and end > start else start + timedelta(hours=1)
            ev.add("dtstart", start.astimezone(timezone.utc))
            ev.add("dtend", e.astimezone(timezone.utc))
        if desc_f and d.get(str(desc_f["id"])):
            ev.add("description", display_text(eng, desc_f, d.get(str(desc_f["id"]))))
        if loc_f and d.get(str(loc_f["id"])):
            ev.add("location", display_text(eng, loc_f, d.get(str(loc_f["id"]))))
        if status_f and d.get(str(status_f["id"])) == "Cancelled":
            ev.add("status", "CANCELLED")
        rule, exdates = recur.parse(d.get(str(repeat_f["id"]))) if repeat_f else (None, [])
        if rule:
            ev.add("rrule", vRecur.from_ical(rule))
            for x in exdates:
                xd = parse_value_dt(x)
                if xd is not None:
                    ev.add("exdate", as_date(xd) if all_day else xd.astimezone(timezone.utc))
        cal.add_component(ev)
    return cal.to_ical()


@router.get("/ics/{token}.ics", include_in_schema=False)
def feed(token: str):
    with connection() as conn:
        f = conn.execute("SELECT * FROM calendar_feeds WHERE token = %s", (token,)).fetchone()
        if not f:
            raise HTTPException(404, "This calendar link doesn't exist (any more)")
        try:
            body = build_feed(conn, f)
        except HTTPException:
            raise HTTPException(404, "The table of this calendar was deleted")
        conn.execute("UPDATE calendar_feeds SET last_fetched_at = now() WHERE id = %s", (f["id"],))
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "private, max-age=300", "Content-Disposition": f'inline; filename="{token[:8]}.ics"'})
