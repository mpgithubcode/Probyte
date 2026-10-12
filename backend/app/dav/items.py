"""Records <-> DAV items for one table: iCalendar VTODO / VEVENT for tasks, events and mixed tables, vCard for
contacts. Used by Probyte's own CalDAV/CardDAV server (dav/server.py) and by the sync with other servers
(dav/sync.py), so a phone writing to Probyte and a calendar pulled from elsewhere are read the same way.
"""
import hashlib

from app.data.store import apply_task_auto, ensure_choices
from app.formats import vcard
from app.formats.ical import build_ical, component_kind, from_mixed, ical_to_values, to_mixed


def safe_name(uid: str) -> str:
    return "".join(c if c.isalnum() or c in "-_.@" else "_" for c in uid)


def item_name(kind: str, uid: str) -> str:
    """The file name of a record's item in its collection."""
    return safe_name(uid) + (".vcf" if kind == "contacts" else ".ics")


def etag(body: str) -> str:
    return '"' + hashlib.md5(body.encode()).hexdigest() + '"'


def parse_item(kind: str, name: str, raw: str):
    """(uid, values) of an item, or (None, None) when it isn't the kind of item this table holds."""
    if kind == "contacts":
        uid, vals = vcard.card_to_values(raw)
        return uid or f"href:{name.rstrip('/').rsplit('/', 1)[-1]}", vals  # a few clients write cards without a UID
    if kind == "mixed":
        obj_kind = component_kind(raw)
        uid, vals = ical_to_values(obj_kind, raw)
        return (uid, to_mixed(obj_kind, vals)) if uid else (None, None)
    uid, vals = ical_to_values(kind, raw)
    return (uid, vals) if uid else (None, None)


def build_body(kind: str, uid: str, vals: dict, raw: str | None) -> str:
    """The item for a record's values, keeping what the previous version of the item had that Probyte doesn't show."""
    if kind == "contacts":
        if uid.startswith("href:") and raw:
            uid = vcard.Card(raw).get("UID") or uid[5:].removesuffix(".vcf")
        return vcard.build_vcard(uid, vals, raw)
    obj_kind, vals = from_mixed(vals) if kind == "mixed" else (kind, vals)
    return build_ical(obj_kind, uid, vals, raw)


class Mapper:
    """Item values (keyed by system field, e.g. "summary") <-> a record's data (keyed by field id) for one table."""

    def __init__(self, conn, table, fields):
        self.conn, self.table, self.fields = conn, table, fields
        self.by_key = {f["system_key"]: f for f in fields if f["system_key"]}

    def from_data(self, data: dict) -> dict:
        return {key: data.get(str(f["id"])) for key, f in self.by_key.items()}

    def body(self, record) -> str:
        """A record's item as it should be now."""
        uid = record["caldav_uid"]
        return build_body(self.table["kind"], uid, self.from_data(record["data"]), record["caldav_raw"])

    def to_data(self, vals: dict, base: dict) -> dict:
        """A record's data after an item's values came in (from a device or another server); `base` is the record's
        data before (empty for a new record)."""
        by_key, conn, table, fields = self.by_key, self.conn, self.table, self.fields
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
                # Done on another device since. Some clients (iOS Reminders) put the reminder's scheduled time in
                # COMPLETED, so use when the change was made instead.
                vals["completed"] = changed_at or vals.get("completed")
        for key, val in vals.items():
            f = by_key.get(key)
            if f:
                ensure_choices(conn, f, val)
                data[str(f["id"])] = val
        # Status changed on another device: fill the task dates the item doesn't carry.
        if "status" in by_key:
            st = str(by_key["status"]["id"])
            explicit = {str(by_key[k]["id"]) for k in ("completed",) if k in by_key and vals.get(k)}
            apply_task_auto(table, fields, base if base else {st: "To Do"}, data, {st: data.get(st), **{k: data[k] for k in explicit}})
        return data
