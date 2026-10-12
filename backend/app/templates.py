"""Templates: a table (its fields, views and optionally its records) or a single view, as a portable JSON document.

Made from any table or view (Table settings / View settings → Save as template, or Download template), uploaded
from a file someone shared, or one of the built-in starters (templates_builtin/*.json). A table template creates a
new table; a view template adds a view to any table or combined database, matched to its fields by name.

Inside a template, fields are referred to by name, written as {{field:Name}} wherever a field id would be (a view's
date field, hidden fields, filters, sorts, a button's targets…), so it means the same in another table. Formulas are
kept in their readable form ({Field name}). Links, lookups and rollups point at other tables, so table templates
leave them out (they're listed under "skipped").

    {"probyte_template": 1, "kind": "table", "name": "Job tracker", "description": "…", "icon": "🔧",
     "fields": [{"name": "Job", "type": "text", "primary": true}, {"name": "Status", "type": "single_select",
                 "options": {"choices": [{"name": "Booked", "color": "#3b82f6"}]}, "required": true}, …],
     "views": [{"name": "Board", "type": "kanban", "config": {"groupBy": "{{field:Status}}"}}, …],
     "records": [{"Job": "Panel upgrade", "Status": "Booked"}, …]}

    {"probyte_template": 1, "kind": "view", "name": "This week", "type": "calendar",
     "config": {"dateField": "{{field:Due}}", "filter": {…}}}
"""
import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel

from . import recordops, store
from .compute import COMPUTED_TYPES, FIELD_TYPES, Engine
from .db import Jsonb, connection
from .routes.common import User, _own_view
from .viewfilter import normalize_config

router = APIRouter()
VERSION = 1
BUILTIN_DIR = Path(__file__).parent / "templates_builtin"
MAX_BYTES = 5 * 1024 * 1024
MAX_RECORDS = 2000
SKIP_TYPES = {"link", "lookup", "rollup", "conditional_lookup", "conditional_rollup", "record_id"}  # point at other tables / built in
VIEW_TYPES = {"grid", "kanban", "timeline", "calendar", "list", "gallery", "chart"}
TOKEN = re.compile(r"^\{\{field:(.+)\}\}$")


def bad(msg: str):
    raise HTTPException(400, msg)


# ---------------------------------------------------------------- field ids <-> names

def tokenize(obj: Any, names: dict[str, str]) -> Any:
    """Field ids (as values or as keys) -> {{field:Name}}."""
    if isinstance(obj, dict):
        return {(f"{{{{field:{names[k]}}}}}" if k in names else k): tokenize(v, names) for k, v in obj.items()}
    if isinstance(obj, list):
        return [tokenize(x, names) for x in obj]
    if isinstance(obj, str) and obj in names:
        return f"{{{{field:{names[obj]}}}}}"
    return obj


_GONE = object()


def detokenize(obj: Any, ids: dict[str, str]) -> Any:
    """{{field:Name}} -> the field id in the target table (names compared without case); references to fields the
    table doesn't have are dropped: list items and dict entries go, a filter condition on such a field goes."""
    def name_of(s):
        m = TOKEN.match(s) if isinstance(s, str) else None
        return m.group(1) if m else None

    def walk(o):
        if isinstance(o, dict):
            if "field" in o and name_of(o["field"]) is not None and ids.get(name_of(o["field"]).lower()) is None:
                return _GONE  # a filter / sort / group / list property on a field that isn't there
            out = {}
            for k, v in o.items():
                n = name_of(k)
                if n is not None:
                    if ids.get(n.lower()) is None:
                        continue
                    k = ids[n.lower()]
                v2 = walk(v)
                if v2 is not _GONE:
                    out[k] = v2
            return out
        if isinstance(o, list):
            return [x for x in (walk(i) for i in o) if x is not _GONE]
        n = name_of(o)
        if n is not None:
            return ids.get(n.lower(), _GONE)
        return o

    out = walk(obj)
    return None if out is _GONE else out


# ---------------------------------------------------------------- making templates

def _clean_options(f: dict) -> dict:
    o = {k: v for k, v in (f.get("options") or {}).items() if not k.startswith("_") and k not in ("resultType", "symmetricFieldId")}
    if f["type"] == "formula":
        o["expression"] = (f.get("options") or {}).get("displayExpression") or o.get("expression") or ""
        o.pop("displayExpression", None)
    return o


def table_template(conn, table_id, user_id, *, include_records=False, name=None, description=None) -> dict:
    t = store.get_table(conn, table_id, user_id)
    if t["kind"] in ("remote", "files"):
        bad("Templates are made from your own tables (not linked or files tables)")
    eng = Engine(conn)
    fields = eng.describe(table_id)
    names = {str(f["id"]): f["name"] for f in fields}
    kept, skipped = [], []
    for f in fields:
        if f["type"] in SKIP_TYPES:
            if f["type"] != "record_id":
                skipped.append(f["name"])
            continue
        entry = {"name": f["name"], "type": f["type"], "options": tokenize(_clean_options(f), names)}
        if f.get("is_primary"):
            entry["primary"] = True
        for k_src, k_out in (("description", "description"), ("required", "required"), ("unique_values", "unique"), ("default_value", "default")):
            if f.get(k_src) not in (None, False, ""):
                entry[k_out] = f[k_src]
        if f.get("system_key") and f["system_key"] != "record_id":
            entry["note"] = "synced field (becomes a regular field)"
        kept.append(entry)
    keep_names = {e["name"] for e in kept}
    views = [{"name": v["name"], "type": v["type"], "config": tokenize(v["config"] or {}, names)} for v in store.get_views(conn, table_id=table_id)]
    out = {"probyte_template": VERSION, "kind": "table", "name": name or t["name"], "description": description or t.get("description") or "",
           "icon": t.get("icon") or None, "fields": kept, "views": views}
    if skipped:
        out["skipped"] = skipped
    if include_records:
        stored = {str(f["id"]): f["name"] for f in fields if f["name"] in keep_names and f["type"] not in COMPUTED_TYPES
                  and f["type"] not in ("attachment", "user", "button")}
        rows = []
        for r in store.list_records(conn, table_id, eng)[:MAX_RECORDS]:
            row = {stored[k]: v for k, v in r["data"].items() if k in stored and v not in (None, "", [], False)}
            if row:
                rows.append(row)
        out["records"] = rows
    return out


def view_template(conn, view_id, user, name=None) -> dict:
    v = _own_view(conn, view_id, user)
    if v["table_id"]:
        fields = store.get_fields(conn, v["table_id"])
    else:
        fields = store.resolve_combo(conn, store.get_combo(conn, v["combo_id"], user["id"]))[1]
    names = {str(f["id"]): f["name"] for f in fields}
    return {"probyte_template": VERSION, "kind": "view", "name": name or v["name"], "type": v["type"],
            "config": tokenize(v["config"] or {}, names)}


def validate(body: Any) -> dict:
    """An uploaded or saved template: the right shape, known types, sensible sizes."""
    if not isinstance(body, dict) or body.get("probyte_template") != VERSION:
        bad("That isn't a Probyte template file")
    kind = body.get("kind")
    if kind not in ("table", "view"):
        bad("A template is for a table or a view")
    if not isinstance(body.get("name"), str) or not body["name"].strip():
        bad("The template has no name")
    if kind == "view":
        if body.get("type") not in VIEW_TYPES:
            bad("Unknown kind of view in the template")
        if not isinstance(body.get("config", {}), dict):
            bad("The view's settings in the template are unreadable")
        return body
    fields = body.get("fields")
    if not isinstance(fields, list) or not fields or len(fields) > 500:
        bad("A table template needs its fields")
    seen = set()
    for f in fields:
        if not isinstance(f, dict) or not isinstance(f.get("name"), str) or not f["name"].strip():
            bad("A field in the template has no name")
        if f.get("type") not in FIELD_TYPES or f["type"] in SKIP_TYPES:
            bad(f"The field {f['name']} has a type that can't be used in a template")
        if f["name"].strip().lower() in seen:
            bad(f"The template has two fields called {f['name']}")
        seen.add(f["name"].strip().lower())
    for v in body.get("views") or []:
        if not isinstance(v, dict) or v.get("type") not in VIEW_TYPES:
            bad("A view in the template is unreadable")
    if not isinstance(body.get("records", []), list):
        bad("The template's records are unreadable")
    return body


def summary(body: dict) -> dict:
    if body["kind"] == "view":
        return {"type": body["type"]}
    return {"fields": len(body["fields"]), "views": len(body.get("views") or []), "records": len(body.get("records") or []),
            "field_names": [f["name"] for f in body["fields"]][:12]}


# ---------------------------------------------------------------- using templates

def create_table(conn, user_id, base_id, body: dict, name=None, include_records=True) -> dict:
    """A new table from a table template."""
    body = validate(body)
    if body["kind"] != "table":
        bad("That's a view template; use it from a table's “+” next to its views")
    t = store.create_table(conn, user_id, base_id, (name or body["name"]).strip()[:100], "generic")
    tid = str(t["id"])
    defaults = store.get_fields(conn, tid)
    primary = next(f for f in defaults if f["is_primary"])
    for f in defaults:  # a blank table's starter fields and view make way for the template's
        if not f["is_primary"] and f["type"] != "record_id":
            conn.execute("DELETE FROM fields WHERE id = %s", (f["id"],))
    conn.execute("DELETE FROM views WHERE table_id = %s", (tid,))
    if body.get("icon") or body.get("description"):
        conn.execute("UPDATE tables SET icon = %s, description = %s WHERE id = %s",
                     ((body.get("icon") or "")[:16] or None, (body.get("description") or "")[:2000] or None, tid))
    specs = body["fields"]
    first = next((f for f in specs if f.get("primary")), specs[0])
    ids: dict[str, str] = {}
    table = store.get_table(conn, tid)
    # fields that refer to other fields (formulas, buttons) come after the ones they refer to
    order = [first] + [f for f in specs if f is not first and f["type"] not in ("formula", "button")] \
        + [f for f in specs if f is not first and f["type"] == "formula"] + [f for f in specs if f is not first and f["type"] == "button"]
    for spec in order:
        ftype = spec["type"]
        if spec is first and ftype not in store.PRIMARY_TYPES:
            ftype = "text"
        opts = detokenize(spec.get("options") or {}, ids) or {}
        try:
            opts = store.prepare_field(conn, table, ftype, opts, user_id, field=primary if spec is first else None)
        except HTTPException as e:
            bad(f"The field {spec['name']}: {e.detail}")
        if spec is first:
            f = conn.execute("UPDATE fields SET name = %s, type = %s, options = %s WHERE id = %s RETURNING *",
                             (spec["name"].strip()[:100], ftype, Jsonb(opts), primary["id"])).fetchone()
        else:
            f = store.create_field(conn, tid, spec["name"].strip()[:100], ftype, opts)
        rules = store.field_rules(conn, {**f, "required": False, "unique_values": False, "default_value": None, "id": None},
                                  required=bool(spec.get("required")), unique=bool(spec.get("unique")), default=spec.get("default"))
        conn.execute("UPDATE fields SET description = %s, required = %s, unique_values = %s, default_value = %s WHERE id = %s",
                     ((spec.get("description") or "")[:1000] or None, rules["required"], rules["unique_values"],
                      Jsonb(rules["default_value"]) if rules["default_value"] is not None else None, f["id"]))
        ids[spec["name"].strip().lower()] = str(f["id"])
    # Record ID stays last
    pos = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 AS n FROM fields WHERE table_id = %s AND type <> 'record_id'", (tid,)).fetchone()["n"]
    conn.execute("UPDATE fields SET position = %s WHERE table_id = %s AND type = 'record_id'", (pos, tid))
    views = body.get("views") or [{"name": "Table", "type": "grid", "config": {}}]
    for v in views:
        cfg = normalize_config(v["type"], detokenize(v.get("config") or {}, ids) or {})
        store.create_view(conn, table_id=tid, name=(v.get("name") or "View").strip()[:100], type=v["type"], config=cfg)
    if include_records:
        for row in (body.get("records") or [])[:MAX_RECORDS]:
            data = {ids[k.lower()]: v for k, v in row.items() if isinstance(k, str) and k.lower() in ids}
            if data:
                try:
                    with conn.transaction():
                        recordops.create(conn, tid, data, user_id)
                except HTTPException:
                    pass  # a row the table's rules don't accept is left out
    return store.get_table(conn, tid)


def create_view(conn, user, body: dict, table_id=None, combo_id=None, name=None) -> dict:
    """A view from a view template, matched to this table's fields by name."""
    from .routes.views import _with_defaults
    body = validate(body)
    if body["kind"] != "view":
        bad("That's a table template; use it from New table")
    if table_id:
        store.get_table(conn, table_id, user["id"])
        fields = store.get_fields(conn, table_id)
    else:
        fields = store.resolve_combo(conn, store.get_combo(conn, combo_id, user["id"]))[1]
    ids = {f["name"].lower(): str(f["id"]) for f in fields}
    cfg = _with_defaults(body["type"], normalize_config(body["type"], detokenize(body.get("config") or {}, ids) or {}), fields)
    return store.create_view(conn, table_id=table_id, combo_id=combo_id, name=(name or body["name"]).strip()[:100], type=body["type"], config=cfg)


# ---------------------------------------------------------------- built-in and saved templates

def builtins() -> list[dict]:
    out = []
    for p in sorted(BUILTIN_DIR.glob("*.json")):
        try:
            body = validate(json.loads(p.read_text()))
        except (ValueError, HTTPException):
            continue
        out.append({"id": f"builtin:{p.stem}", "kind": body["kind"], "name": body["name"], "description": body.get("description") or "",
                    "icon": body.get("icon"), "builtin": True, "summary": summary(body), "body": body})
    return out


def find(conn, template_id: str, user_id) -> dict:
    if template_id.startswith("builtin:"):
        hit = next((b for b in builtins() if b["id"] == template_id), None)
        if not hit:
            store.not_found("Template not found")
        return hit["body"]
    row = conn.execute("SELECT body FROM templates WHERE id = %s AND user_id = %s", (template_id, user_id)).fetchone()
    return row["body"] if row else store.not_found("Template not found")


def _download(body: dict) -> Response:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", body["name"]).strip("-").lower() or "template"
    return Response(json.dumps(body, indent=2, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{slug}.probyte-{body["kind"]}.json"'})


class SaveIn(BaseModel):
    kind: str = "table"
    table_id: str | None = None
    view_id: str | None = None
    name: str | None = None
    description: str | None = None
    include_records: bool = False
    body: dict[str, Any] | None = None     # an uploaded template file's contents


class FromTemplateIn(BaseModel):
    template_id: str | None = None
    body: dict[str, Any] | None = None
    base_id: str | None = None
    table_id: str | None = None
    combo_id: str | None = None
    name: str | None = None
    include_records: bool = True


@router.get("/api/templates")
def list_templates(kind: str | None = None, user=User):
    with connection() as conn:
        rows = conn.execute("SELECT id, kind, name, description, body, created_at FROM templates WHERE user_id = %s ORDER BY created_at DESC",
                            (user["id"],)).fetchall()
    mine = [{"id": str(r["id"]), "kind": r["kind"], "name": r["name"], "description": r["description"] or "", "icon": r["body"].get("icon"),
             "builtin": False, "created_at": r["created_at"], "summary": summary(r["body"])} for r in rows]
    built = [{k: v for k, v in b.items() if k != "body"} for b in builtins()]
    return [t for t in mine + built if not kind or t["kind"] == kind]


def _make(conn, body: SaveIn, user) -> dict:
    if body.body is not None:
        tpl = validate(body.body)
    elif body.kind == "table" and body.table_id:
        tpl = table_template(conn, body.table_id, user["id"], include_records=body.include_records, name=body.name, description=body.description)
    elif body.kind == "view" and body.view_id:
        tpl = view_template(conn, body.view_id, user, name=body.name)
    else:
        bad("Say which table or view to make the template from")
    if body.name and body.body is not None:
        tpl["name"] = body.name.strip()[:100]
    if body.description is not None:
        tpl["description"] = body.description.strip()[:2000]
    return tpl


@router.post("/api/templates")
def save_template(body: SaveIn, user=User):
    """Save a template: from a table or view, or an uploaded template file's contents."""
    with connection() as conn:
        tpl = _make(conn, body, user)
        raw = json.dumps(tpl)
        if len(raw) > MAX_BYTES:
            bad("That template is too large (5 MB at most); leave out the records")
        r = conn.execute("INSERT INTO templates (user_id, kind, name, description, body) VALUES (%s,%s,%s,%s,%s) RETURNING id",
                         (user["id"], tpl["kind"], tpl["name"].strip()[:100], (tpl.get("description") or "")[:2000] or None, Jsonb(tpl))).fetchone()
    return {"id": str(r["id"]), "kind": tpl["kind"], "name": tpl["name"], "summary": summary(tpl)}


@router.post("/api/templates/upload")
async def upload_template(file: UploadFile = File(...), user=User):
    """Read a template file (for a preview before using or saving it); nothing is stored."""
    raw = await file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        bad("That file is too large for a template (5 MB at most)")
    try:
        body = json.loads(raw)
    except ValueError:
        bad("That isn't a Probyte template file")
    body = validate(body)
    return {"body": body, "kind": body["kind"], "name": body["name"], "description": body.get("description") or "", "summary": summary(body)}


@router.post("/api/templates/export")
def export_template(body: SaveIn, user=User):
    """Download a template of a table or view, without saving it."""
    with connection() as conn:
        return _download(_make(conn, body, user))


@router.get("/api/templates/{template_id}/download")
def download_template(template_id: str, user=User):
    with connection() as conn:
        return _download(find(conn, template_id, user["id"]))


@router.delete("/api/templates/{template_id}")
def delete_template(template_id: str, user=User):
    with connection() as conn:
        n = conn.execute("DELETE FROM templates WHERE id = %s AND user_id = %s", (template_id, user["id"])).rowcount
    if not n:
        store.not_found("Template not found")
    return {"ok": True}


@router.post("/api/tables/from-template")
def table_from_template(body: FromTemplateIn, user=User):
    with connection() as conn:
        tpl = find(conn, body.template_id, user["id"]) if body.template_id else body.body
        if tpl is None:
            bad("Choose a template")
        base = store.get_base(conn, body.base_id, user["id"]) if body.base_id else store.ensure_base(conn, user["id"])
        from .routes.common import _table_out
        return _table_out(create_table(conn, user["id"], base["id"], tpl, body.name, body.include_records))


@router.post("/api/views/from-template")
def view_from_template(body: FromTemplateIn, user=User):
    if not body.table_id and not body.combo_id:
        bad("Say which table the view is for")
    with connection() as conn:
        tpl = find(conn, body.template_id, user["id"]) if body.template_id else body.body
        if tpl is None:
            bad("Choose a template")
        return create_view(conn, user, tpl, body.table_id, body.combo_id, body.name)
