"""Bases, tables, fields and views: creating them, checking a field's settings (formulas, links, lookups…), the
synced fields of calendar and contacts tables, and what kind of table is where. Also the errors every part of the
store raises."""
import json
import re
from datetime import datetime, timezone

from fastapi import HTTPException

from app.calc import formula as F
from app.calc.compute import COMPUTED_TYPES, Engine, FIELD_TYPES, FILTER_OPS
from app.core.context import api_bases
from app.data import access, pages
from app.core.db import Jsonb
from app.formats.ical import component_kind, ical_to_values, PALETTE, SYSTEM_FIELDS


SELECT_TYPES = ("single_select", "multi_select")
DAV_KINDS = ("tasks", "events", "mixed", "contacts")  # tables that are a calendar / address book (CalDAV, CardDAV)


# A record's primary (title) field must hold plain text.
PRIMARY_TYPES = {"text", "long_text", "number", "url", "email", "phone", "single_select", "date", "autonumber", "formula"}


def not_found(what="Not found"):
    raise HTTPException(404, what)


def bad(msg):
    raise HTTPException(400, msg)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def base_allowed(base_id) -> bool:
    """False when the request uses an API key limited to other bases."""
    allowed = api_bases.get()
    return allowed is None or str(base_id) in allowed


def get_base(conn, base_id, user_id, need=None):
    """A base the user owns or was given access to (at least `need`: see data/access.py), with their "role"."""
    b = conn.execute("SELECT * FROM bases WHERE id = %s", (base_id,)).fetchone()
    if not b or not base_allowed(b["id"]):
        not_found("Base not found")
    return {**b, "role": access.check(conn, b["id"], user_id, need, "Base")}


def ensure_base(conn, user_id):
    """Every user has at least one base."""
    b = conn.execute("SELECT * FROM bases WHERE user_id = %s ORDER BY position, created_at LIMIT 1", (user_id,)).fetchone()
    return b or conn.execute("INSERT INTO bases (user_id, name) VALUES (%s, 'My Base') RETURNING *", (user_id,)).fetchone()


def get_table(conn, table_id, user_id=None, trashed=False, need=None):
    """Load a table; with user_id, only if that user may (its owner, or a member of its base with at least `need`,
    which by default follows the request: reading or changing; see data/access.py), and an API key may use its base.
    Tables in the trash count as gone, except with trashed=True (restoring them)."""
    t = conn.execute("SELECT * FROM tables WHERE id = %s AND (trashed_at IS NULL OR %s)", (table_id, trashed)).fetchone()
    if not t or (user_id and not base_allowed(t["base_id"])):
        not_found("Table not found")
    if user_id:
        access.check(conn, t["base_id"], user_id, need)
    return t


def get_fields(conn, table_id):
    return conn.execute("SELECT * FROM fields WHERE table_id = %s ORDER BY position, name", (table_id,)).fetchall()


def get_views(conn, *, table_id=None, combo_id=None, user_id=None):
    """A table's (or combined database's) views as a user sees them: the base's views, with the member's own settings
    for them where they have them (data/access.py: always when they keep their own copies, else only until the view
    is changed for everyone), and their own views. Without a user: the base's views as they are."""
    col = "table_id" if table_id else "combo_id"
    rows = conn.execute(f"SELECT * FROM views WHERE {col} = %s AND (user_id IS NULL OR user_id = %s) ORDER BY position, name",
                        (table_id or combo_id, user_id)).fetchall()
    if not user_id:
        return rows
    base = conn.execute(f"SELECT base_id FROM {'tables' if table_id else 'combos'} WHERE id = %s", (table_id or combo_id,)).fetchone()
    m = access.membership(conn, base["base_id"], user_id) if base else None
    if not m or m["role"] == "owner":
        return rows
    shared = [v["id"] for v in rows if v["user_id"] is None]
    if not m["follow_views"]:  # their own copies: one is made of a view the first time they see it
        for vid in shared:
            conn.execute("INSERT INTO view_overrides (view_id, user_id, config) SELECT id, %s, config FROM views WHERE id = %s "
                         "ON CONFLICT DO NOTHING", (user_id, vid))
    ov = {r["view_id"]: r for r in conn.execute("SELECT * FROM view_overrides WHERE user_id = %s AND view_id = ANY(%s)",
                                                 (user_id, shared)).fetchall()}
    out = []
    for v in rows:
        o = ov.get(v["id"])
        if o and (not m["follow_views"] or o["updated_at"] > v["updated_at"]):
            v = {**v, "config": o["config"], "own_settings": True}
        out.append(v)
    return out


def view_edit_target(conn, view, user_id) -> str:
    """Where a user's change to a view goes: "view" (the view itself: theirs, or the base's for its owner and for
    editors and admins following the base's views), or "own" (their own settings for the base's view)."""
    if view["user_id"]:
        return "view"
    base = conn.execute("SELECT COALESCE(t.base_id, c.base_id) AS b FROM views v LEFT JOIN tables t ON t.id = v.table_id "
                        "LEFT JOIN combos c ON c.id = v.combo_id WHERE v.id = %s", (view["id"],)).fetchone()["b"]
    m = access.membership(conn, base, user_id)
    if m and (m["role"] == "owner" or (access.RANK[m["role"]] >= access.RANK["editor"] and m["follow_views"])):
        return "view"
    return "own"


def create_view(conn, *, name, type, config=None, table_id=None, combo_id=None):
    pos = len(get_views(conn, table_id=table_id, combo_id=combo_id))
    return conn.execute(
        "INSERT INTO views (table_id, combo_id, name, type, config, position) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
        (table_id, combo_id, name, type, Jsonb(config or {}), pos),
    ).fetchone()


def _name_taken(conn, table_id, name, exclude=None) -> bool:
    return bool(conn.execute("SELECT 1 FROM fields WHERE table_id = %s AND lower(name) = lower(%s) AND id IS DISTINCT FROM %s",
                             (table_id, name, exclude)).fetchone())


def check_field_name(conn, table_id, name, exclude=None) -> str:
    """A name a person chose for a field: not empty, and not the name of another field in the table (fields are
    addressed by name in the API, formulas and imports)."""
    name = (name or "").strip()
    if not name:
        bad("Give the field a name")
    if _name_taken(conn, table_id, name, exclude):
        bad(f"There's already a field called “{name}” in this table")
    return name


def unique_field_name(conn, table_id, name) -> str:
    """A name made up by Probyte (a link's counterpart field): numbered when it's taken."""
    out, n = name, 2
    while _name_taken(conn, table_id, out):
        out, n = f"{name} {n}", n + 1
    return out


def create_field(conn, table_id, name, type, options=None, system_key=None, is_primary=False):
    if type not in FIELD_TYPES:
        bad(f"Unknown field type {type}")
    if not system_key:
        name = check_field_name(conn, table_id, name)
    pos = conn.execute("SELECT COALESCE(MAX(position) + 1, 0) AS n FROM fields WHERE table_id = %s", (table_id,)).fetchone()["n"]
    f = conn.execute(
        "INSERT INTO fields (table_id, name, type, options, system_key, is_primary, position) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (table_id, name, type, Jsonb(options or {}), system_key, is_primary, pos),
    ).fetchone()
    if system_key not in TAIL_KEYS:  # the built-in fields, then Record ID, stay the last columns
        for n, key in enumerate(TAIL_KEYS):
            conn.execute("UPDATE fields SET position = %s WHERE table_id = %s AND system_key = %s", (pos + 1 + n, table_id, key))
    return f


def create_table(conn, user_id, base_id, name, kind="generic", account_id=None, calendar_url=None):
    t = conn.execute(
        "INSERT INTO tables (user_id, base_id, name, kind, account_id, calendar_url) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
        (user_id, base_id, name, kind, account_id, calendar_url),
    ).fetchone()
    tid = t["id"]
    if kind == "generic":
        create_field(conn, tid, "Name", "text", is_primary=True)
        create_field(conn, tid, "Notes", "long_text")
        create_field(conn, tid, "Status", "single_select",
                     {"choices": [{"name": n, "color": PALETTE[i]} for i, n in enumerate(["Todo", "Doing", "Done"])]})
        create_view(conn, table_id=tid, name="Table", type="grid")
    else:
        by_key = {}
        for key, fname, ftype, opts, primary in SYSTEM_FIELDS[kind]:
            by_key[key] = _create_system_field(conn, tid, key, fname, ftype, opts, primary)
        create_view(conn, table_id=tid, name="Table", type="grid",
                    config={"sort": {"field": str(by_key["fn"]["id"]), "dir": "asc"}} if kind == "contacts" else None)
        if kind == "contacts":
            create_view(conn, table_id=tid, name="Cards", type="gallery")
            create_view(conn, table_id=tid, name="A–Z", type="list", config={"sort": {"field": str(by_key["fn"]["id"]), "dir": "asc"}})
        elif kind == "tasks":
            create_view(conn, table_id=tid, name="Board", type="kanban", config={"groupBy": str(by_key["status"]["id"])})
            create_view(conn, table_id=tid, name="Calendar", type="calendar",
                        config={"dateField": str(by_key["due"]["id"]), "colorField": str(by_key["status"]["id"])})
        elif kind == "files":
            conn.execute("UPDATE views SET config = %s WHERE table_id = %s",
                         (Jsonb({"hidden": [str(by_key["text"]["id"]), str(by_key["mime"]["id"])]}), tid))
            create_view(conn, table_id=tid, name="Gallery", type="gallery",
                        config={"coverField": str(by_key["file"]["id"]), "hidden": [str(by_key["text"]["id"]), str(by_key["mime"]["id"])]})
            create_view(conn, table_id=tid, name="By kind", type="kanban", config={"groupBy": str(by_key["kind"]["id"])})
        elif kind == "mixed":
            create_view(conn, table_id=tid, name="Calendar", type="calendar",
                        config={"dateField": str(by_key["dtstart"]["id"]), "endField": str(by_key["dtend"]["id"]),
                                "colorField": str(by_key["component"]["id"])})
            create_view(conn, table_id=tid, name="Board", type="kanban", config={"groupBy": str(by_key["status"]["id"])})
        else:
            create_view(conn, table_id=tid, name="Calendar", type="calendar",
                        config={"dateField": str(by_key["dtstart"]["id"]), "endField": str(by_key["dtend"]["id"])})
    _add_record_id_field(conn, tid)
    return t


def _create_system_field(conn, tid, key, fname, ftype, opts, primary):
    if ftype == "link":  # Parent Task / Subtasks link the table to itself
        opts = {**opts, "foreignTableId": str(tid)}
    return create_field(conn, tid, fname, ftype, opts, system_key=key, is_primary=primary)


# Every table also says when and by whom each record was made and last changed. They can't be deleted or changed, and
# views hide them until someone shows them (like Record ID). (system key, name, type)
BUILTIN_FIELDS = [("created_time", "Created time", "created_time"), ("modified_time", "Last modified time", "last_modified_time"),
                  ("created_by", "Created by", "created_by"), ("modified_by", "Last modified by", "last_modified_by")]


TAIL_KEYS = [k for k, _, _ in BUILTIN_FIELDS] + ["record_id"]


def add_builtin_fields(conn, tid):
    """The built-in fields a table doesn't have yet (numbered names if the table has its own fields called that)."""
    have = {r["system_key"] for r in conn.execute("SELECT system_key FROM fields WHERE table_id = %s AND system_key IS NOT NULL", (tid,)).fetchall()}
    for key, name, ftype in BUILTIN_FIELDS:
        if key not in have:
            create_field(conn, tid, unique_field_name(conn, tid, name), ftype, {}, system_key=key)


def _add_record_id_field(conn, tid):
    """Every table has a Record ID column (the row's UUID), and the built-in fields. Views hide them until someone
    shows them."""
    add_builtin_fields(conn, tid)
    return create_field(conn, tid, "Record ID", "record_id", {}, system_key="record_id")


def _place_after(conn, table_id, field, after):
    """Move a field to just after another one in the table's field order."""
    order = [f for f in get_fields(conn, table_id) if f["id"] != field["id"]]
    i = next((n for n, f in enumerate(order) if f["id"] == after["id"]), len(order) - 1)
    order.insert(i + 1, field)
    for n, f in enumerate(order):
        conn.execute("UPDATE fields SET position = %s WHERE id = %s", (n, f["id"]))


def ensure_system_fields(conn):
    """Add synced fields defined in code (ical.SYSTEM_FIELDS) that a table doesn't have yet. Runs at startup."""
    for t in conn.execute("SELECT id, kind FROM tables WHERE kind IN ('tasks', 'events', 'mixed', 'contacts')").fetchall():
        by_key = {f["system_key"]: f for f in get_fields(conn, t["id"]) if f["system_key"]}
        added = set()
        for key, fname, ftype, opts, primary in SYSTEM_FIELDS[t["kind"]]:
            if key not in by_key:
                by_key[key] = _create_system_field(conn, t["id"], key, fname, ftype, opts, primary)
                added.add(key)
        if "alarm" in added:
            _backfill_reminders(conn, t, by_key["alarm"])
        if added:
            if "done" in by_key and by_key.get("summary"):
                _place_after(conn, t["id"], by_key["done"], by_key["summary"])
                if by_key.get("status"):
                    d, st = str(by_key["done"]["id"]), str(by_key["status"]["id"])
                    conn.execute("UPDATE records SET data = jsonb_set(data, %s, to_jsonb(COALESCE(data->>%s, '') = 'Completed')) "
                                 "WHERE table_id = %s AND NOT data ? %s", ([d], st, t["id"], d))


def _backfill_reminders(conn, table, field):
    """A newly added Reminder field starts with the alerts the items already have (set on a phone), so the next
    edit here doesn't remove them."""
    fid = str(field["id"])
    for r in conn.execute("SELECT id, caldav_raw FROM records WHERE table_id = %s AND caldav_raw LIKE %s",
                          (table["id"], "%BEGIN:VALARM%")).fetchall():
        try:
            kind = component_kind(r["caldav_raw"]) if table["kind"] == "mixed" else table["kind"]
            _, vals = ical_to_values(kind, r["caldav_raw"])
        except Exception:
            continue
        if vals.get("alarm"):
            ensure_choices(conn, field, vals["alarm"])
            conn.execute("UPDATE records SET data = jsonb_set(data, %s, to_jsonb(%s::text)) WHERE id = %s", ([fid], vals["alarm"], r["id"]))


def rename_choices(conn, field, renames: dict):
    """Select options renamed in the field editor: records holding an old name get the new one."""
    fid, tid = str(field["id"]), str(field["table_id"])
    for old, new in renames.items():
        if not isinstance(old, str) or not isinstance(new, str) or old == new or not new.strip():
            continue
        if field["type"] == "single_select":
            conn.execute("UPDATE records SET data = jsonb_set(data, %s, to_jsonb(%s::text)) WHERE table_id = %s AND data->>%s = %s",
                         ([fid], new, tid, fid, old))
        else:
            conn.execute(
                "UPDATE records SET data = jsonb_set(data, %s, (SELECT COALESCE(jsonb_agg(DISTINCT CASE WHEN x = %s THEN %s ELSE x END), '[]')"
                " FROM jsonb_array_elements_text(data->%s) x)) WHERE table_id = %s AND jsonb_typeof(data->%s) = 'array' AND data->%s ? %s",
                ([fid], old, new, fid, tid, fid, fid, old))


def ensure_choices(conn, field, values):
    """Add unseen select values to a field's choices."""
    if field["type"] not in SELECT_TYPES:
        return
    vals = values if isinstance(values, list) else [values]
    # (choices written as plain names, as the API accepts them, or left malformed, are kept as {name, color} from here on)
    raw = field["options"].get("choices") or []
    try:
        choices = clean_choices(raw)
    except HTTPException:
        choices = []
    names = {c["name"] for c in choices}
    added = choices != raw
    for v in vals:
        if isinstance(v, str) and v and v not in names:
            choices.append({"name": str(v), "color": PALETTE[len(choices) % len(PALETTE)]})
            names.add(str(v))
            added = True
    if added:
        field["options"] = {**field["options"], "choices": choices}
        conn.execute("UPDATE fields SET options = %s WHERE id = %s", (Jsonb(field["options"]), field["id"]))


def _own_table_id(conn, table_id, owner_id):
    """A table a field may point at (links, lookups): one of the same owner's (in their bases, shared or not)."""
    if not table_id or not conn.execute("SELECT 1 FROM tables WHERE id = %s AND user_id = %s AND trashed_at IS NULL", (table_id, owner_id)).fetchone():
        bad("Choose one of your tables")
    return str(table_id)


def _field_in(conn, field_id, table_id, what="field"):
    f = conn.execute("SELECT * FROM fields WHERE id = %s AND table_id = %s", (field_id, table_id)).fetchone() if field_id else None
    if not f:
        bad(f"Choose a {what}")
    return f


def compile_formula(conn, table_id, expression: str, field_id=None) -> str:
    """{Field name} expression -> {field_id} expression, validated (syntax, references, cycles)."""
    fields = get_fields(conn, table_id)
    by_id = {str(f["id"]): f for f in fields}
    by_name = {f["name"]: f for f in fields}
    by_lower = {f["name"].lower(): f for f in fields}

    def to_id(ref):
        f = by_id.get(ref) or by_name.get(ref) or by_lower.get(ref.lower())
        if not f:
            raise F.FormulaError(f"There is no field called {{{ref}}}")
        if field_id and str(f["id"]) == str(field_id):
            raise F.FormulaError("A formula can't refer to itself")
        if f["type"] == "button":
            raise F.FormulaError(f"{{{f['name']}}} is a button and has no value")
        return str(f["id"])

    try:
        compiled = F.rewrite_refs(expression, to_id)
        ast = F.parse(compiled)
    except F.FormulaError as e:
        bad(str(e))

    # Cycle check through other formulas / conditional fields of this table.
    eng = Engine(conn)
    graph = {str(f["id"]): eng._deps(f) for f in fields if f["type"] in COMPUTED_TYPES}
    me = str(field_id) if field_id else "__new__"
    graph[me] = F.field_refs(ast)
    seen, stack = set(), list(graph[me])
    while stack:
        cur = stack.pop()
        if cur == me:
            bad("Circular reference: this formula ends up depending on itself")
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(graph.get(cur, ()))
    return compiled


# how a field's numbers and dates are shown (number, formula, rollup, lookup, autonumber, created / modified time…)
FORMAT_KEYS = ("numberFormat", "currency", "separators", "dateFormat", "timeFormat")


def prepare_field(conn, table, ftype: str, options: dict, user_id, field=None) -> dict:
    """Validate and normalise options for a field of the given type."""
    if ftype not in FIELD_TYPES:
        bad(f"Unknown field type {ftype}")
    tid, o = str(table["id"]), dict(options or {})
    if field and field["is_primary"] and ftype not in PRIMARY_TYPES:
        bad("The primary field can't be that type")
    if table["kind"] == "remote":
        bad("A linked table's fields come from its source link")
    if ftype != "date" or not (o.get("stampWhen") or "").strip():
        for k in ("stampWhen", "stampClear", "stampKind"):
            o.pop(k, None)
    if "views" in o:   # how it's shown in the table's cells and on the record (data/pages.py)
        o["views"] = pages.clean_views(o["views"])
        if o["views"] is None:
            o.pop("views")
    if ftype == "recurrence":
        # ordinary tables: the checkbox that completes it (False: none) and the dates it moves on to the next date
        mine = {str(f["id"]): f for f in get_fields(conn, tid)}
        if o.get("doneField") not in (None, False) and (mine.get(str(o["doneField"])) or {}).get("type") != "checkbox":
            bad("Choose a checkbox field of this table to complete the repeat")
        if o.get("dateFields") is not None:
            if not isinstance(o["dateFields"], list) or any((mine.get(str(d)) or {}).get("type") != "date" for d in o["dateFields"]):
                bad("Choose date fields of this table to move to the next date")
            o["dateFields"] = [str(d) for d in o["dateFields"]] or None
    if ftype == "link":
        o["foreignTableId"] = _own_table_id(conn, o.get("foreignTableId"), table["user_id"])
        # which records the picker offers: a view of the linked table, and / or conditions on its fields
        if o.get("pickView"):
            if not conn.execute("SELECT 1 FROM views WHERE id = %s AND table_id = %s", (str(o["pickView"]), o["foreignTableId"])).fetchone():
                bad("Choose a view of the linked table")
            o["pickView"] = str(o["pickView"])
        else:
            o.pop("pickView", None)
        pf = o.get("pickFilter")
        if pf and isinstance(pf, dict) and pf.get("items"):
            ftf = {str(f["id"]) for f in get_fields(conn, o["foreignTableId"])}
            def walk(g):
                for it in g.get("items") or []:
                    if isinstance(it, dict) and "items" in it:
                        walk(it)
                    elif str(it.get("field")) not in ftf:
                        bad("The conditions must use fields of the linked table")
            walk(pf)
        else:
            o.pop("pickFilter", None)
        if is_remote_table(conn, o["foreignTableId"]):
            o["symmetric"] = False  # a linked table's fields belong to its source: the link is one-way
        o["relationship"] = "one" if o.get("relationship") == "one" else "many"
        if field and field["type"] == "link":
            o["symmetricFieldId"] = field["options"].get("symmetricFieldId")
            if str(field["options"].get("foreignTableId")) != o["foreignTableId"]:
                bad("The linked table of an existing link field can't be changed; create a new field instead")
        # what the cell shows of each linked record: its title, or another of its fields (any: a formula, a lookup,
        # its files, shown as previews unless displayFiles is off)
        if o.get("displayFieldId"):
            _field_in(conn, o["displayFieldId"], o["foreignTableId"], "field to show")
            o["displayFieldId"] = str(o["displayFieldId"])
        else:
            o.pop("displayFieldId", None)
        o["displayFiles"] = o.get("displayFiles") is not False
        # how its chips look (fields of the linked records shown in them, a colour, a picture) and the picker's columns
        ftf = {str(x["id"]): x for x in get_fields(conn, o["foreignTableId"])}
        chip = o.get("chip") if isinstance(o.get("chip"), dict) else {}
        chip = {
            "style": chip.get("style") if chip.get("style") in CHIP_STYLES else "chip",
            "fields": [str(x) for x in (chip.get("fields") or []) if str(x) in ftf][:4],
            "colorField": str(chip["colorField"]) if chip.get("colorField") and ftf.get(str(chip["colorField"]), {}).get("type") == "single_select" else None,
            "imageField": str(chip["imageField"]) if chip.get("imageField") and ftf.get(str(chip["imageField"]), {}).get("type") in ("attachment", "lookup") else None,
        }
        o["chip"] = chip if chip != {"style": "chip", "fields": [], "colorField": None, "imageField": None} else None
        if o["chip"] is None:
            o.pop("chip")
        pick = [str(x) for x in (o.get("pickFields") or []) if str(x) in ftf][:6] if isinstance(o.get("pickFields"), list) else None
        if pick is None:
            o.pop("pickFields", None)
        else:
            o["pickFields"] = pick
    elif ftype in ("lookup", "rollup"):
        link = _field_in(conn, o.get("linkFieldId"), tid, "link field")
        if link["type"] != "link":
            bad("Choose a link field")
        _field_in(conn, o.get("lookupFieldId"), link["options"]["foreignTableId"], "field to look up")
        if ftype == "rollup" and o.get("func") not in F.ROLLUPS:
            bad("Choose a rollup function")
    elif ftype in ("conditional_lookup", "conditional_rollup"):
        ftid = o["foreignTableId"] = _own_table_id(conn, o.get("foreignTableId"), table["user_id"])
        _field_in(conn, o.get("lookupFieldId"), ftid, "field to look up")
        filters = []
        for c in o.get("filters") or []:
            _field_in(conn, c.get("field"), ftid, "filter field")
            if c.get("op") not in FILTER_OPS:
                bad("Unknown filter condition")
            entry = {"field": str(c["field"]), "op": c["op"]}
            if c.get("valueField"):
                _field_in(conn, c["valueField"], tid, "field of this table")
                entry["valueField"] = str(c["valueField"])
            else:
                entry["value"] = c.get("value")
            filters.append(entry)
        o["filters"] = filters
        if o.get("sortField"):
            _field_in(conn, o["sortField"], ftid, "sort field")
        o["limit"] = int(o["limit"]) if o.get("limit") not in (None, "", 0) else None
        if ftype == "conditional_rollup" and o.get("func") not in F.ROLLUPS:
            bad("Choose a rollup function")
    elif ftype == "date" and (o.get("stampWhen") or "").strip():
        # a date that fills itself in when its formula is true (Started date, Completed date…)
        o["stampWhen"] = compile_formula(conn, tid, o["stampWhen"].strip(), field["id"] if field else None)
        o["stampClear"] = bool(o.get("stampClear"))
        o["stampKind"] = o.get("stampKind") if o.get("stampKind") in ("started", "completed") else None
    elif ftype == "formula":
        expr = (o.get("expression") or "").strip()
        o = {"expression": compile_formula(conn, tid, expr, field["id"] if field else None),
             **({"precision": int(o["precision"])} if o.get("precision") not in (None, "") else {}),
             **({"includeTime": bool(o["includeTime"])} if "includeTime" in o else {}),
             **{k: o[k] for k in FORMAT_KEYS if o.get(k) not in (None, "")}}   # how its numbers / dates are shown
    elif ftype == "button":
        o["label"] = (o.get("label") or "Click").strip()[:40]
        o["action"] = "url" if o.get("action") == "url" else "update"
        if o["action"] == "update":
            ups = []
            for u in o.get("updates") or []:
                f = _field_in(conn, u.get("fieldId"), tid, "field to update")
                if f["type"] in COMPUTED_TYPES:
                    bad(f"{f['name']} is computed and can't be set by a button")
                ups.append({"fieldId": str(f["id"]), "value": u.get("value")})
            if not ups:
                bad("Add at least one field for the button to set")
            o["updates"] = ups
        elif not (o.get("url") or "").strip():
            bad("Enter the URL the button opens")
    elif ftype == "user":
        o["multiple"] = bool(o.get("multiple"))
    elif ftype in SELECT_TYPES:
        o["choices"] = clean_choices(o.get("choices"))
    if ftype not in SELECT_TYPES:
        o.pop("choices", None)   # options another type left behind
    # several values (data/multi.py): text, email, URL and phone fields; not the title (a record has one name)
    if o.get("multi") not in ("list", "labeled") or ftype not in ("text", "email", "url", "phone"):
        o.pop("multi", None)
    elif field and field["is_primary"]:
        bad("The title field holds one value; add another field for more")
    return o


MAX_CHOICE = 200
CHIP_STYLES = ("chip", "compact", "details", "card", "plain")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def clean_choices(choices) -> list[dict]:
    """A select field's options as {name, color}, whatever was sent (names alone, {"label": …}, other keys, repeats):
    names are text, one line, unique; a colour that isn't one is replaced. Anything else is refused."""
    if choices in (None, ""):
        return []
    if not isinstance(choices, list):
        bad("A select field's options (choices) must be a list, e.g. [\"Todo\", \"Done\"]")
    out, seen = [], set()
    for c in choices:
        if isinstance(c, dict):
            name, color = c.get("name", c.get("label", c.get("value"))), c.get("color")
        elif isinstance(c, (str, int, float)) and not isinstance(c, bool):
            name, color = c, None
        else:
            bad(f"Each option needs a name, not {json.dumps(c)[:60]}")
        if not isinstance(name, (str, int, float)) or isinstance(name, bool):
            bad(f"Each option needs a name, not {json.dumps(c)[:60]}")
        name = " ".join(str(name).split())[:MAX_CHOICE]
        if not name or name in seen:
            continue
        seen.add(name)
        if not isinstance(color, str) or not COLOR_RE.match(color):
            color = PALETTE[len(out) % len(PALETTE)]
        out.append({"name": name, "color": color})
    return out


def is_remote_table(conn, table_id) -> bool:
    return bool(conn.execute("SELECT 1 FROM tables WHERE id = %s AND kind = 'remote'", (table_id,)).fetchone())


def create_link_pair(conn, table, name, options, user_id):
    """Create a link field and (unless it links a table to itself, or to a linked table) its counterpart."""
    f = create_field(conn, table["id"], name, "link", options)
    ftid = options["foreignTableId"]
    if options.get("symmetric", True) and str(ftid) != str(table["id"]) and not is_remote_table(conn, ftid):
        sym = create_field(conn, ftid, unique_field_name(conn, ftid, table["name"]), "link",
                           {"foreignTableId": str(table["id"]), "relationship": "many", "symmetricFieldId": str(f["id"])})
        opts = {**f["options"], "symmetricFieldId": str(sym["id"])}
        opts.pop("symmetric", None)
        f = conn.execute("UPDATE fields SET options = %s WHERE id = %s RETURNING *", (Jsonb(opts), f["id"])).fetchone()
    return f


def is_synced(table) -> bool:
    """Linked to a collection on another DAV server (and synced with it)."""
    return table["kind"] in DAV_KINDS and bool(table.get("account_id")) and bool(table.get("calendar_url"))


def is_served(table) -> bool:
    """On Probyte's own CalDAV/CardDAV server (phones read and write it directly)."""
    return table["kind"] in DAV_KINDS and bool(table.get("dav_slug"))
