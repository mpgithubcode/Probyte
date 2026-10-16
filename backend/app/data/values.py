"""Turning values into what a field stores, in one place:

- from_api(): values written by people and API clients (REST v1, MCP); strict, a bad value is a 400.
- from_cell(): spreadsheet/CSV cells being imported; lenient, an unreadable cell is skipped.
- convert() / convert_field(): existing values when a field changes type.

Lookups caches the title -> record id and username -> user id maps that link and user fields need.
"""
import json
import re

from fastapi import HTTPException

from app.calc import formula as F
from app.calc.compute import COMPUTED_TYPES, Engine
from app.core.db import Jsonb
from app.data import store
from app.formats import fieldformats, recur


def bad(msg: str, code: int = 400):
    raise HTTPException(code, msg)


class Lookups:
    def __init__(self, conn):
        self.conn = conn
        self._titles: dict = {}
        self._users: dict | None = None

    def link_index(self, table_id) -> tuple[set, dict]:
        """Every record of a linked table: (record ids, lower-cased title -> [record ids]). Titles are only labels:
        several records can share one, so a title maps to all of them and ids identify records."""
        key = str(table_id)
        if key not in self._titles:
            ids, by_name = set(), {}
            for rid, title in (Engine(self.conn).titles(table_id).items() if table_id else ()):
                ids.add(str(rid))
                by_name.setdefault(str(title).strip().lower(), []).append(str(rid))
            self._titles[key] = (ids, by_name)
        return self._titles[key]

    def titles(self, table_id) -> dict:
        """For imports and type changes: lower-cased title -> record id, for titles only one record has (a title
        shared by several records links none of them rather than a guess), and each record id -> itself."""
        ids, by_name = self.link_index(table_id)
        return {**{t: rs[0] for t, rs in by_name.items() if len(rs) == 1}, **{i: i for i in ids}}

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
NUMBER_IN_TEXT = re.compile(r"[^\d\-+.]{0,4}\s*([-+]?\d+(?:\.\d+)?|[-+]?\.\d+)\s*[^\d]{0,16}")


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
            # one number, perhaps with a currency sign or unit around it ("$1,200.50", "3 hours", "12%"); text with
            # several numbers in it ("2026-10-12", "555-0199") isn't a number, and is kept rather than half-read
            m = NUMBER_IN_TEXT.fullmatch(items[0].replace(",", "").strip())
            n = float(m.group(1)) if m else None
        if n is None:
            return None
        if new_type == "rating":
            return max(0, min(int(round(n)), int(opts.get("max") or 5))) or None
        return int(n) if float(n).is_integer() else n
    if new_type == "checkbox":
        if isinstance(raw, bool):
            return raw
        return bool(items) and items[0].strip().lower() not in ("false", "0", "no", "off", "unchecked")
    one_line = lambda x: " ".join(str(x).split())[:200]   # as select options are named (schema.clean_choices)
    if new_type == "single_select":
        return one_line(items[0]) or None if items else None
    if new_type == "multi_select":
        parts = items if len(items) != 1 else [p.strip() for p in items[0].split(",")]
        return list(dict.fromkeys(one_line(p) for p in parts if one_line(p))) or None
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


def _backups(conn, fid) -> dict:
    """Values of a field kept by earlier type changes: record id -> (raw, items, vt, old type)."""
    return {str(r["record_id"]): (r["raw"], r["items"] or [], r["vt"], r["old_type"]) for r in conn.execute(
        "SELECT record_id, raw, items, vt, old_type FROM field_value_backups WHERE field_id = %s", (fid,)).fetchall()}


def _blank(ftype, raw, items) -> bool:
    """Nothing to keep: no value (an unchecked box counts as none)."""
    if ftype == "checkbox":
        return not raw
    return not items and (raw is None or raw == "" or raw == [])


def plan_conversion(conn, old_field, new_type, opts, snapshot: dict) -> dict:
    """What a type change does to each record's value, without changing anything:
    {"values": {record id: new value or None}, "kept": {record id: (raw, items, vt, old type)} (values that don't fit
    the new type, kept to come back later), "restored": [record ids whose value an earlier change kept comes back]}."""
    fid = str(old_field["id"])
    lookup = Lookups(conn).for_field({"type": new_type, "options": opts})
    earlier = _backups(conn, fid)
    out, kept, restored = {}, {}, []
    for rid, (raw, items, vt) in snapshot.items():
        if new_type in COMPUTED_TYPES or new_type == "attachment":
            v = None
        else:
            v = convert(raw, items, vt, old_field["type"], new_type, opts, lookup)
        if (v is None or _blank(new_type, v, [])) and rid in earlier and not items:
            # empty now, but an earlier change kept its value: convert that one
            braw, bitems, bvt, btype = earlier[rid]
            v = None if new_type in COMPUTED_TYPES or new_type == "attachment" else (
                braw if btype == new_type else convert(braw, bitems, bvt, btype, new_type, opts, lookup))
            if v is not None:
                restored.append(rid)
        elif v is None and old_field["type"] not in COMPUTED_TYPES and not _blank(old_field["type"], raw, items):
            kept[rid] = (raw, items, vt, old_field["type"])
        out[rid] = v
    return {"values": out, "kept": kept, "restored": restored}


def convert_field(conn, old_field, new_field, snapshot: dict) -> dict:
    """Rewrite stored values of a field whose type changed, converting instead of discarding them. A value that
    doesn't fit the new type is kept aside (field_value_backups) and comes back when the field changes to a type it
    fits again; each record's history notes the change. Returns {"converted", "kept", "restored"} counts."""
    from app.data import audit
    new_type, opts = new_field["type"], new_field["options"]
    fid, tid = str(new_field["id"]), str(new_field["table_id"])
    plan = plan_conversion(conn, old_field, new_type, opts, snapshot)
    fields = store.get_fields(conn, tid)
    target = next(f for f in fields if str(f["id"]) == fid)
    old_data = {str(r["id"]): r["data"].get(fid) for r in conn.execute(
        "SELECT id, data FROM records WHERE table_id = %s AND data ? %s", (tid, fid)).fetchall()}
    sets, clears = [], []
    for rid, v in plan["values"].items():
        if v is None:
            if rid in old_data:
                clears.append((fid, rid))
            continue
        store.ensure_choices(conn, target, v)
        sets.append(([fid], Jsonb(v), rid))
    with conn.cursor() as cur:
        if clears:
            cur.executemany("UPDATE records SET data = data - %s WHERE id = %s", clears)
        if sets:
            cur.executemany("UPDATE records SET data = jsonb_set(data, %s, %s) WHERE id = %s", sets)
        if plan["kept"]:
            cur.executemany(
                "INSERT INTO field_value_backups (field_id, record_id, old_type, raw, items, vt) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (field_id, record_id) DO UPDATE SET old_type = EXCLUDED.old_type, raw = EXCLUDED.raw, "
                "items = EXCLUDED.items, vt = EXCLUDED.vt, created_at = now()",
                [(fid, rid, t, Jsonb(raw), Jsonb(items), vt) for rid, (raw, items, vt, t) in plan["kept"].items()])
        if plan["restored"]:
            cur.executemany("DELETE FROM field_value_backups WHERE field_id = %s AND record_id = %s",
                            [(fid, rid) for rid in plan["restored"]])
    if new_type == "link":
        for _, v, rid in sets:
            store._sync_links(conn, fields, rid, tid, {}, {fid: v.obj})
    for rid, v in plan["values"].items():
        before = old_data.get(rid)
        if before != v and not (before in (None, "", []) and v in (None, "", [])):
            audit.note(conn, tid, rid, "updated", {fid: before}, {fid: v})
    return {"converted": len(sets), "kept": len(plan["kept"]), "restored": len(plan["restored"])}


# ---------------------------------------------------------------- from API clients

def _items(val) -> list:
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        return [x.strip() for x in val.split(",") if x.strip()]
    return [val]


def _option(name, x):
    """A select option as an API client may write it: its name, or {"name": …}; anything else is a 400."""
    if isinstance(x, dict) and "name" in x:
        x = x["name"]
    if x is None or isinstance(x, (bool, dict, list)):
        if x is None:
            return None
        bad(f"'{name}': an option is written as its name, e.g. \"Done\"")
    return " ".join(str(x).split())[:200] or None


def from_api(f, val, lookups: "Lookups"):
    """A value as a person or API client would write it -> the stored value. Raises a 400 when it doesn't fit."""
    t, name = f["type"], f["name"]
    if val is None or (isinstance(val, str) and not val.strip()) or val == []:
        return [] if t in ("link", "multi_select", "user") else None
    if t == "link":
        # record ids first (they identify records); a title only when exactly one record has it
        ids, by_name = lookups.link_index(f["options"].get("foreignTableId"))

        def by_title(s: str) -> str:
            found = by_name.get(s.lower(), [])
            if len(found) > 1:
                bad(f"'{name}': {len(found)} records in the linked table are called '{s}': supply a record ID to say which")
            if not found:
                bad(f"'{name}': no record with the ID or title '{s}' in the linked table")
            return found[0]
        whole = str(val).strip() if isinstance(val, str) else None
        if whole is not None and whole not in ids and "," in whole and whole.lower() in by_name:  # a title that contains commas
            return [by_title(whole)]
        out = []
        for it in _items(val):
            it = it.get("id") if isinstance(it, dict) else it
            s = str(it).strip()
            rid = s if s in ids else by_title(s)
            if rid not in out:
                out.append(rid)
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
        return [n for n in (_option(name, x) for x in _items(val)) if n]
    if t == "single_select":
        if isinstance(val, list):
            if len(val) > 1:
                bad(f"'{name}' takes one option, not {len(val)}")
            val = val[0] if val else None
        return _option(name, val)
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
