"""Data access helpers shared by the API routes and the CalDAV sync engine."""
import os
from datetime import datetime, timezone

from fastapi import HTTPException

from . import formula as F
from .auth import api_bases
from .compute import COMPUTED_TYPES, FIELD_TYPES, FILTER_OPS, Engine
from .db import Jsonb
from . import audit, jobs, recur, webhooks
from .ical import DEFAULT_TZ, LOCAL_KEYS, PALETTE, SEMANTIC_KEY, SYSTEM_FIELDS, component_kind, ical_to_values, is_date_only, new_uid

SELECT_TYPES = ("single_select", "multi_select")
DAV_KINDS = ("tasks", "events", "mixed", "contacts")  # tables synced with a CalDAV calendar / CardDAV address book
AUTO_PUSH_DELAY = float(os.environ.get("AUTO_PUSH_DELAY", "2"))  # seconds after an edit before it is pushed; <0 disables
# A record's primary (title) field must hold plain text.
PRIMARY_TYPES = {"text", "long_text", "number", "url", "email", "single_select", "date", "autonumber", "formula"}


def not_found(what="Not found"):
    raise HTTPException(404, what)


def bad(msg):
    raise HTTPException(400, msg)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- tables / fields

def base_allowed(base_id) -> bool:
    """False when the request uses an API key limited to other bases."""
    allowed = api_bases.get()
    return allowed is None or str(base_id) in allowed


def get_base(conn, base_id, user_id):
    b = conn.execute("SELECT * FROM bases WHERE id = %s AND user_id = %s", (base_id, user_id)).fetchone()
    if not b or not base_allowed(b["id"]):
        not_found("Base not found")
    return b


def ensure_base(conn, user_id):
    """Every user has at least one base."""
    b = conn.execute("SELECT * FROM bases WHERE user_id = %s ORDER BY position, created_at LIMIT 1", (user_id,)).fetchone()
    return b or conn.execute("INSERT INTO bases (user_id, name) VALUES (%s, 'My Base') RETURNING *", (user_id,)).fetchone()


def get_table(conn, table_id, user_id=None, trashed=False):
    """Load a table; with user_id, only if that user owns it (and an API key may use its base).
    Tables in the trash count as gone, except with trashed=True (restoring them)."""
    t = conn.execute("SELECT * FROM tables WHERE id = %s AND (%s::uuid IS NULL OR user_id = %s) AND (trashed_at IS NULL OR %s)",
                     (table_id, user_id, user_id, trashed)).fetchone()
    if not t or (user_id and not base_allowed(t["base_id"])):
        not_found("Table not found")
    return t


def get_fields(conn, table_id):
    return conn.execute("SELECT * FROM fields WHERE table_id = %s ORDER BY position, name", (table_id,)).fetchall()


def get_views(conn, *, table_id=None, combo_id=None):
    col = "table_id" if table_id else "combo_id"
    return conn.execute(f"SELECT * FROM views WHERE {col} = %s ORDER BY position, name", (table_id or combo_id,)).fetchall()


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
    if type != "record_id":  # Record ID stays the last column
        conn.execute("UPDATE fields SET position = %s WHERE table_id = %s AND type = 'record_id'", (pos + 1, table_id))
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


def _add_record_id_field(conn, tid):
    """Every table has a Record ID column (the row's UUID). Views hide it until someone shows it."""
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
    choices = list(field["options"].get("choices", []))
    names = {c["name"] for c in choices}
    added = False
    for v in vals:
        if v not in (None, "") and str(v) not in names:
            choices.append({"name": str(v), "color": PALETTE[len(choices) % len(PALETTE)]})
            names.add(str(v))
            added = True
    if added:
        field["options"] = {**field["options"], "choices": choices}
        conn.execute("UPDATE fields SET options = %s WHERE id = %s", (Jsonb(field["options"]), field["id"]))


# ---------------------------------------------------------------- field validation

def _own_table_id(conn, table_id, user_id):
    if not table_id or not conn.execute("SELECT 1 FROM tables WHERE id = %s AND user_id = %s AND trashed_at IS NULL", (table_id, user_id)).fetchone():
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


def prepare_field(conn, table, ftype: str, options: dict, user_id, field=None) -> dict:
    """Validate and normalise options for a field of the given type."""
    if ftype not in FIELD_TYPES:
        bad(f"Unknown field type {ftype}")
    tid, o = str(table["id"]), dict(options or {})
    if field and field["is_primary"] and ftype not in PRIMARY_TYPES:
        bad("The primary field can't be that type")
    if table["kind"] == "remote":
        bad("A linked table's fields come from its source link")
    if ftype == "link":
        o["foreignTableId"] = _own_table_id(conn, o.get("foreignTableId"), user_id)
        if is_remote_table(conn, o["foreignTableId"]):
            o["symmetric"] = False  # a linked table's fields belong to its source: the link is one-way
        o["relationship"] = "one" if o.get("relationship") == "one" else "many"
        if field and field["type"] == "link":
            o["symmetricFieldId"] = field["options"].get("symmetricFieldId")
            if str(field["options"].get("foreignTableId")) != o["foreignTableId"]:
                bad("The linked table of an existing link field can't be changed; create a new field instead")
    elif ftype in ("lookup", "rollup"):
        link = _field_in(conn, o.get("linkFieldId"), tid, "link field")
        if link["type"] != "link":
            bad("Choose a link field")
        _field_in(conn, o.get("lookupFieldId"), link["options"]["foreignTableId"], "field to look up")
        if ftype == "rollup" and o.get("func") not in F.ROLLUPS:
            bad("Choose a rollup function")
    elif ftype in ("conditional_lookup", "conditional_rollup"):
        ftid = o["foreignTableId"] = _own_table_id(conn, o.get("foreignTableId"), user_id)
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
    elif ftype == "formula":
        expr = (o.get("expression") or "").strip()
        o = {"expression": compile_formula(conn, tid, expr, field["id"] if field else None),
             **({"precision": int(o["precision"])} if o.get("precision") not in (None, "") else {}),
             **({"includeTime": bool(o["includeTime"])} if "includeTime" in o else {})}
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
    return o


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


# ---------------------------------------------------------------- records

def clean_record(r):
    return {k: r[k] for k in ("id", "table_id", "data", "sync_status", "created_at", "updated_at")}


def normalize_data(conn, table, fields, data: dict, user_id=None, record_id=None) -> dict:
    """Drop unknown/computed keys, coerce link/user/attachment values, register new select choices."""
    by_id = {str(f["id"]): f for f in fields}
    out = {}
    for k, v in data.items():
        f = by_id.get(str(k))
        if not f or f["type"] in COMPUTED_TYPES:
            continue
        t = f["type"]
        if t == "link":
            ids = [str(x) for x in (v if isinstance(v, list) else [v] if v else [])]
            if ids:
                ftid = f["options"].get("foreignTableId")
                if is_remote_table(conn, ftid):  # records of a linked table live at its source
                    ok = set(Engine(conn).titles(ftid))
                else:
                    ok = {str(r["id"]) for r in conn.execute(
                        "SELECT id FROM records WHERE table_id = %s AND id = ANY(%s::uuid[]) AND NOT deleted",
                        (ftid, ids)).fetchall()}
                ids = list(dict.fromkeys(x for x in ids if x in ok))
            v = ids[:1] if f["options"].get("relationship") == "one" else ids
        elif t in ("number", "rating") and v is not None and not (isinstance(v, (int, float)) and not isinstance(v, bool)):
            try:
                v = float(str(v).replace(",", "").strip()) if str(v).strip() else None
                v = int(v) if v is not None and v.is_integer() else v
            except ValueError:
                bad(f"{f['name']} needs a number, not “{v}”")
        elif t == "checkbox":
            v = bool(v) and str(v).lower() not in ("false", "0", "no", "off")
        elif t == "recurrence":
            try:
                v = recur.normalize(v)
            except ValueError as e:
                bad(str(e))
        elif t == "user":
            ids = [str(x) for x in (v if isinstance(v, list) else [v] if v else [])]
            if ids:
                ok = {str(r["id"]) for r in conn.execute("SELECT id FROM users WHERE id = ANY(%s::uuid[])", (ids,)).fetchall()}
                ids = list(dict.fromkeys(x for x in ids if x in ok))
            v = ids if f["options"].get("multiple") else ids[:1]
        elif t == "attachment":
            items = [a for a in (v or []) if isinstance(a, dict) and a.get("id")]
            if items and user_id:
                rows = {str(r["id"]): r for r in conn.execute(
                    "SELECT id, name, mime, size FROM attachments WHERE user_id = %s AND id = ANY(%s::uuid[])",
                    (user_id, [str(a["id"]) for a in items])).fetchall()}
                v = [{"id": i, "name": rows[i]["name"], "mime": rows[i]["mime"], "size": rows[i]["size"]}
                     for i in dict.fromkeys(str(a["id"]) for a in items) if i in rows]
            elif items:
                continue  # attachments can only be set by a logged-in user
        if t in SELECT_TYPES and f["options"].get("allowNew") is False:
            known = {c["name"] for c in f["options"].get("choices", [])}
            for x in (v if isinstance(v, list) else [v]):
                if x not in (None, "") and str(x) not in known:
                    bad(f"“{x}” isn't one of the options of {f['name']}")
        ensure_choices(conn, f, v)
        out[str(k)] = v
    return out


# ---------------------------------------------------------------- field rules (field editor: required, unique, default)

RULE_TYPES = {"text", "long_text", "number", "rating", "email", "url", "phone", "date", "single_select", "multi_select",
              "checkbox", "user", "link", "attachment", "recurrence"}
UNIQUE_TYPES = {"text", "number", "email", "url", "phone", "date"}
DEFAULT_TYPES = {"text", "long_text", "number", "rating", "email", "url", "phone", "date", "single_select", "multi_select",
                 "checkbox", "user"}


def is_blank(v) -> bool:
    return v is None or v is False or (isinstance(v, (str, list, dict)) and not (v.strip() if isinstance(v, str) else v))


def field_rules(conn, f, *, required=None, unique=None, default=None, clear_default=False) -> dict:
    """Validate the rules set in the field editor; returns the column values to store."""
    t = f["type"]
    req = bool(required) if required is not None else f.get("required", False)
    uniq = bool(unique) if unique is not None else f.get("unique_values", False)
    dv = None if clear_default else (default if default is not None else f.get("default_value"))
    req = req and t in RULE_TYPES
    uniq = uniq and t in UNIQUE_TYPES
    if dv is not None and t not in DEFAULT_TYPES:
        dv = None
    if t == "date" and isinstance(dv, str) and dv not in ("today", "now") and not dv[:10].replace("-", "").isdigit():
        bad("A date's default is “today”, “now” or a date")
    if t in ("number", "rating") and dv is not None and not (isinstance(dv, (int, float)) and not isinstance(dv, bool)):
        bad("A number's default must be a number")
    if t == "multi_select" and dv is not None and not isinstance(dv, list):
        dv = [dv]
    if is_blank(dv) and t != "checkbox":
        dv = None
    if uniq and not f.get("unique_values") and f.get("id"):
        dup = conn.execute(
            "SELECT lower(data->>%s) AS v FROM records WHERE table_id = %s AND NOT deleted AND COALESCE(data->>%s, '') <> '' "
            "GROUP BY 1 HAVING count(*) > 1 LIMIT 1", (str(f["id"]), f["table_id"], str(f["id"]))).fetchone()
        if dup:
            bad(f"{f['name']} already has the value “{dup['v']}” more than once; make the values unique first")
    return {"required": req, "unique_values": uniq, "default_value": dv}


def _resolve_default(f, dv, user_id):
    if f["type"] == "date" and dv == "today":
        return datetime.now(DEFAULT_TZ).date().isoformat()
    if f["type"] == "date" and dv == "now":
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if f["type"] == "user":
        ids = dv if isinstance(dv, list) else [dv]
        return [str(user_id) if x == "me" else x for x in ids if x and (x != "me" or user_id)]
    return dv


def apply_defaults(fields, data: dict, user_id=None) -> dict:
    """A new record's empty fields get their default value."""
    out = dict(data)
    for f in fields:
        dv = f.get("default_value")
        if dv is not None and f["type"] not in COMPUTED_TYPES and (str(f["id"]) not in out or out[str(f["id"])] in (None, "", [])):
            out[str(f["id"])] = _resolve_default(f, dv, user_id)
    return out


def check_rules(conn, table_id, fields, data: dict, keys, record_id=None):
    """Required fields can't be left empty and unique fields can't repeat a value (for the fields in keys)."""
    for f in fields:
        fid = str(f["id"])
        if fid not in keys:
            continue
        v = data.get(fid)
        if f.get("required") and is_blank(v):
            bad(f"{f['name']} is required")
        if f.get("unique_values") and not is_blank(v):
            if isinstance(v, str):
                q = "lower(data->>%s) = lower(%s)"
            else:
                q, v = "data->%s = %s", Jsonb(v)
            if conn.execute(f"SELECT 1 FROM records WHERE table_id = %s AND NOT deleted AND id IS DISTINCT FROM %s AND {q} LIMIT 1",
                            (table_id, record_id, fid, v)).fetchone():
                bad(f"{f['name']} must be unique: another record already has “{data.get(fid)}”")


def apply_event_defaults(table, fields, data: dict, patch: dict):
    """For events: a date-only start implies an all-day event unless set explicitly."""
    if table["kind"] not in ("events", "mixed"):
        return
    keys = {f["system_key"]: str(f["id"]) for f in fields if f["system_key"]}
    if table["kind"] == "mixed" and data.get(keys["component"]) != "Event":
        return
    start_id, allday_id = keys.get("dtstart"), keys.get("all_day")
    if start_id in patch and allday_id not in patch and is_date_only(data.get(start_id)):
        data[allday_id] = True


def apply_task_auto(table, fields, old: dict, data: dict, patch: dict):
    """Fill Started At / Completed At when a task's status changes (unless set explicitly in the same edit)."""
    if table["kind"] not in ("tasks", "mixed"):
        return
    keys = {f["system_key"]: str(f["id"]) for f in fields if f["system_key"]}
    if "started" not in keys or "completed" not in keys:
        return
    if table["kind"] == "mixed" and data.get(keys["component"]) != "Task":
        return
    st, s_id, c_id = keys["status"], keys["started"], keys["completed"]
    before, after = old.get(st), data.get(st)
    if st not in patch or before == after:
        return
    if after == "Completed":
        if c_id not in patch:
            data[c_id] = now_iso()
    elif before == "Completed" and c_id not in patch:
        data[c_id] = None
    if after == "In Progress" and not data.get(s_id) and s_id not in patch:
        data[s_id] = now_iso()
    if after == "To Do" and s_id not in patch:
        data[s_id] = None


def _sync_links(conn, fields, record_id, table_id, old: dict, new: dict):
    """Keep two-way link fields consistent: mirror added/removed links into the other table."""
    for f in fields:
        sym = f["options"].get("symmetricFieldId") if f["type"] == "link" else None
        fid = str(f["id"])
        if not sym or fid not in new:
            continue
        before, after = set(old.get(fid) or []), set(new.get(fid) or [])
        ftid = f["options"]["foreignTableId"]
        sym_field = conn.execute("SELECT * FROM fields WHERE id = %s", (sym,)).fetchone()
        one = sym_field and sym_field["options"].get("relationship") == "one"
        for rid in after - before:
            row = conn.execute("SELECT data FROM records WHERE id = %s AND table_id = %s", (rid, ftid)).fetchone()
            if row:
                cur = [x for x in (row["data"].get(sym) or []) if x != str(record_id)]
                vals = [str(record_id)] if one else cur + [str(record_id)]
                conn.execute("UPDATE records SET data = jsonb_set(data, %s, %s), updated_at = now() WHERE id = %s",
                             ([sym], Jsonb(vals), rid))
                if one:  # the other record can only point at one record: drop its previous link here too
                    for prev in cur:
                        _remove_link(conn, prev, fid, rid)
        for rid in before - after:
            _remove_link(conn, rid, sym, record_id, ftid)


def _remove_link(conn, rid, field_id, target, table_id=None):
    row = conn.execute("SELECT data FROM records WHERE id = %s" + (" AND table_id = %s" if table_id else ""),
                       (rid, table_id) if table_id else (rid,)).fetchone()
    if row and str(target) in (row["data"].get(str(field_id)) or []):
        vals = [x for x in row["data"][str(field_id)] if x != str(target)]
        conn.execute("UPDATE records SET data = jsonb_set(data, %s, %s), updated_at = now() WHERE id = %s",
                     ([str(field_id)], Jsonb(vals), rid))


def is_synced(table) -> bool:
    return table["kind"] in DAV_KINDS and bool(table["account_id"]) and bool(table["calendar_url"])


def schedule_sync(table_id, delay: float | None = None, conn=None):
    """Sync a table soon (debounced), e.g. to push local edits shortly after they happen. With `conn`, the job
    is queued in that transaction, so it only exists once the edit is committed."""
    delay = AUTO_PUSH_DELAY if delay is None else delay
    if delay < 0:
        return
    jobs.enqueue(conn, "sync_table", {"table_id": str(table_id)}, key=f"sync:{table_id}", delay=delay, max_attempts=3)


def _changed(conn, table, event, record_id):
    """Every local record write ends here: tell webhooks, and push the change to the DAV server."""
    webhooks.emit(conn, table["id"], event, record_id)
    if is_synced(table):
        schedule_sync(table["id"], conn=conn)


def insert_record(conn, table_id, data, *, user_id=None, caldav_uid=None, caldav_href=None, caldav_raw=None, sync_status="synced"):
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (str(table_id),))  # serialise auto numbers
    return conn.execute(
        "INSERT INTO records (table_id, data, caldav_uid, caldav_href, caldav_raw, sync_status, seq, created_by, updated_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,(SELECT COALESCE(MAX(seq), 0) + 1 FROM records WHERE table_id = %s),%s,%s) RETURNING *",
        (table_id, Jsonb(data), caldav_uid, caldav_href, caldav_raw, sync_status, table_id, user_id, user_id),
    ).fetchone()


def create_record(conn, table_id, data: dict, user_id=None):
    table = get_table(conn, table_id, user_id)
    if table["kind"] == "remote":
        bad("Records of a linked table are written through its link")
    fields = get_fields(conn, table_id)
    data = normalize_data(conn, table, fields, apply_defaults(fields, data or {}, user_id), user_id)
    check_rules(conn, table_id, fields, data, {str(f["id"]) for f in fields})
    if table["kind"] in ("tasks", "mixed"):
        keys = {f["system_key"]: str(f["id"]) for f in fields if f["system_key"]}
        if table["kind"] == "mixed" and not data.get(keys["component"]):
            # Something placed on a day (calendar view) is an event; anything else starts as a task.
            data[keys["component"]] = "Event" if data.get(keys["dtstart"]) else "Task"
        is_task = table["kind"] == "tasks" or (table["kind"] == "mixed" and data[keys["component"]] == "Task")
        if is_task and not data.get(keys["status"]):
            data[keys["status"]] = "To Do"
        if keys.get("done") and data.get(keys["done"]) and _is_task(table, keys, data) and data.get(keys["status"]) != "Completed":
            data[keys["status"]] = "Completed"
        apply_task_auto(table, fields, {keys["status"]: "To Do"}, data, data)
        sync_done(table, keys, data)
    apply_event_defaults(table, fields, data, data)
    if table["kind"] != "generic":
        r = insert_record(conn, table_id, data, user_id=user_id, caldav_uid=new_uid(), sync_status="dirty")
    else:
        r = insert_record(conn, table_id, data, user_id=user_id)
    _sync_links(conn, fields, r["id"], table_id, {}, data)
    audit.note(conn, table_id, r["id"], "created")
    _changed(conn, table, "record.created", r["id"])
    return r, table


def update_record(conn, record_id, patch: dict, user_id=None):
    # FOR UPDATE: simultaneous edits of one record wait for each other instead of overwriting each other's fields
    r = conn.execute("SELECT * FROM records WHERE id = %s AND NOT deleted FOR UPDATE", (record_id,)).fetchone() or not_found("Record not found")
    table = get_table(conn, r["table_id"], user_id)
    fields = get_fields(conn, r["table_id"])
    patch = normalize_data(conn, table, fields, patch or {}, user_id, record_id)
    old = dict(r["data"])
    check_rules(conn, r["table_id"], fields, patch, {k for k, v in patch.items() if v != old.get(k)}, record_id)
    data = {**old, **patch}
    keys = {f["system_key"]: str(f["id"]) for f in fields if f["system_key"]}
    patch = apply_done(table, keys, old, data, patch)
    history = advance_recurring(table, keys, old, data, patch)
    apply_task_auto(table, fields, old, data, patch)
    apply_event_defaults(table, fields, data, patch)
    sync_done(table, keys, data)
    # Only edits to CalDAV-managed fields need pushing; local extra fields don't.
    caldav_ids = {str(f["id"]) for f in fields if f["system_key"] and f["system_key"] not in LOCAL_KEYS}
    changed = {k for k in data if data.get(k) != old.get(k)}
    status = r["sync_status"]
    if table["kind"] != "generic" and changed & caldav_ids:
        status = "dirty"
    r = conn.execute(
        "UPDATE records SET data = %s, sync_status = %s, updated_at = now(), updated_by = COALESCE(%s, updated_by) "
        "WHERE id = %s RETURNING *",
        (Jsonb(data), status, user_id, record_id),
    ).fetchone()
    _sync_links(conn, fields, record_id, r["table_id"], old, patch)
    audit.note(conn, r["table_id"], record_id, "updated", old, data, fields)
    if history is not None:
        # A repeating task moved on to its next date; keep the finished occurrence as a completed task.
        apply_task_auto(table, fields, old, history, patch)
        h = insert_record(conn, r["table_id"], history, user_id=user_id, caldav_uid=new_uid(), sync_status="dirty")
        _sync_links(conn, fields, h["id"], r["table_id"], {}, history)
        audit.note(conn, r["table_id"], h["id"], "created")
        _changed(conn, table, "record.created", h["id"])
    _changed(conn, table, "record.updated", record_id)
    return r, table


def _is_task(table, keys, data) -> bool:
    return table["kind"] == "tasks" or (table["kind"] == "mixed" and data.get(keys.get("component")) == "Task")


def apply_done(table, keys, old, data, patch):
    """The Completed checkbox drives a task's status (ticked = Completed, unticked = To Do).
    Returns the patch as if the status had been set, so the usual completion rules run. Events ignore it (it's calculated)."""
    d = keys.get("done")
    if not d or d not in patch:
        return patch
    if not _is_task(table, keys, data):
        data[d] = old.get(d)
        return {k: v for k, v in patch.items() if k != d}
    st = keys.get("status")
    if st and st not in patch:
        if data.get(d) and data.get(st) != "Completed":
            data[st] = "Completed"
            return {**patch, st: "Completed"}
        if not data.get(d) and data.get(st) == "Completed":
            data[st] = "To Do"
            return {**patch, st: "To Do"}
    return patch


def sync_done(table, keys, data):
    """Keep a task's Completed checkbox in line with its status."""
    if keys.get("done") and keys.get("status") and _is_task(table, keys, data):
        data[keys["done"]] = data.get(keys["status"]) == "Completed"


def advance_recurring(table, keys, old, data, patch):
    """Completing a repeating task moves it to its next date (status back to To Do), like Reminders does.

    Returns the data for a completed copy of the finished occurrence, or None if nothing repeats.
    """
    if table["kind"] not in ("tasks", "mixed") or "rrule" not in keys or "status" not in keys:
        return None
    if table["kind"] == "mixed" and data.get(keys["component"]) != "Task":
        return None
    st = keys["status"]
    if st not in patch or data.get(st) != "Completed" or old.get(st) == "Completed":
        return None
    rule = data.get(keys["rrule"])
    start_id, due_id = keys.get("dtstart"), keys.get("due") or keys.get("dtend")
    anchor = next((f for f in (start_id, due_id) if f and data.get(f)), None)
    if not rule or not anchor:
        return None
    nxt = recur.next_after(rule, data[anchor])
    if nxt is None:
        return None  # the series is over: this occurrence just completes
    delta = recur.delta_between(data[anchor], nxt)
    history = {**data, keys["rrule"]: None}
    for f in (start_id, due_id):
        if f and data.get(f):
            data[f] = recur.shift(data[f], delta)
    data[st] = "To Do"
    for k in ("started", "completed"):
        if k in keys:
            data[keys[k]] = None
    data[keys["rrule"]] = recur.used_one(rule)
    return history


def delete_record(conn, record_id, user_id=None):
    r = conn.execute("SELECT * FROM records WHERE id = %s AND NOT deleted", (record_id,)).fetchone() or not_found("Record not found")
    table = get_table(conn, r["table_id"], user_id)
    # a copy goes to the trash (30 days), so the record can be restored
    conn.execute(
        "INSERT INTO record_trash (id, table_id, data, seq, caldav_uid, caldav_raw, created_at, created_by, deleted_by) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, deleted_at = now()",
        (record_id, r["table_id"], Jsonb(r["data"]), r["seq"], r["caldav_uid"], r["caldav_raw"], r["created_at"], r["created_by"], user_id))
    # Remove this record from link fields pointing at it.
    for f in conn.execute("SELECT id FROM fields WHERE type = 'link' AND options->>'foreignTableId' = %s",
                          (str(r["table_id"]),)).fetchall():
        fid = str(f["id"])
        for row in conn.execute("SELECT id FROM records WHERE data -> %s @> %s", (fid, Jsonb([str(record_id)]))).fetchall():
            _remove_link(conn, row["id"], fid, record_id)
    if r["caldav_href"]:
        conn.execute("UPDATE records SET deleted = true, updated_at = now() WHERE id = %s", (record_id,))
    else:
        conn.execute("DELETE FROM records WHERE id = %s", (record_id,))
    audit.note(conn, r["table_id"], record_id, "deleted")
    _changed(conn, table, "record.deleted", record_id)
    return table


def restore_record(conn, record_id, user_id=None):
    """Bring a record back from the trash (with its links). A synced one goes back to the calendar / address book."""
    t = conn.execute("SELECT * FROM record_trash WHERE id = %s", (record_id,)).fetchone() or not_found("Not in the trash")
    table = get_table(conn, t["table_id"], user_id)
    fields = get_fields(conn, t["table_id"])
    live = conn.execute("SELECT * FROM records WHERE id = %s", (record_id,)).fetchone()
    if live:  # deleted here but not yet on the server: just un-delete it
        conn.execute("UPDATE records SET deleted = false, sync_status = 'dirty', updated_at = now() WHERE id = %s", (record_id,))
    else:
        known = {str(f["id"]) for f in fields}
        data = {k: v for k, v in t["data"].items() if k in known}
        # links only to records that still exist
        for f in fields:
            fid = str(f["id"])
            if f["type"] == "link" and isinstance(data.get(fid), list):
                ok = {str(x["id"]) for x in conn.execute("SELECT id FROM records WHERE id = ANY(%s::uuid[]) AND NOT deleted",
                                                         ([str(x) for x in data[fid]],)).fetchall()}
                data[fid] = [x for x in data[fid] if str(x) in ok]
        synced = table["kind"] in DAV_KINDS
        conn.execute(
            "INSERT INTO records (id, table_id, data, seq, caldav_uid, sync_status, created_at, created_by, updated_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (record_id, t["table_id"], Jsonb(data), t["seq"], t["caldav_uid"] if synced else None, "dirty" if synced else "synced",
             t["created_at"], t["created_by"], user_id))
        _sync_links(conn, fields, record_id, t["table_id"], {}, data)
    conn.execute("DELETE FROM record_trash WHERE id = %s", (record_id,))
    conn.execute("DELETE FROM record_tombstones WHERE record_id = %s", (record_id,))  # it's back: no longer "deleted"
    audit.note(conn, t["table_id"], record_id, "restored")
    _changed(conn, table, "record.created", record_id)
    return table


def list_records(conn, table_id, engine=None):
    eng = engine or Engine(conn)
    return [clean_record(r) for r in eng.records(table_id)]


def computed_record(conn, record_id, table_id):
    """One record with its computed fields filled in."""
    r = Engine(conn).record(table_id, record_id)
    return clean_record(r) if r else None


def delete_field_cleanup(conn, f):
    """Remove a field's stored values; for link fields also drop the counterpart field."""
    conn.execute("UPDATE records SET data = data - %s WHERE table_id = %s", (str(f["id"]), f["table_id"]))
    sym = f["options"].get("symmetricFieldId") if f["type"] == "link" else None
    if sym:
        s = conn.execute("SELECT * FROM fields WHERE id = %s", (sym,)).fetchone()
        if s and not s["is_primary"]:
            conn.execute("UPDATE records SET data = data - %s WHERE table_id = %s", (str(s["id"]), s["table_id"]))
            conn.execute("DELETE FROM fields WHERE id = %s", (s["id"],))


# ---------------------------------------------------------------- combos

def get_combo(conn, combo_id, user_id=None):
    c = conn.execute("SELECT * FROM combos WHERE id = %s AND (%s::uuid IS NULL OR user_id = %s)",
                     (combo_id, user_id, user_id)).fetchone()
    if not c or (user_id and not base_allowed(c["base_id"])):
        not_found("Combined database not found")
    return c


# What a field means, for lining up fields of different tables in a combined database. Synced fields know it
# (SEMANTIC_KEY); other fields get it from a common name, if their type fits that meaning.
NAME_MEANINGS = {
    "start": ({"start", "start date", "starts", "begin", "begins", "from", "start time"}, {"date"}),
    "end": ({"end", "end date", "ends", "due", "due date", "deadline", "until", "finish", "end time"}, {"date"}),
    "status": ({"status", "state", "stage"}, {"single_select"}),
    "description": ({"description", "notes", "note", "details", "comments"}, {"text", "long_text"}),
    "tags": ({"tags", "tag", "labels", "label", "categories", "category"}, {"multi_select", "single_select"}),
    "location": ({"location", "place", "where", "venue"}, {"text", "long_text"}),
    "priority": ({"priority"}, {"single_select"}),
    "email": ({"email", "e-mail", "mail"}, {"email", "text"}),
    "phone": ({"phone", "telephone", "mobile", "phone number"}, {"text"}),
    "company": ({"company", "organisation", "organization", "org"}, {"text"}),
}


def _semantic(f):
    if f["system_key"]:
        return SEMANTIC_KEY.get(f["system_key"])
    name = f["name"].strip().lower()
    return next((sem for sem, (names, types) in NAME_MEANINGS.items() if name in names and f["type"] in types), None)


def build_combo_fields(conn, table_ids: list[str]) -> list[dict]:
    """Auto-map fields of several tables onto one unified field list."""
    import uuid as _uuid

    unified: list[dict] = []
    for tid in table_ids:
        for f in get_fields(conn, tid):
            if f["type"] == "button":
                continue
            sem, lname = _semantic(f), f["name"].strip().lower()
            match = None
            for u in unified:
                if tid in u["mapping"]:
                    continue
                if f["is_primary"] and u["_primary"]:  # every table's title column lines up
                    match = u
                    break
                if (sem and u.get("_sem") == sem) or (not sem and not u.get("_sem") and u["_lname"] == lname and u["_type"] == f["type"]):
                    match = u
                    break
            if match:
                match["mapping"][tid] = str(f["id"])
                if f["name"] not in match["_names"]:
                    match["_names"].append(f["name"])
            else:
                unified.append({"id": str(_uuid.uuid4()), "mapping": {tid: str(f["id"])}, "_sem": sem,
                                "_lname": lname, "_type": f["type"], "_names": [f["name"]], "_primary": f["is_primary"]})
    # Primary (title) fields first, then fields shared by most sources.
    unified.sort(key=lambda u: (not u["_primary"], -len(u["mapping"])))
    return [{"id": u["id"], "name": " / ".join(u["_names"]), "mapping": u["mapping"]} for u in unified]


def resolve_combo(conn, combo, engine=None):
    """Expand a combo into its sources and derived unified fields (type/options from source fields)."""
    eng = engine or Engine(conn)
    sources, field_index = [], {}
    for s in combo["sources"]:
        t = conn.execute("SELECT id, name, kind FROM tables WHERE id = %s AND user_id IS NOT DISTINCT FROM %s AND trashed_at IS NULL",
                         (s["table_id"], combo["user_id"])).fetchone()
        if not t:
            continue
        sources.append({"table_id": str(t["id"]), "name": t["name"], "kind": t["kind"], "color": s.get("color")})
        for f in eng.describe(t["id"]):
            field_index[str(f["id"])] = f

    live = {s["table_id"] for s in sources}
    fields = [{
        "id": "__source", "name": "Source", "type": "single_select", "readonly": True, "computed": True,
        "options": {"choices": [{"name": s["name"], "color": s["color"]} for s in sources]}, "mapping": {},
    }, {"id": "__id", "name": "Record ID", "type": "record_id", "readonly": True, "computed": True, "options": {}, "mapping": {}}]
    for i, u in enumerate(combo["fields"]):
        mapping = {tid: fid for tid, fid in u["mapping"].items() if tid in live and fid in field_index}
        src = [field_index[fid] for fid in mapping.values()]
        ftype = src[0]["type"] if src else "text"
        options = dict(src[0]["options"]) if src else {}
        if ftype in SELECT_TYPES:
            seen, choices = set(), []
            for f in src:
                for c in f["options"].get("choices", []):
                    if c["name"] not in seen:
                        seen.add(c["name"])
                        choices.append(c)
            options["choices"] = choices
        if ftype == "date":
            options["includeTime"] = any(f["options"].get("includeTime") for f in src)
        computed = any(f["computed"] for f in src)
        fields.append({"id": u["id"], "name": u["name"], "type": ftype, "options": options, "mapping": mapping,
                       "is_primary": i == 0, "readonly": not mapping or computed, "computed": computed})
    return sources, fields


def _project(fields, src_name, tid, record):
    data = {"__source": src_name, "__id": str(record["id"])}
    for f in fields:
        fid = f["mapping"].get(tid)
        if fid is not None:
            data[f["id"]] = record["data"].get(fid)
    return {**clean_record(record), "data": data}


def combo_records(conn, combo, engine=None, only: set | None = None):
    """Records of a combined database, projected onto its fields. `only`: just these source tables."""
    eng = engine or Engine(conn)
    sources, fields = resolve_combo(conn, combo, eng)
    out = []
    for s in sources:
        if only is not None and str(s["table_id"]) not in only:
            continue
        for r in list_records(conn, s["table_id"], eng):
            out.append(_project(fields, s["name"], s["table_id"], r))
    out.sort(key=lambda r: r["created_at"])
    return out


def combo_refs(conn, combo, engine, only: set | None = None):
    refs = {"records": {}, "users": {}}
    for s in combo["sources"]:
        if only is not None and str(s["table_id"]) not in only:
            continue
        try:
            recs = engine.records(s["table_id"])
        except Exception:
            continue
        r = engine.refs(s["table_id"], recs)
        refs["records"].update(r["records"])
        refs["users"].update(r["users"])
    return refs


def project_record(conn, combo, record):
    """Project a single source record (computed) into the combo's unified shape."""
    sources, fields = resolve_combo(conn, combo)
    tid = str(record["table_id"])
    src = next((s for s in sources if s["table_id"] == tid), None)
    full = computed_record(conn, record["id"], tid) or clean_record(record)
    return _project(fields, src["name"] if src else None, tid, full)


def unified_to_source(conn, combo, table_id: str, data: dict) -> dict:
    _, fields = resolve_combo(conn, combo)
    out = {}
    for f in fields:
        if f["id"] in data and table_id in f["mapping"]:
            out[f["mapping"][table_id]] = data[f["id"]]
    return out
