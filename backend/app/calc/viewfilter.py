"""Apply a view's filters and sort on the server (mirrors frontend/src/util.ts applyView).

Used for public links, so records a view filters out never leave the server, and for the API's ?view=.
"""
import re
from functools import cmp_to_key

from app.calc import formula as F
from app.calc.compute import Engine
from app.formats import fieldformats


def display_text(eng: Engine, f, v) -> str:
    if v is None or (isinstance(v, dict) and "__error" in v):
        return ""
    t = f["type"]
    if t in ("link", "user", "created_by", "last_modified_by", "attachment"):
        return ", ".join(x for x in eng.shown(f, v) if x)
    if t in ("lookup", "conditional_lookup"):
        inn = eng.inner(f)
        shown = eng.shown(inn[2], v) if inn else v
        if inn and inn[0] == "phone":
            shown = [fieldformats.format_phone(x) for x in (shown if isinstance(shown, list) else [shown])]
        return ", ".join(F.to_str(x) for x in (shown if isinstance(shown, list) else [shown]) if not F.is_blank(x))
    if t == "checkbox":
        return "true" if v else "false"
    if t == "phone":
        return fieldformats.format_phone(v)
    if eng.value_type(f) == "date" and isinstance(v, str) and v:
        try:
            dt = F.local(F.parse_dt(v))
            day = f"{dt.strftime('%b')} {dt.day}, {dt.year}"
            return day if len(v) == 10 else f"{day}, {dt.strftime('%H:%M')}"
        except F.FormulaError:
            return v
    if isinstance(v, list):
        return ", ".join(F.to_str(x) for x in v)
    return F.to_str(v)


def _empty(v):
    return v is None or v == "" or v is False or (isinstance(v, list) and not v)


def _matches(eng, f, v, flt) -> bool:
    op = flt.get("op", "contains")
    text = display_text(eng, f, v).lower()
    raw = flt.get("value")
    val = ("" if raw is None else str(raw)).lower()  # (False and 0 are values too)
    if f["type"] == "checkbox" and op in ("is", "is_not"):  # ticked or not, whatever way the value is written
        hit = bool(v) == (val in ("true", "1", "yes", "checked"))
        return hit if op == "is" else not hit
    if op == "empty":
        return _empty(v)
    if op == "not_empty":
        return not _empty(v)
    if op == "contains":
        return val in text
    if op == "not_contains":
        return val not in text
    if op in ("is", "is_not") and eng.value_type(f) == "date" and re.match(r"^\d{4}-\d{2}-\d{2}$", val) and not _empty(v):
        try:
            hit = F.local(F.parse_dt(v)).date().isoformat() == val  # same day
        except F.FormulaError:
            hit = False
        return hit if op == "is" else not hit
    if op in ("gt", "gte", "lt", "lte"):
        if _empty(v):
            return False
        try:
            if eng.value_type(f) == "date":
                x, y = F.parse_dt(v), F.parse_dt(str(flt.get("value") or ""))
                if len(str(flt.get("value") or "").strip()) == 10:  # compare whole days against a date
                    x, y = F.local(x).date(), F.local(y).date()
            else:
                x, y = F.to_num(v), F.to_num(flt.get("value"))
        except F.FormulaError:
            return False
        return {"gt": x > y, "gte": x >= y, "lt": x < y, "lte": x <= y}[op]
    if isinstance(v, list):
        items = eng.shown(f, v) if f["type"] in ("link", "user", "attachment") else v
        hit = any(F.to_str(x).lower() == val for x in items)
    else:
        hit = text == val
    return hit if op == "is" else not hit


def view_filter(config: dict) -> dict:
    """A view's filter as a group {conjunction, items}."""
    g = config.get("filter")
    return g if isinstance(g, dict) and isinstance(g.get("items"), list) else {"conjunction": "and", "items": []}


def view_sorts(config: dict) -> list:
    """[{field, dir}, ...]"""
    return [x for x in config.get("sorts") or [] if isinstance(x, dict) and x.get("field")]


def _test(eng, by_id, r, node) -> bool:
    if isinstance(node.get("items"), list):  # a nested group
        items = [i for i in node["items"] if isinstance(i, dict)]
        if not items:
            return True
        results = (_test(eng, by_id, r, i) for i in items)
        return any(results) if node.get("conjunction") == "or" else all(results)
    f = by_id.get(str(node.get("field")))
    if not f:
        return True  # a condition on a deleted field doesn't filter anything
    if node.get("op", "contains") not in ("empty", "not_empty") and node.get("value") in (None, "") and f["type"] != "checkbox":
        return True  # nor does one still waiting for its value
    return _matches(eng, f, r["data"].get(str(f["id"])), node)


def apply_view(eng: Engine, fields: list, records: list, config: dict) -> list:
    by_id = {str(f["id"]): f for f in fields}
    out = records
    group = view_filter(config)  # (older views' flat filters go through the same checks)
    if group["items"]:
        out = [r for r in out if _test(eng, by_id, r, group)]
    for sort in reversed(view_sorts(config)):  # stable sorts: the first sort wins
        out = _sorted(eng, by_id, out, sort)
    return out


def _sorted(eng, by_id, out, sort):
    sf = by_id.get(str(sort.get("field")))
    if sf:
        sid, vt = str(sf["id"]), eng.value_type(sf)
        order = [c["name"] for c in sf["options"].get("choices", [])] if sf["type"] == "single_select" else None

        def cmp(a, b):
            x, y = a["data"].get(sid), b["data"].get(sid)
            if _empty(x) and _empty(y):
                return 0
            if _empty(x):
                return 1
            if _empty(y):
                return -1
            try:
                if vt == "number":
                    p, q = F.to_num(x), F.to_num(y)
                elif vt == "date":
                    p, q = F.parse_dt(x), F.parse_dt(y)
                elif order is not None:
                    p, q = (order.index(x) if x in order else 1e9), (order.index(y) if y in order else 1e9)
                else:
                    p, q = display_text(eng, sf, x).lower(), display_text(eng, sf, y).lower()
            except F.FormulaError:
                p, q = F.to_str(x), F.to_str(y)
            return (p > q) - (p < q)
        out = sorted(out, key=cmp_to_key(cmp), reverse=sort.get("dir") == "desc")
        if sort.get("dir") == "desc":  # keep empty values last in both directions
            out = [r for r in out if not _empty(r["data"].get(sid))] + [r for r in out if _empty(r["data"].get(sid))]
    return out


def normalize_config(vtype: str, cfg: dict) -> dict:
    """A view config sent in an older shape (flat `filters`, one `sort`, a table view's `groupBy`) -> today's."""
    c = dict(cfg or {})
    if "filters" in c:
        flat = c.pop("filters") or []
        if not (isinstance(c.get("filter"), dict) and isinstance(c["filter"].get("items"), list)):
            c["filter"] = {"conjunction": "and", "items": [x for x in flat if isinstance(x, dict)]}
    if "sort" in c:
        one = c.pop("sort")
        if not isinstance(c.get("sorts"), list) and isinstance(one, dict) and one.get("field"):
            c["sorts"] = [one]
    if vtype != "kanban" and "groupBy" in c:
        by = c.pop("groupBy")
        if by and not isinstance(c.get("groups"), list):
            c["groups"] = [{"field": by, "dir": "asc"}]
    return c
