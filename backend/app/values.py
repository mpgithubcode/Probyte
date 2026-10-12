"""Turning values into what a field stores, in one place:

- from_api(): values written by people and API clients (REST v1, MCP); strict, a bad value is a 400.
- from_cell(): spreadsheet/CSV cells being imported; lenient, an unreadable cell is skipped.
- convert() / convert_field(): existing values when a field changes type.

Lookups caches the title -> record id and username -> user id maps that link and user fields need.
"""
import json
import re

from fastapi import HTTPException

from . import fieldformats, formula as F
from . import recur, store
from .compute import COMPUTED_TYPES, Engine
from .db import Jsonb


def bad(msg: str, code: int = 400):
    raise HTTPException(code, msg)


class Lookups:
    def __init__(self, conn):
        self.conn = conn
        self._titles: dict = {}
        self._users: dict | None = None

    def titles(self, table_id) -> dict:
        """lower-cased record title -> record id."""
        key = str(table_id)
        if key not in self._titles:
            self._titles[key] = {v.lower(): k for k, v in Engine(self.conn).titles(table_id).items()} if table_id else {}
        return self._titles[key]

    def users(self) -> dict:
        """lower-cased username -> user id."""
        if self._users is None:
            self._users = {r["username"].lower(): str(r["id"]) for r in self.conn.execute("SELECT id, username FROM users").fetchall()}
        return self._users

    def for_field(self, f) -> dict:
        if f["type"] == "link":
            return self.titles(f["options"].get("foreignTableId"))
        if f["type"] == "user":
            return self.users()
        return {}


TEXT_TYPES = {"text", "long_text", "url", "email", "phone"}


def snapshot(conn, field) -> dict:
    """Each record's current value of a field, as (raw value, visible items), before a type change."""
    eng = Engine(conn)
    out = {}
    for r in eng.records(field["table_id"]):
        raw = r["data"].get(str(field["id"]))
        if isinstance(raw, dict) and "__error" in raw:
            raw = None
        shown = eng.shown(field, raw)
        if field["type"] in ("lookup", "conditional_lookup"):
            inn = eng.inner(field)
            shown = eng.shown(inn[2], raw) if inn else raw
        vt = eng.value_type(field)
        items = []
        for x in (shown if isinstance(shown, list) else [shown]):
            if F.is_blank(x):
                continue
            if vt == "date" and isinstance(x, str):
                try:
                    dt = F.local(F.parse_dt(x))
                    x = x if len(x) == 10 else dt.strftime("%Y-%m-%d %H:%M")
                except F.FormulaError:
                    pass
            items.append(F.to_str(x) if not isinstance(x, str) else x)
        out[str(r["id"])] = (raw, items, vt)
    return out


def convert(raw, items, vt, old_type, new_type, opts, lookup):
    """A value shown as `items` in a field of old_type -> the stored value for new_type (None if it doesn't fit).
    `lookup`: lower-cased title/username -> id, for link and user fields."""
    text = ", ".join(items)
    if new_type == "recurrence":
        try:
            return recur.normalize(raw if isinstance(raw, str) else text)
        except ValueError:
            return None
    if new_type in fieldformats.FORMAT_TYPES:  # email / url / phone: only values that fit (the rest are left empty)
        try:
            return fieldformats.clean(new_type, raw if isinstance(raw, str) and old_type in TEXT_TYPES else text)
        except ValueError:
            return None
    if new_type in TEXT_TYPES:
        if old_type in TEXT_TYPES:
            return raw
        if old_type == "checkbox":
            return "true" if raw else None
        return text or None
    if new_type in ("number", "rating"):
        n = None
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            n = raw
        elif isinstance(raw, bool):
            n = 1 if raw else 0
        elif len(items) == 1:
            m = re.search(r"-?\d+(?:\.\d+)?", items[0].replace(",", ""))
            n = float(m.group()) if m else None
        if n is None:
            return None
        if new_type == "rating":
            return max(0, min(int(round(n)), int(opts.get("max") or 5))) or None
        return int(n) if float(n).is_integer() else n
    if new_type == "checkbox":
        if isinstance(raw, bool):
            return raw
        return bool(items) and items[0].strip().lower() not in ("false", "0", "no", "off", "unchecked")
    if new_type == "single_select":
        return items[0] if items else None
    if new_type == "multi_select":
        parts = items if len(items) != 1 else [p.strip() for p in items[0].split(",")]
        return list(dict.fromkeys(p for p in parts if p)) or None
    if new_type == "date":
        src = raw if vt == "date" and isinstance(raw, str) else (items[0] if items else None)
        if not src:
            return None
        try:
            dt = F.parse_dt(src)
        except F.FormulaError:
            try:
                dt = F.datetime_parse(src, "DD/MM/YYYY") if "/" in src else None
            except F.FormulaError:
                dt = None
        if dt is None:
            return None
        date_only = (isinstance(src, str) and len(src.strip()) == 10) or not opts.get("includeTime")
        return F.local(dt).date().isoformat() if date_only else F.to_json(dt)
    if new_type in ("link", "user"):
        parts = items if len(items) != 1 else [p.strip() for p in items[0].split(",")]
        ids = [lookup[p.lower()] for p in parts if p.lower() in lookup]
        ids = list(dict.fromkeys(ids))
        if new_type == "link" and opts.get("relationship") == "one":
            ids = ids[:1]
        if new_type == "user" and not opts.get("multiple"):
            ids = ids[:1]
        return ids or None
    return None  # attachments can't be made from other values; computed fields have no stored value


def from_cell(field, cell, lookups: "Lookups"):
    """A spreadsheet cell -> a value for the field (None when it can't be read)."""
    lookup = lookups.for_field(field)
    t = field["type"]
    if isinstance(cell, bool):
        text = "true" if cell else "false"
    elif isinstance(cell, float) and cell.is_integer():
        text = str(int(cell))
    else:
        text = str(cell).strip()
    if t == "recurrence":
        try:
            return recur.normalize(text)
        except ValueError:
            return None
    if t in fieldformats.FORMAT_TYPES:
        return text  # checked when the record is saved: a value that doesn't fit makes that row an error, with the reason
    raw = cell if t in ("number", "rating", "checkbox") and not isinstance(cell, str) else text
    vt = "date" if t == "date" and re.match(r"^\d{4}-\d{2}-\d{2}", text) else "text"
    return convert(raw, [text], vt, "text", t, field["options"], lookup)


def convert_field(conn, old_field, new_field, snapshot: dict):
    """Rewrite stored values of a field whose type changed, converting instead of discarding them."""
    new_type, opts = new_field["type"], new_field["options"]
    fid, tid = str(new_field["id"]), str(new_field["table_id"])
    if new_type in COMPUTED_TYPES or new_type == "attachment":
        conn.execute("UPDATE records SET data = data - %s WHERE table_id = %s", (fid, tid))
        return
    lookup = Lookups(conn).for_field(new_field)
    fields = store.get_fields(conn, tid)
    target = next(f for f in fields if str(f["id"]) == fid)
    for rid, (raw, items, vt) in snapshot.items():
        v = convert(raw, items, vt, old_field["type"], new_type, opts, lookup)
        if v is None:
            conn.execute("UPDATE records SET data = data - %s WHERE id = %s", (fid, rid))
            continue
        store.ensure_choices(conn, target, v)
        conn.execute("UPDATE records SET data = jsonb_set(data, %s, %s) WHERE id = %s", ([fid], Jsonb(v), rid))
        if new_type == "link":
            store._sync_links(conn, fields, rid, tid, {}, {fid: v})


# ---------------------------------------------------------------- from API clients

def _items(val) -> list:
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        return [x.strip() for x in val.split(",") if x.strip()]
    return [val]


def from_api(f, val, lookups: "Lookups"):
    """A value as a person or API client would write it -> the stored value. Raises a 400 when it doesn't fit."""
    t, name = f["type"], f["name"]
    if val is None or (isinstance(val, str) and not val.strip()) or val == []:
        return [] if t in ("link", "multi_select", "user") else None
    if t == "link":
        titles = lookups.titles(f["options"].get("foreignTableId"))
        ids = set(titles.values())
        whole = str(val).strip().lower() if isinstance(val, str) else None
        if whole is not None and whole in titles:  # a title that contains commas
            return [titles[whole]]
        out = []
        for it in _items(val):
            it = it.get("id") if isinstance(it, dict) else it
            s = str(it).strip()
            if s in ids:
                out.append(s)
            elif s.lower() in titles:
                out.append(titles[s.lower()])
            else:
                bad(f"'{name}': no record called '{s}' in the linked table")
        return out
    if t == "user":
        users = lookups.users()
        out = []
        for it in _items(val):
            s = str(it.get("id") if isinstance(it, dict) else it).strip()
            if s in users.values():
                out.append(s)
            elif s.lower() in users:
                out.append(users[s.lower()])
            else:
                bad(f"'{name}': no user '{s}'")
        return out
    if t == "multi_select":
        return [str(x).strip() for x in _items(val) if str(x).strip()]
    if t == "single_select":
        return str(val).strip()
    if t == "checkbox":
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in ("true", "yes", "1", "on", "checked", "x", "✓")
    if t in ("number", "rating"):
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return val
        try:
            n = float(str(val).replace(",", "").strip())
        except ValueError:
            bad(f"'{name}' needs a number, not '{val}'")
        return int(n) if n.is_integer() else n
    if t == "date":
        v = from_cell(f, val if isinstance(val, str) else str(val), lookups)
        if v is None:
            bad(f"'{name}' needs a date like 2026-10-12 or 2026-10-12T15:30:00Z, not '{val}'")
        return v
    if t == "recurrence":
        try:
            return recur.normalize(str(val))
        except ValueError as e:
            bad(f"'{name}': {e}")
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    return str(val)
