"""One-time move from the bundled Radicale (Probyte up to 0.8) to Probyte's own CalDAV/CardDAV server.

Migration 0020 made the tables on the built-in account collections of Probyte's server. Radicale was only synced
every few minutes, so a phone may have changed things there that Probyte hasn't seen, and calendars made on a phone
were in Radicale alone. This reads Radicale's files (its storage folder, still on the data volume) once:

- a collection with a table: items changed on a phone are written to their records (unless the record was also
  changed in Probyte since: that edit wins, as it did in the sync); new items become records; items that are gone
  were deleted on the phone, and their records go to the trash;
- a collection without a table becomes a new table in the user's first base, with everything in it.

It runs in the worker when the migration left app_settings 'radicale_import' = 'pending', and marks it done. Without
Radicale's folder (not mounted), it waits and tries again on the next start.
"""
import json
import logging
import os
from pathlib import Path

from app.core import jobs
from app.core.db import connection, Jsonb
from app.data import audit, store
from app.dav import server

log = logging.getLogger("probyte.dav.import")

CANDIDATES = [os.environ.get("RADICALE_DATA_DIR", ""), "/radicale-data/collections", "/radicale-data", "/data/radicale/collections"]


def storage_root() -> Path | None:
    for c in CANDIDATES:
        if c and (Path(c) / "collection-root").is_dir():
            return Path(c) / "collection-root"
    return None


def pending() -> bool:
    with connection() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'radicale_import'").fetchone()
    return bool(row and row["value"] == "pending")


def _props(folder: Path) -> dict:
    try:
        return json.loads((folder / ".Radicale.props").read_text())
    except (OSError, ValueError):
        return {}


def _items(folder: Path) -> dict[str, str]:
    out = {}
    for f in folder.iterdir():
        if f.is_file() and not f.name.startswith("."):
            try:
                out[f.name] = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                log.warning("skipped unreadable item %s", f)
    return out


def _kind(props: dict) -> str | None:
    tag = props.get("tag")
    if tag == "VADDRESSBOOK":
        return "contacts"
    if tag != "VCALENDAR":
        return None
    comps = {c.strip().upper() for c in (props.get("C:supported-calendar-component-set") or "").split(",") if c.strip()}
    return "tasks" if comps == {"VTODO"} else "events" if comps == {"VEVENT"} else "mixed"


def import_collection(conn, user, folder: Path, stats: dict) -> None:
    props = _props(folder)
    kind = _kind(props)
    if not kind:
        return
    slug = folder.name
    items = _items(folder)
    t = conn.execute("SELECT * FROM tables WHERE user_id = %s AND dav_slug = %s", (user["id"], slug)).fetchone()
    if t and t["trashed_at"]:
        return  # in Probyte's trash: deleted there, not brought back
    if t is None:
        base = store.ensure_base(conn, user["id"])
        t = store.create_table(conn, user["id"], base["id"], (props.get("D:displayname") or slug)[:200], kind)
        dead = {f"{{{server.ICAL}}}calendar-color": props["ICAL:calendar-color"]} if props.get("ICAL:calendar-color") else {}
        conn.execute("UPDATE tables SET dav_slug = %s, dav_props = %s WHERE id = %s", (slug, Jsonb(dead), t["id"]))
        t = conn.execute("SELECT * FROM tables WHERE id = %s", (t["id"],)).fetchone()
        stats["tables"] += 1
    records = {r["caldav_href"]: r for r in conn.execute(
        "SELECT id, caldav_href, caldav_raw, sync_status FROM records WHERE table_id = %s AND NOT deleted AND caldav_href IS NOT NULL",
        (t["id"],)).fetchall()}
    for name, text in items.items():
        rec = records.get(name)
        if rec and (rec["caldav_raw"] == text or rec["sync_status"] == "dirty"):
            continue  # unchanged, or also changed in Probyte since the last sync (that edit wins)
        try:
            with conn.transaction():
                server.write_item(conn, user, t, name, text)
            stats["items"] += 1
        except server.DavFail as e:
            log.warning("item %s/%s not imported (%s)", slug, name, e.status)
        except Exception:
            log.exception("item %s/%s not imported", slug, name)
    if items or not records:  # an empty folder next to a full table looks like a broken copy: delete nothing then
        for name, rec in records.items():
            if name not in items and rec["sync_status"] == "synced" and rec["caldav_raw"] is not None:
                store.delete_record(conn, rec["id"], user["id"], source="dav")  # deleted on the phone: to the trash
                stats["deleted"] += 1


def run() -> dict | None:
    root = storage_root()
    if root is None:
        log.warning("calendars moved into Probyte, but Radicale's folder wasn't found to bring in the last changes made on "
                    "phones (looked in %s); set RADICALE_DATA_DIR to it if you have it", ", ".join(c for c in CANDIDATES if c))
        return None
    stats = {"tables": 0, "items": 0, "deleted": 0}
    audit.set_actor("Phone (from the calendar server before the upgrade)")
    for user_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        with connection() as conn:
            user = conn.execute("SELECT id, username, is_admin FROM users WHERE username = %s", (user_dir.name.lower(),)).fetchone()
        if not user:
            continue
        for folder in sorted(p for p in user_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            with connection() as conn:
                import_collection(conn, user, folder, stats)
    with connection() as conn:
        conn.execute("UPDATE app_settings SET value = 'done' WHERE key = 'radicale_import'")
    log.info("brought in the calendars from Radicale: %s", stats)
    return stats


@jobs.handler("radicale_import")
def _job(payload):
    if pending():
        run()


def schedule_if_pending():
    if pending():
        jobs.enqueue(None, "radicale_import", key="radicale_import", max_attempts=3)
