"""Pages, and how fields that hold several records or values are shown. Pure checks of what the browser sends; the
browser draws both (frontend/src/components/PageView.tsx, PageBlocks.tsx, LinkedView.tsx).

A table's page (tables.page) lays out its records like a document instead of the record panel:

    {"enabled": true,
     "blocks": [<block>, …],                    the layout (every record, unless a layout for its kind says otherwise)
     "header": [field ids],                     shown under the title, and kept in view (with the title) when scrolling
     "outline": true,                           a list of the page's headings, sections and tabs to jump to
     "template": "## Scope\\n\\n…",             a new record's page body starts as this (Markdown; may place fields)
     "templateField": "<long text id>",         where it goes (default: the first page body of the layout)
     "variants": {"field": "<single select id>", "layouts": {"<option>": [<block>, …]}}}   a layout per kind of record

A block: {"id", "type", "span": 1-4 (of the page's 4 columns; default 4), "height": px (fixed; its content scrolls),
"when": <filter group> (shown only for records that match, as a view's filter)} and by type:

    field       "field", "view" (how it's shown: as the field says, else this)
    doc         "field": a long text, as the page's body (formatted, with fields placed in it as {{field:<id>}} lines)
    heading     "text";  divider
    properties  every field not placed elsewhere, as a list
    section     "text" (its title), "closed" (starts folded), "blocks": [<block>, …]
    tabs        "tabs": [{"id", "text", "blocks": [<block>, …]}, …]
    related     records of another table that link to this one: "table", "field" (that table's link field),
                "filterView" (one of that table's views: its filter and sort), "display": list | table | cards, "fields": [that table's field ids], "limit", "text"
    files       "field": an attachment field, or a link to a files table: a gallery with a drop zone
    stats       "items": [{"field", "view": stat | bar}, …]: big numbers side by side

A field's own choice of how it's shown (field.options.views), separately in the table's cells and on the record:

    {"cell": <a way>, "record": <a way>, "fields": [ids of the linked table's fields a list / table / cards show],
     "max": the full bar's value}

The ways (which fit which field type: frontend/src/display.ts): chips, count, text, list, table, cards, hcards
(cards in a row), value, bar, stat (a big number), stars, number, check, yesno, relative (a date as "in 3 days"),
thumbs, gallery, names, large (text set large).
"""
from fastapi import HTTPException

BLOCK_TYPES = {"properties", "field", "heading", "divider", "doc", "section", "tabs", "related", "files", "stats"}
SIZED = {"properties", "field", "doc", "section", "tabs", "related", "files", "stats"}
VIEWS = {"chips", "count", "text", "list", "table", "cards", "hcards", "value", "bar", "stat", "stars", "number", "check",
         "yesno", "relative", "thumbs", "gallery", "names", "large"}
MAX_BLOCKS = 300
MAX_DEPTH = 3


def _bad(msg: str):
    raise HTTPException(400, msg)


def _id(v, what: str) -> str:
    s = str(v or "").strip()
    if not s or len(s) > 64:
        _bad(f"Each block needs {what}")
    return s


def _filter(g, by_id: dict, depth=0) -> dict | None:
    """A block's "show only when": a view-style filter group on this table's fields (None: always)."""
    if g is None:
        return None
    if not isinstance(g, dict) or not isinstance(g.get("items"), list) or depth > 3:
        _bad("A block's condition is {conjunction, items}")
    items = []
    for it in g["items"][:20]:
        if isinstance(it, dict) and isinstance(it.get("items"), list):
            sub = _filter(it, by_id, depth + 1)
            if sub:
                items.append(sub)
        elif isinstance(it, dict):
            if str(it.get("field")) not in by_id:
                _bad("A block's condition uses a field that isn't in this table")
            items.append({k: it[k] for k in ("field", "op", "value") if k in it})
        else:
            _bad("A block's condition is {conjunction, items}")
    if not items:
        return None
    return {"conjunction": "or" if g.get("conjunction") == "or" else "and", "items": items}


class _Checker:
    def __init__(self, fields: list[dict]):
        self.by_id = {str(f["id"]): f for f in fields}
        self.ids: set[str] = set()
        self.count = 0

    def field(self, v, what="field") -> dict:
        f = self.by_id.get(str(v or ""))
        if not f:
            _bad(f"A block shows a {what} that isn't in this table")
        return f

    def blocks(self, blocks, depth=0) -> list[dict]:
        if not isinstance(blocks, list):
            _bad("blocks is a list")
        if depth > MAX_DEPTH:
            _bad("Sections and tabs can be nested only a few levels deep")
        return [self.block(b, depth) for b in blocks]

    def block(self, b, depth) -> dict:
        self.count += 1
        if self.count > MAX_BLOCKS:
            _bad(f"A page has at most {MAX_BLOCKS} blocks")
        if not isinstance(b, dict) or b.get("type") not in BLOCK_TYPES:
            _bad("Unknown kind of block")
        bid = _id(b.get("id"), "an id")
        if bid in self.ids:
            _bad("Two blocks have the same id")
        self.ids.add(bid)
        t = b["type"]
        c = {"id": bid, "type": t}
        if t in ("field", "doc", "files"):
            f = self.field(b.get("field"))
            if t == "doc" and f["type"] != "long_text":
                _bad("Only a long text field can be the page's body")
            if t == "files" and f["type"] != "attachment" and not (f["type"] == "link"):
                _bad("A files block shows an attachment field, or a link to a files table")
            c["field"] = str(f["id"])
        if t == "field" and b.get("view") is not None:
            if b["view"] not in VIEWS:
                _bad("Unknown way to show a field")
            c["view"] = b["view"]
        if t in ("heading", "section", "related") and b.get("text") is not None:
            c["text"] = str(b.get("text") or "").strip()[:200]
        if t == "section":
            if b.get("closed"):
                c["closed"] = True
            c["blocks"] = self.blocks(b.get("blocks") or [], depth + 1)
        if t == "tabs":
            tabs = b.get("tabs") or []
            if not isinstance(tabs, list) or not 1 <= len(tabs) <= 12:
                _bad("A tabs block has 1 to 12 tabs")
            c["tabs"] = []
            for tab in tabs:
                if not isinstance(tab, dict):
                    _bad("A tab is {id, text, blocks}")
                tid = _id(tab.get("id"), "an id")
                if tid in self.ids:
                    _bad("Two blocks have the same id")
                self.ids.add(tid)
                c["tabs"].append({"id": tid, "text": str(tab.get("text") or "").strip()[:80] or "Tab",
                                  "blocks": self.blocks(tab.get("blocks") or [], depth + 1)})
        if t == "related":
            for k in ("table", "field"):
                if not str(b.get(k) or "").strip():
                    _bad("A related list needs the table and its link field")
                c[k] = str(b[k])
            if b.get("filterView"):
                c["filterView"] = str(b["filterView"])
            if b.get("to"):   # a combined database: the source table whose records it's for
                c["to"] = str(b["to"])
            c["display"] = b.get("display") if b.get("display") in ("list", "table", "cards") else "list"
            if b.get("fields") is not None:
                if not isinstance(b["fields"], list):
                    _bad("fields is a list of field ids")
                c["fields"] = [str(x) for x in b["fields"]][:12]
            if b.get("limit") is not None:
                if not isinstance(b["limit"], int) or not 1 <= b["limit"] <= 500:
                    _bad("A related list shows 1 to 500 records")
                c["limit"] = b["limit"]
        if t == "stats":
            items = b.get("items") or []
            if not isinstance(items, list) or len(items) > 8:
                _bad("A summary shows up to 8 numbers")
            c["items"] = [{"field": str(self.field(it.get("field"))["id"]), "view": "bar" if it.get("view") == "bar" else "stat"}
                          for it in items if isinstance(it, dict)]
        if t in SIZED:
            span = {"half": 2, "full": 4}.get(b.get("width"), b.get("span"))   # (layouts from 0.9.7: width full / half)
            if span is not None:
                if not isinstance(span, int) or not 1 <= span <= 4:
                    _bad("A block spans 1 to 4 columns")
                if span != 4:
                    c["span"] = span
            if b.get("height") is not None:
                if not isinstance(b["height"], (int, float)) or not 60 <= b["height"] <= 3000:
                    _bad("A block's height is 60 to 3000 pixels")
                c["height"] = int(b["height"])
        when = _filter(b.get("when"), self.by_id)
        if when:
            c["when"] = when
        return c


def clean(fields: list[dict], page: dict) -> dict | None:
    """A page layout as it's kept: known block types, fields of this table, sensible sizes. {} (or nothing laid out and
    not enabled) removes the page."""
    if not isinstance(page, dict):
        _bad("A page is {enabled, blocks}")
    ch = _Checker(fields)
    out: dict = {"enabled": bool(page.get("enabled")), "blocks": ch.blocks(page.get("blocks") or [])}
    if page.get("header"):
        if not isinstance(page["header"], list):
            _bad("header is a list of field ids")
        out["header"] = [str(ch.field(x)["id"]) for x in page["header"][:8]]
    if page.get("outline"):
        out["outline"] = True
    if page.get("template"):
        if not isinstance(page["template"], str) or len(page["template"]) > 20000:
            _bad("A page template is text (up to 20,000 characters)")
        out["template"] = page["template"]
    if page.get("templateField"):
        f = ch.field(page["templateField"])
        if f["type"] != "long_text":
            _bad("A page template goes into a long text field")
        out["templateField"] = str(f["id"])
    v = page.get("variants")
    if v:
        if not isinstance(v, dict) or not isinstance(v.get("layouts") or {}, dict):
            _bad("variants is {field, layouts}")
        f = ch.field(v.get("field"), "field to choose the layout by")
        if f["type"] not in ("single_select", "checkbox"):
            _bad("Layouts are chosen by a single select (or a checkbox)")
        layouts = {str(k)[:100]: ch.blocks(bl) for k, bl in list((v.get("layouts") or {}).items())[:30]}
        out["variants"] = {"field": str(f["id"]), "layouts": layouts}
    if not out["enabled"] and not out["blocks"] and len(out) == 2:
        return None
    return out


def template_for(page: dict | None, fields: list[dict]) -> tuple[str, str] | None:
    """(long text field id, text) a new record's page body starts as, if the table's page has a template."""
    if not page or not page.get("template"):
        return None
    fid = page.get("templateField")
    if not fid:
        def first_doc(blocks):
            for b in blocks or []:
                if b.get("type") == "doc":
                    return b.get("field")
                for inner in [b.get("blocks")] + [t.get("blocks") for t in b.get("tabs") or []]:
                    found = first_doc(inner)
                    if found:
                        return found
            return None
        fid = first_doc(page.get("blocks"))
    if not fid:
        fid = next((str(f["id"]) for f in fields if f["type"] == "long_text" and not f.get("system_key")), None)
    return (fid, page["template"]) if fid else None


def clean_views(views) -> dict | None:
    """field.options.views as it's kept (None: the defaults)."""
    if views is None:
        return None
    if not isinstance(views, dict):
        _bad("views is {cell, record, fields, max}")
    out = {}
    for k in ("cell", "record"):
        if views.get(k) is not None:
            if views[k] not in VIEWS:
                _bad("Unknown way to show a field")
            out[k] = views[k]
    if views.get("fields") is not None:
        if not isinstance(views["fields"], list):
            _bad("views.fields is a list of field ids")
        out["fields"] = [str(x) for x in views["fields"]][:20]
    if views.get("max") is not None:
        try:
            m = float(views["max"])
        except (TypeError, ValueError):
            _bad("views.max is a number")
        if m <= 0:
            _bad("views.max is more than 0")
        out["max"] = m
    return out or None
