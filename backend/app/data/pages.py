"""Pages, and how fields that hold several records or values are shown. Pure checks of what the browser sends; the
browser draws both (frontend/src/components/PageView.tsx, LinkedView.tsx).

A table's page (tables.page) lays out its records like a document instead of the record panel:

    {"enabled": true, "blocks": [
        {"id": "b1", "type": "properties"},                      the fields not placed in another block, as a list
        {"id": "b2", "type": "field", "field": "<id>", "view": "table", "width": "half"},
        {"id": "b3", "type": "heading", "text": "Details"},
        {"id": "b4", "type": "divider"},
        {"id": "b5", "type": "doc", "field": "<long text field id>"},   the field as the page's body (rich text, with
    ]}                                                                fields placed in it as {{field:<id>}} lines)

A field's own choice of how it's shown (field.options.views), separately in the table's cells and on the record:

    {"cell": "chips" | "count", "record": "chips" | "list" | "table" | "cards" | "bar" | "value",
     "fields": [ids of the linked table's fields the list / table / cards show], "max": the full bar's value}
"""
from fastapi import HTTPException

BLOCK_TYPES = {"properties", "field", "heading", "divider", "doc"}
VIEWS = {"chips", "count", "list", "table", "cards", "value", "bar"}
MAX_BLOCKS = 200


def _bad(msg: str):
    raise HTTPException(400, msg)


def _id(v, what: str) -> str:
    s = str(v or "").strip()
    if not s or len(s) > 64:
        _bad(f"Each block needs {what}")
    return s


def clean(fields: list[dict], page: dict) -> dict | None:
    """A page layout as it's kept: known block types, fields of this table (a doc block: a long text field), sensible
    sizes. {} (or no blocks and not enabled) removes the page."""
    if not isinstance(page, dict):
        _bad("A page is {enabled, blocks}")
    blocks = page.get("blocks") or []
    if not isinstance(blocks, list) or len(blocks) > MAX_BLOCKS:
        _bad(f"A page has at most {MAX_BLOCKS} blocks")
    by_id = {str(f["id"]): f for f in fields}
    out, seen = [], set()
    for b in blocks:
        if not isinstance(b, dict) or b.get("type") not in BLOCK_TYPES:
            _bad("Unknown kind of block")
        bid = _id(b.get("id"), "an id")
        if bid in seen:
            _bad("Two blocks have the same id")
        seen.add(bid)
        c = {"id": bid, "type": b["type"]}
        if b["type"] in ("field", "doc"):
            f = by_id.get(str(b.get("field") or ""))
            if not f:
                _bad("A block shows a field that isn't in this table")
            if b["type"] == "doc" and f["type"] != "long_text":
                _bad("Only a long text field can be the page's body")
            c["field"] = str(f["id"])
        if b["type"] == "field" and b.get("view") is not None:
            if b["view"] not in VIEWS:
                _bad("Unknown way to show a field")
            c["view"] = b["view"]
        if b["type"] == "heading":
            c["text"] = str(b.get("text") or "").strip()[:200]
        if b.get("width") in ("full", "half") and b["type"] in ("field", "properties"):
            c["width"] = b["width"]
        out.append(c)
    enabled = bool(page.get("enabled"))
    if not enabled and not out:
        return None
    return {"enabled": enabled, "blocks": out}


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
