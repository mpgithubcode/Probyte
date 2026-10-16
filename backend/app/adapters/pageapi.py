"""Pages through the API (/api/v1 and MCP): a table's page layout, and one record's page as it shows.

    describe_page(table)            the layout: blocks (fields by id and name, how each is shown and why), conditions,
                                    sections and tabs, related lists, numbers, the fields under the title, the outline,
                                    the template for new records, and the layouts for each kind of record
    record_page(table, record)      that record's page: which layout it gets (and why), every block (also the ones its
                                    conditions hide, with "shown": false), each condition with the record's actual
                                    values and whether it's met, the values shown (raw and as text), related records,
                                    files, numbers, and the fields under the title

Pages are drawn by the browser (frontend/src/components/PageBlocks.tsx); this reads the same layout the same way: a
block's condition is a view filter (calc/viewfilter.matches), a block inside a hidden section or tab is hidden too,
and a field shows its block's own way, else its own "on the record" way, else its type's default (frontend/src/display.ts).
"""
from app.adapters import apiv1
from app.calc.compute import Engine
from app.calc.viewfilter import apply_view, display_text, matches
from app.data import pages, store

# the default way each kind of field shows on a record (the first of display.ts's lists), and the others it can
_RECORD_WAYS = {
    "link": ["chips", "list", "table", "cards", "hcards", "count"],
    "lookup": ["chips", "list", "hcards", "text", "count"], "conditional_lookup": ["chips", "list", "hcards", "text", "count"],
    "multi_select": ["chips", "list", "text", "count"],
    "user": ["chips", "list", "text"], "created_by": ["chips", "list", "text"], "last_modified_by": ["chips", "list", "text"],
    "attachment": ["thumbs", "gallery", "names"], "rating": ["stars", "number"], "checkbox": ["check", "yesno"],
    "single_select": ["chips", "text"], "text": ["value", "large"], "url": ["value", "large"], "email": ["value", "large"],
    "phone": ["value", "large"],
}
_WIDE = {"long_text", "link", "lookup", "conditional_lookup", "attachment", "multi_select", "recurrence"}
_BUILTIN = {"created_time", "modified_time", "created_by", "modified_by"}


def _ways(eng, f) -> list[str]:
    if f["type"] in _RECORD_WAYS:
        return _RECORD_WAYS[f["type"]]
    try:
        vt = eng.value_type(f)
    except Exception:
        vt = "text"
    return {"number": ["value", "bar", "stat"], "date": ["value", "relative"], "boolean": ["check", "yesno"]}.get(vt, [])


def _view(eng, f, own):
    """(how the field shows, why): the block's own choice, else the field's, else its type's default."""
    ways = _ways(eng, f)
    if own and own in ways:
        return own, "block"
    mine = ((f.get("options") or {}).get("views") or {}).get("record")
    if mine and mine in ways:
        return mine, "field"
    return (ways[0] if ways else "value"), "default"


def _ref(f) -> dict | None:
    return {"id": str(f["id"]), "name": f["name"], "type": f["type"]} if f else None


def _default_blocks(fields) -> list[dict]:
    primary = next((f for f in fields if f.get("is_primary")), None)
    shown = [f for f in fields if f["id"] != "__source" and f["type"] not in ("record_id", "button") and f.get("system_key") not in _BUILTIN
             and f is not primary]
    body = next((f for f in shown if f["type"] == "long_text" and not f.get("system_key")), None)
    out = [{"id": f"f-{f['id']}", "type": "field", "field": str(f["id"]), **({} if f["type"] in _WIDE else {"span": 2})} for f in shown if f is not body]
    if body:
        out.append({"id": "body", "type": "doc", "field": str(body["id"])})
    return out


def _load(conn, user, ref, base=None):
    kind, row = apiv1.resolve(conn, user, ref, base)
    eng = Engine(conn)
    fields = apiv1._fields(conn, kind, row, eng)
    page = (conn.execute(f"SELECT page FROM {'tables' if kind == 'table' else 'combos'} WHERE id = %s", (row["id"],)).fetchone() or {}).get("page")
    return kind, row, eng, fields, page


def _cond(group, by_id) -> dict | None:
    """A block's condition with its fields by id and name."""
    if not group or not group.get("items"):
        return None
    items = []
    for it in group["items"]:
        if isinstance(it, dict) and isinstance(it.get("items"), list):
            sub = _cond(it, by_id)
            if sub:
                items.append(sub)
        elif isinstance(it, dict):
            items.append({"field": _ref(by_id.get(str(it.get("field")))) or {"id": str(it.get("field")), "name": None},
                          "op": it.get("op"), "value": it.get("value")})
    return {"conjunction": group.get("conjunction", "and"), "items": items}


def _describe_blocks(conn, eng, blocks, by_id) -> list[dict]:
    out = []
    for b in blocks or []:
        f = by_id.get(str(b.get("field"))) if b.get("field") else None
        o = {"id": b["id"], "type": b["type"]}
        if b["type"] not in ("heading", "divider"):
            o["span"] = b.get("span", 4)
            if b.get("height"):
                o["height"] = b["height"]
        if b.get("when"):
            o["condition"] = _cond(b["when"], by_id)
        if f is not None:
            o["field"] = _ref(f)
        if b["type"] == "field" and f is not None:
            o["view"], o["view_from"] = _view(eng, f, b.get("view"))
        if b["type"] == "doc":
            o["view"] = "page_body"
        if b.get("text") is not None:
            o["title" if b["type"] in ("section", "related") else "text"] = b["text"]
        if b["type"] == "section":
            o["starts_folded"] = bool(b.get("closed"))
            o["blocks"] = _describe_blocks(conn, eng, b.get("blocks"), by_id)
        if b["type"] == "tabs":
            o["tabs"] = [{"id": t["id"], "title": t.get("text"), "blocks": _describe_blocks(conn, eng, t.get("blocks"), by_id)} for t in b.get("tabs") or []]
        if b["type"] == "stats":
            o["items"] = [{"field": _ref(by_id.get(str(i["field"]))), "view": i.get("view", "stat")} for i in b.get("items") or []]
        if b["type"] == "related":
            t = conn.execute("SELECT id, name FROM tables WHERE id::text = %s", (str(b.get("table")),)).fetchone()
            lf = eng.field(b.get("field")) if b.get("field") else None
            v = conn.execute("SELECT id, name FROM views WHERE id::text = %s", (str(b.get("filterView")),)).fetchone() if b.get("filterView") else None
            o["related"] = {"table": {"id": str(t["id"]), "name": t["name"]} if t else None, "link_field": _ref(lf),
                            "view": {"id": str(v["id"]), "name": v["name"]} if v else None, "display": b.get("display", "list"),
                            "fields": b.get("fields"), "limit": b.get("limit", 100)}
            if b.get("to"):
                o["related"]["for_records_of"] = b["to"]
        out.append(o)
    return out


def describe_page(conn, user, ref, base=None) -> dict:
    """A table's (or combined database's) page layout, with every field by id and name."""
    kind, row, eng, fields, page = _load(conn, user, ref, base)
    by_id = {str(f["id"]): f for f in fields}
    page = page or {}
    primary = next((f for f in fields if f.get("is_primary")), None)
    blocks = page.get("blocks") or _default_blocks(fields)
    out = {"table": {"id": str(row["id"]), "name": row["name"], "kind": kind}, "enabled": bool(page.get("enabled")),
           "laid_out": bool(page.get("blocks")), "title_field": _ref(primary),
           "header": [_ref(by_id[x]) for x in page.get("header") or [] if x in by_id], "outline": bool(page.get("outline")),
           "layout": _describe_blocks(conn, eng, blocks, by_id)}
    if page.get("template"):
        out["template"] = {"text": page["template"], "into": _ref(by_id.get(str((pages.template_for(page, fields) or ("",))[0])))}
    v = page.get("variants")
    if v:
        out["layouts_by"] = {"field": _ref(by_id.get(v["field"])),
                             "layouts": {k: _describe_blocks(conn, eng, bl, by_id) for k, bl in (v.get("layouts") or {}).items()}}
    return out


# ---------------------------------------------------------------- one record's page

def _met(eng, fields, rec, group, by_id) -> dict:
    """A condition, each part with the record's actual value and whether it's met."""
    items = []
    for it in group.get("items") or []:
        if isinstance(it, dict) and isinstance(it.get("items"), list):
            items.append(_met(eng, fields, rec, it, by_id))
            continue
        f = by_id.get(str(it.get("field")))
        actual = rec["data"].get(str(f["id"])) if f else None
        items.append({"field": _ref(f) or {"id": str(it.get("field")), "name": None}, "op": it.get("op"), "value": it.get("value"),
                      "actual": actual, "actual_text": display_text(eng, f, actual) if f and actual is not None else None,
                      "met": matches(eng, fields, rec, {"conjunction": "and", "items": [it]})})
    return {"conjunction": group.get("conjunction", "and"), "items": items, "met": matches(eng, fields, rec, group)}


def _value(eng, f, rec) -> dict:
    v = rec["data"].get(str(f["id"]))
    if isinstance(v, dict) and "__error" in v:
        return {"value": None, "text": "#ERROR", "error": v["__error"]}
    out = {"value": v, "text": display_text(eng, f, v) if v is not None else None}
    if f["type"] == "attachment" and isinstance(v, list):
        out["files"] = [{"id": a.get("id"), "name": a.get("name"), "size": a.get("size"), "mime": a.get("mime")} for a in v if isinstance(a, dict)]
    return out


def _related(conn, user, eng, b, rec) -> dict:
    if b.get("to") and b["to"] != str(rec["table_id"]):
        return {"applies": False, "records": [], "total": 0}
    try:
        kind, row = apiv1.resolve(conn, user, str(b.get("table")), allow_combo=False)
    except Exception as e:   # (a table this key or user can't open)
        return {"error": getattr(e, "detail", str(e)), "records": [], "total": 0}
    fields = eng.describe(row["id"])
    recs = [r for r in eng.records(row["id"]) if str(rec["id"]) in (r["data"].get(str(b.get("field"))) or [])]
    if b.get("filterView"):
        v = conn.execute("SELECT config FROM views WHERE id::text = %s AND table_id = %s", (str(b["filterView"]), row["id"])).fetchone()
        if v:
            recs = apply_view(eng, fields, recs, v["config"])
    titles = eng.titles(row["id"])
    limit = b.get("limit", 100)
    show = [f for f in fields if str(f["id"]) in set(b.get("fields") or [])] or [f for f in fields if f.get("is_primary")]
    return {"total": len(recs), "records": [{"id": str(r["id"]), "title": titles.get(str(r["id"]), "Untitled"),
                                             "fields": {f["name"]: display_text(eng, f, r["data"].get(str(f["id"]))) for f in show
                                                        if r["data"].get(str(f["id"])) not in (None, "", [])}} for r in recs[:limit]]}


def _resolve_blocks(conn, user, eng, fields, by_id, rec, blocks, parent_shown, placed) -> list[dict]:
    out = []
    for b in blocks or []:
        f = by_id.get(str(b.get("field"))) if b.get("field") else None
        o = {"id": b["id"], "type": b["type"]}
        cond = _met(eng, fields, rec, b["when"], by_id) if b.get("when") else None
        o["shown"] = parent_shown and (cond is None or cond["met"])
        if cond:
            o["condition"] = cond
        if b["type"] not in ("heading", "divider"):
            o["span"] = b.get("span", 4)
        if f is not None:
            o["field"] = _ref(f)
            o.update(_value(eng, f, rec))
        if b["type"] == "field" and f is not None:
            o["view"], o["view_from"] = _view(eng, f, b.get("view"))
        if b["type"] == "doc":
            o["view"] = "page_body"
            text = rec["data"].get(str(f["id"])) if f else None
            if isinstance(text, str):   # fields placed in the page body's text (a combined database's: its source's ids)
                found = [_embedded(fields, by_id, x, rec) for x in pages_embeds(text)]
                o["fields_in_text"] = [{**_ref(x), **_value(eng, x, rec)} for x in found if x]
        if b.get("text") is not None:
            o["title" if b["type"] in ("section", "related") else "text"] = b["text"]
        if b["type"] == "section":
            o["starts_folded"] = bool(b.get("closed"))
            o["blocks"] = _resolve_blocks(conn, user, eng, fields, by_id, rec, b.get("blocks"), o["shown"], placed)
        if b["type"] == "tabs":
            o["tabs"] = [{"id": t["id"], "title": t.get("text"),
                          "blocks": _resolve_blocks(conn, user, eng, fields, by_id, rec, t.get("blocks"), o["shown"], placed)} for t in b.get("tabs") or []]
        if b["type"] == "stats":
            o["items"] = [{"field": _ref(by_id.get(str(i["field"]))), "view": i.get("view", "stat"), **(_value(eng, by_id[str(i["field"])], rec) if str(i["field"]) in by_id else {})}
                          for i in b.get("items") or []]
        if b["type"] == "related":
            o["related"] = {"table_id": b.get("table"), "link_field_id": b.get("field"), "display": b.get("display", "list"),
                            **(_related(conn, user, eng, b, rec) if o["shown"] else {"records": [], "total": None})}
        if b["type"] == "properties":
            o["fields"] = [{**_ref(x), **_value(eng, x, rec)} for x in fields if str(x["id"]) not in placed and x["id"] != "__source"
                           and x["type"] not in ("record_id", "button") and x.get("system_key") not in _BUILTIN and not x.get("is_primary")]
        out.append(o)
    return out


def _embedded(fields, by_id, fid, rec):
    """A field placed in a page body's text, by id; a combined database's record keeps its source table's ids."""
    return by_id.get(fid) or next((f for f in fields if (f.get("mapping") or {}).get(str(rec["table_id"])) == fid), None)


def pages_embeds(text: str) -> list[str]:
    import re
    return re.findall(r"^\{\{field:([\w-]+)\}\}$", text, re.M)


def record_page(conn, user, ref, record_id, base=None) -> dict:
    """One record's page: its layout (and why), every block with whether it shows, its conditions met or not, values."""
    kind, row, eng, fields, page = _load(conn, user, ref, base)
    by_id = {str(f["id"]): f for f in fields}
    recs = eng.records(row["id"]) if kind == "table" else store.combo_records(conn, row, eng)
    rec = next((r for r in recs if str(r["id"]) == str(record_id)), None)
    if not rec:
        apiv1.bad("Record not found", 404)
    page = page or {}
    blocks, used = page.get("blocks") or _default_blocks(fields), {"layout": "default"}
    v = page.get("variants")
    if v and v.get("field") in by_id:
        f = by_id[v["field"]]
        raw = rec["data"].get(v["field"])
        key = str(raw).lower() if isinstance(raw, bool) else ("false" if raw is None and f["type"] == "checkbox" else ("" if raw is None else str(raw)))
        used = {"layout": "default", "by": _ref(f), "value": raw, "layouts_for": sorted((v.get("layouts") or {}).keys())}
        if key in (v.get("layouts") or {}):
            blocks, used["layout"] = v["layouts"][key], key
    placed = {str(b.get("field")) for b in _walk(blocks)} | {str(i["field"]) for b in _walk(blocks) for i in b.get("items") or []}
    for b in _walk(blocks):
        if b.get("type") == "doc" and isinstance(rec["data"].get(str(b.get("field"))), str):
            placed |= {str(x["id"]) for x in (_embedded(fields, by_id, e, rec) for e in pages_embeds(rec["data"][str(b["field"])])) if x}
    primary = next((f for f in fields if f.get("is_primary")), None)
    return {"table": {"id": str(row["id"]), "name": row["name"], "kind": kind}, "enabled": bool(page.get("enabled")),
            "record": {"id": str(rec["id"]), "table_id": str(rec["table_id"]),
                       "title": display_text(eng, primary, rec["data"].get(str(primary["id"]))) if primary else None},
            "layout_used": used,
            "header": [{**_ref(by_id[x]), **_value(eng, by_id[x], rec)} for x in page.get("header") or [] if x in by_id],
            "outline": bool(page.get("outline")),
            "blocks": _resolve_blocks(conn, user, eng, fields, by_id, rec, blocks, True, placed)}


def _walk(blocks):
    for b in blocks or []:
        yield b
        yield from _walk(b.get("blocks"))
        for t in b.get("tabs") or []:
            yield from _walk(t.get("blocks"))
