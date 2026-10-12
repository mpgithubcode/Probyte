"""MCP tools for Blueprint takeoff: an AI builds a takeoff step by step, as a person does in the app, through
Probyte's MCP server (shown while the extension is on). Sheets are named by their sheet number (E1.1) or page
number; items by name or id. Finds are suggestions until reviewed; only accepted marks are in the takeoff."""
from fastapi import HTTPException

from ...db import connection
from . import api

PROJECT = {"type": "string", "description": "Takeoff project id or name"}
SHEET = {"type": ["string", "integer"], "description": "Sheet number as on the drawing (E1.1) or page number (1 = first page)"}
SHEETS = {"type": "array", "items": SHEET, "description": "Default: the sheets in the takeoff"}


def _tool(name, title, description, props, required=(), read_only=False, destructive=False):
    return {"name": name, "title": title, "description": description,
            "inputSchema": {"type": "object", "properties": props, "required": list(required), "additionalProperties": False},
            "annotations": {"title": title, "readOnlyHint": read_only, "destructiveHint": destructive, "idempotentHint": read_only,
                            "openWorldHint": False}}


TOOLS = [
    _tool("takeoff_list_projects", "List takeoffs", "Your Blueprint takeoff projects (drawing sets).", {}, read_only=True),
    _tool("takeoff_create_project", "Start a takeoff",
          "Start a takeoff from a PDF drawing set in one of your files tables (its record id; find it with list_records or search). "
          "Every sheet's number, title, discipline and scale are read. Next: takeoff_update_sheets to choose the sheets.",
          {"record_id": {"type": "string"}, "name": {"type": "string"}}, ["record_id"]),
    _tool("takeoff_get_project", "Get a takeoff",
          "A takeoff: its sheets (number, title, discipline, in the takeoff or not, scale), its items and the takeoff list (quantities).",
          {"project": PROJECT}, ["project"], read_only=True),
    _tool("takeoff_update_sheets", "Choose and check sheets",
          "Put sheets in the takeoff or leave them out, mark them checked, or correct a number, title, discipline or scale. "
          "disciplines: include every sheet of these disciplines (e.g. [\"Electrical\"]).",
          {"project": PROJECT, "sheets": SHEETS, "disciplines": {"type": "array", "items": {"type": "string"}},
           "included": {"type": "boolean"}, "confirmed": {"type": "boolean"},
           "sheet_no": {"type": "string"}, "title": {"type": "string"}, "discipline": {"type": "string"},
           "scale": {"type": "string", "description": "A scale written like 1/4\" = 1'-0\", 1\" = 20' or 1:100"}}, ["project"]),
    _tool("takeoff_read_legends", "Read a sheet's legends",
          "The legends and schedules on a sheet with their entries (name, label, whether it has a symbol picture, details). "
          "Add them as items with takeoff_add_items (from_legend).",
          {"project": PROJECT, "sheet": SHEET}, ["project", "sheet"], read_only=True),
    _tool("takeoff_add_items", "Add items",
          "Add what to take off. from_legend: entries of a sheet's legends (all, or by their names/labels), with their symbols "
          "and details. items: items described by hand (kind count / length / area; label = the text next to it on plans).",
          {"project": PROJECT,
           "from_legend": {"type": "object", "properties": {"sheet": SHEET, "legend": {"type": "string", "description": "Heading, e.g. LIGHT FIXTURE SCHEDULE (default: all)"},
                                                             "entries": {"type": "array", "items": {"type": "string"}, "description": "Names or labels (default: all)"}},
                           "required": ["sheet"]},
           "items": {"type": "array", "items": {"type": "object", "properties": {
               "name": {"type": "string"}, "kind": {"type": "string", "enum": ["count", "length", "area"]}, "label": {"type": "string"},
               "category": {"type": "string"}, "unit": {"type": "string"}, "waste_pct": {"type": "number"}, "unit_cost": {"type": "number"}},
               "required": ["name"]}}}, ["project"]),
    _tool("takeoff_update_item", "Change an item",
          "Rename an item or set its label, kind, unit, waste %, unit cost, per-run length (runs), category or notes.",
          {"project": PROJECT, "item": {"type": "string"}, "name": {"type": "string"}, "label": {"type": "string"},
           "kind": {"type": "string", "enum": ["count", "length", "area"]}, "unit": {"type": "string"}, "category": {"type": "string"},
           "waste_pct": {"type": "number"}, "unit_cost": {"type": "number"}, "per_run": {"type": "number"}, "notes": {"type": "string"}},
          ["project", "item"]),
    _tool("takeoff_find", "Find items on sheets",
          "Look for items on sheets: by label (the text next to each on the plans) or by symbol (the legend picture). "
          "Finds are suggestions: review them with takeoff_list_marks and takeoff_review. items: names/ids (default: every item to count).",
          {"project": PROJECT, "items": {"type": "array", "items": {"type": "string"}}, "sheets": SHEETS,
           "method": {"type": "string", "enum": ["auto", "label", "symbol"], "default": "auto"},
           "likeness": {"type": "number", "minimum": 0.5, "maximum": 0.95, "default": 0.75, "description": "Symbols: how alike (0.5–0.95)"}},
          ["project"]),
    _tool("takeoff_list_marks", "List marks",
          "The marks on sheets (finds and hand-made counts / runs / areas): item, sheet, status, how alike (score), where.",
          {"project": PROJECT, "item": {"type": "string"}, "sheet": SHEET,
           "status": {"type": "string", "enum": ["suggested", "confirmed", "rejected"]},
           "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200}}, ["project"], read_only=True),
    _tool("takeoff_review", "Accept or reject finds",
          "Accept (confirmed) or reject suggestions: chosen marks, or an item's suggestions (on a sheet, or at least this alike).",
          {"project": PROJECT, "status": {"type": "string", "enum": ["confirmed", "rejected"]}, "marks": {"type": "array", "items": {"type": "string"}},
           "item": {"type": "string"}, "sheet": SHEET, "min_score": {"type": "number"}}, ["project", "status"]),
    _tool("takeoff_add_marks", "Count or measure",
          "Add marks by hand: points to count, or lines (runs, in PDF points; measured with the sheet's scale) or areas.",
          {"project": PROJECT, "item": {"type": "string"}, "sheet": SHEET, "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
           "lines": {"type": "array", "items": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}}},
           "areas": {"type": "array", "items": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}}}},
          ["project", "item", "sheet"]),
    _tool("takeoff_search_text", "Search the sheets' text", "Where some text is written on the sheets (e.g. GFCI, WP, EM-2).",
          {"project": PROJECT, "query": {"type": "string"}, "all_sheets": {"type": "boolean", "default": False}}, ["project", "query"], read_only=True),
    _tool("takeoff_summary", "Get the takeoff list",
          "The takeoff: per item, quantity by sheet and in all, waste, cost, and suggestions still to review.",
          {"project": PROJECT}, ["project"], read_only=True),
    _tool("takeoff_export_table", "Export to a Probyte table", "The takeoff list as a new Probyte table in a base.",
          {"project": PROJECT, "base": {"type": "string", "description": "Base id or name (default: your first)"}, "name": {"type": "string"}},
          ["project"]),
]
READ = {t["name"] for t in TOOLS if t["annotations"]["readOnlyHint"]}


def _project_id(user, ref) -> str:
    with connection() as conn:
        rows = conn.execute("SELECT id, name FROM tk_projects WHERE user_id = %s ORDER BY updated_at DESC", (user["id"],)).fetchall()
    for r in rows:
        if str(r["id"]) == str(ref) or r["name"].lower() == str(ref).strip().lower():
            return str(r["id"])
    raise HTTPException(404, f"No takeoff called “{ref}”. Yours: {', '.join(r['name'] for r in rows) or 'none'}")


def _page_no(project: dict, ref) -> int:
    for g in project["pages"]:
        if (g["sheet_no"] or "").lower() == str(ref).strip().lower():
            return g["page_no"]
    try:
        n = int(ref) - 1
    except (TypeError, ValueError):
        raise HTTPException(404, f"No sheet {ref}. Sheets: {', '.join(g['sheet_no'] or str(g['page_no'] + 1) for g in project['pages'])}")
    if not 0 <= n < len(project["pages"]):
        raise HTTPException(404, f"No page {ref}")
    return n


def _item_id(project: dict, ref) -> str:
    for it in project["items"]:
        if it["id"] == str(ref) or it["name"].lower() == str(ref).strip().lower() or (it["tag"] or "").lower() == str(ref).strip().lower():
            return it["id"]
    raise HTTPException(404, f"No item {ref}")


def _sheet_out(g) -> dict:
    return {"sheet": g["sheet_no"] or g["page_no"] + 1, "page": g["page_no"] + 1, "title": g["title"], "discipline": g["discipline"],
            "in_takeoff": g["included"], "checked": g["confirmed"], "scale": (g["scale"] or {}).get("label")}


def call(user, name: str, a: dict):
    if name == "takeoff_list_projects":
        return {"projects": api.list_projects(None, user)}
    if name == "takeoff_create_project":
        p = api.project_from_file(api.FromFile(record_id=a["record_id"], name=a.get("name") or ""), user)
        return {"project": p["id"], "name": p["name"], "sheets": [_sheet_out(g) for g in p["pages"]]}
    pid = _project_id(user, a["project"])
    p = api.get_project(pid, user)
    if name == "takeoff_get_project":
        return {"project": pid, "name": p["name"], "sheets": [_sheet_out(g) for g in p["pages"]],
                "items": [{"id": i["id"], "name": i["name"], "label": i["tag"], "kind": i["kind"], "unit": i["unit"], "category": i["category"],
                           "has_symbol": bool(i["symbol"])} for i in p["items"]],
                "takeoff": p["summary"]}
    if name == "takeoff_update_sheets":
        pages = {_page_no(p, s) for s in a.get("sheets") or []}
        pages |= {g["page_no"] for g in p["pages"] if (g["discipline"] or "").lower() in {d.lower() for d in a.get("disciplines") or []}}
        if not pages:
            raise HTTPException(400, "Name the sheets (sheets) or disciplines")
        patch = {k: a[k] for k in ("included", "confirmed", "sheet_no", "title", "discipline") if k in a}
        if a.get("scale"):
            sc = _parse_scale(a["scale"])
            patch["scale"] = {"ft_per_pt": sc[0], "label": sc[1], "source": "manual"}
        for n in sorted(pages):
            api.update_page(pid, n, api.PageIn(**patch), user)
        return {"sheets": [_sheet_out(g) for g in api.get_project(pid, user)["pages"] if g["page_no"] in pages]}
    if name == "takeoff_read_legends":
        n = _page_no(p, a["sheet"])
        return {"legends": [{"heading": lg["heading"], "entries": [{"name": e["name"], "label": e["tag"], "has_symbol": bool(e["symbol"]),
                                                                    "details": e["details"]} for e in lg["rows"]]}
                            for lg in api.legends_cached(pid, api._open(pid), n)]}
    if name == "takeoff_add_items":
        new = []
        if a.get("from_legend"):
            fl = a["from_legend"]
            n = _page_no(p, fl["sheet"])
            want = {x.strip().lower() for x in fl.get("entries") or []}
            for lg in api.legends_cached(pid, api._open(pid), n):
                if fl.get("legend") and (lg["heading"] or "").lower() != fl["legend"].strip().lower():
                    continue
                for e in lg["rows"]:
                    if want and e["name"].lower() not in want and (e["tag"] or "").lower() not in want:
                        continue
                    new.append(api.ItemIn(name=e["name"], tag=e["tag"], category=lg["heading"], details=e["details"],
                                          symbol={"page": n, "rect": e["symbol"], "legend": lg["rect"]} if e["symbol"] else None))
        for it in a.get("items") or []:
            new.append(api.ItemIn(name=it["name"], kind=it.get("kind"), tag=it.get("label"), category=it.get("category"), unit=it.get("unit"),
                                  waste_pct=it.get("waste_pct"), unit_cost=it.get("unit_cost")))
        if not new:
            raise HTTPException(400, "Nothing to add: give from_legend (a sheet with a legend) or items")
        added = api.add_items(pid, api.ItemsIn(items=new), user)
        return {"added": [{"id": i["id"], "name": i["name"], "label": i["tag"], "kind": i["kind"], "has_symbol": bool(i["symbol"])} for i in added]}
    if name == "takeoff_update_item":
        iid = _item_id(p, a["item"])
        fields = {k: a[k] for k in ("name", "kind", "unit", "category", "waste_pct", "unit_cost", "per_run", "notes") if k in a}
        if "label" in a:
            fields["tag"] = a["label"]
        return api.update_item(iid, api.ItemIn(**fields), user)
    if name == "takeoff_find":
        ids = [_item_id(p, x) for x in a["items"]] if a.get("items") else [i["id"] for i in p["items"] if i["kind"] == "count" and (i["tag"] or i["symbol"])]
        pages = [_page_no(p, s) for s in a["sheets"]] if a.get("sheets") else [g["page_no"] for g in p["pages"] if g["included"]]
        if not pages:
            raise HTTPException(400, "No sheets in the takeoff yet: takeoff_update_sheets first")
        method = {"label": "tag", "symbol": "symbol"}.get(a.get("method") or "auto", "auto")
        out = []
        for iid in ids:
            it = next(i for i in p["items"] if i["id"] == iid)
            try:
                r = api.find_item(iid, api.FindIn(pages=pages, method=method, threshold=float(a.get("likeness") or 0.75)), user)
                out.append({"item": it["name"], "method": "label" if r["method"] == "tag" else "symbol", "found": r["found"],
                            "by_sheet": {(x["sheet_no"] or x["page_no"] + 1): x["found"] for x in r["pages"] if x["found"]}})
            except HTTPException as e:
                out.append({"item": it["name"], "error": e.detail})
        return {"results": out, "next": "Review the suggestions: takeoff_list_marks (status suggested), then takeoff_review"}
    if name == "takeoff_list_marks":
        items = {i["id"]: i["name"] for i in p["items"]}
        sheet = {g["page_no"]: g["sheet_no"] for g in p["pages"]}
        ms = api.list_marks(pid, _page_no(p, a["sheet"]) if a.get("sheet") is not None else None,
                            _item_id(p, a["item"]) if a.get("item") else None, user)
        if a.get("status"):
            ms = [m for m in ms if m["status"] == a["status"]]
        lim = int(a.get("limit") or 200)
        return {"total": len(ms), "marks": [{"id": m["id"], "item": items.get(m["item_id"]), "sheet": sheet.get(m["page_no"]), "status": m["status"],
                                             "source": m["source"], "score": m["score"], "type": m["geom"]["type"], "points": m["geom"]["points"],
                                             "value": m["value"]} for m in ms[:lim]]}
    if name == "takeoff_review":
        body = api.Review(status=a["status"], ids=a.get("marks"), item_id=_item_id(p, a["item"]) if a.get("item") else None,
                          page_no=_page_no(p, a["sheet"]) if a.get("sheet") is not None else None, min_score=a.get("min_score"))
        return api.review(pid, body, user)
    if name == "takeoff_add_marks":
        iid, n = _item_id(p, a["item"]), _page_no(p, a["sheet"])
        made = []
        for kind, key in (("point", "points"), ("line", "lines"), ("area", "areas")):
            for g in a.get(key) or []:
                pts = [g] if kind == "point" else g
                made.append(api.add_mark(pid, api.MarkIn(item_id=iid, page_no=n, geom={"type": kind, "points": pts}), user))
        return {"added": len(made), "values": [m["value"] for m in made]}
    if name == "takeoff_search_text":
        sheet = {g["page_no"]: g["sheet_no"] for g in p["pages"]}
        hits = api.search(pid, a["query"], "all" if a.get("all_sheets") else "included", user)
        return {"hits": [{"sheet": sheet.get(h["page_no"]), "count": len(h["rects"]), "places": h["rects"][:50]} for h in hits]}
    if name == "takeoff_summary":
        return {"takeoff": api.summary(pid, user)}
    if name == "takeoff_export_table":
        from ... import apiv1
        with connection() as conn:
            bases = apiv1.list_bases(conn, user)
        base = next((b for b in bases if not a.get("base") or a["base"] in (b["id"], b["name"])), None)
        if not base:
            raise HTTPException(404, f"No base {a.get('base')}")
        return api.export_table(pid, api.ToTable(base_id=base["id"], name=a.get("name") or ""), user)
    raise HTTPException(400, f"Unknown tool {name}")


def _parse_scale(text: str):
    from . import drawings
    t = " ".join(text.split())
    m = drawings.ARCH_RE.search(t)
    if m:
        paper = drawings._num(m.group(1))
        real = drawings._num(m.group(2)) + (drawings._num(m.group(3)) / 12 if m.group(3) else 0)
        return real / (paper * 72), t
    m = drawings.RATIO_RE.search(t)
    if m:
        return int(m.group(1)) / 72 / 12, t
    raise HTTPException(400, "Write the scale like 1/4\" = 1'-0\", 1\" = 20' or 1:100 (not " + text + ")")
