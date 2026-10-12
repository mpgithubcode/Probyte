"""The takeoff API (/api/x/takeoff/…): projects (a drawing set), its sheets, items (what's counted or measured),
marks on the sheets, and the takeoff (summary and exports).

Automatic results (sheet numbers, scales, legend entries, symbols and labels found on a sheet) are stored as
suggestions; the person confirms, corrects or rejects them. Only confirmed marks are in the takeoff.
"""
import csv
import io
import json
import math
import re
import shutil
import uuid
from pathlib import Path

import fitz
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from ... import files, store
from ...db import connection
from ...routes.common import User
from . import drawings, symbols

router = APIRouter()
DIR = files.ATTACH_DIR / "takeoff"
MAX_BYTES = 300 * 1024 * 1024
MAX_PAGES = 2000
DPIS = (50, 100, 150, 200, 300)
COLORS = ["#e5484d", "#3e63dd", "#30a46c", "#f76b15", "#8e4ec6", "#12a594", "#d6409f", "#ffb224", "#0091ff", "#978365"]


# ---------------------------------------------------------------- helpers

def _pdf(pid) -> Path:
    return DIR / f"{pid}.pdf"


def _open(pid):
    p = _pdf(pid)
    if not p.exists():
        raise HTTPException(404, "The drawing set's PDF is missing")
    return fitz.open(p)


def _project(conn, pid, user):
    p = conn.execute("SELECT * FROM tk_projects WHERE id = %s AND user_id = %s", (pid, user["id"])).fetchone()
    return p or store.not_found("Project not found")


def _item(conn, iid, user):
    it = conn.execute("SELECT i.* FROM tk_items i JOIN tk_projects p ON p.id = i.project_id WHERE i.id = %s AND p.user_id = %s",
                      (iid, user["id"])).fetchone()
    return it or store.not_found("Item not found")


def _page(conn, pid, n):
    pg = conn.execute("SELECT * FROM tk_pages WHERE project_id = %s AND page_no = %s", (pid, n)).fetchone()
    return pg or store.not_found("Sheet not found")


def _length(points) -> float:
    return sum(math.dist(points[i], points[i + 1]) for i in range(len(points) - 1))


def _area(points) -> float:
    n = len(points)
    return abs(sum(points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1] for i in range(n))) / 2


def _value(geom: dict, ft_per_pt: float | None) -> float:
    t, pts = geom.get("type"), geom.get("points") or []
    if t == "point":
        return 1
    if not ft_per_pt:
        return 0
    if t == "line":
        return round(_length(pts) * ft_per_pt, 3)
    if t == "area":
        return round(_area(pts) * ft_per_pt ** 2, 3)
    return 0


def _ft_per_pt(pg) -> float | None:
    return (pg["scale"] or {}).get("ft_per_pt") if pg else None


def _touch(conn, pid):
    conn.execute("UPDATE tk_projects SET updated_at = now() WHERE id = %s", (pid,))


def _page_out(pg) -> dict:
    return {k: pg[k] for k in ("page_no", "sheet_no", "title", "discipline", "included", "confirmed", "width", "height", "scale",
                               "scales", "excluded", "has_text")}


def _item_out(it) -> dict:
    out = {k: it[k] for k in ("name", "category", "kind", "unit", "color", "tag", "symbol", "details", "status", "waste_pct",
                              "per_run", "unit_cost", "notes", "position")}
    out["id"] = str(it["id"])
    return out


def _mark_out(m) -> dict:
    return {"id": str(m["id"]), "item_id": str(m["item_id"]), "page_no": m["page_no"], "geom": m["geom"], "value": m["value"],
            "status": m["status"], "source": m["source"], "score": m["score"]}


# ---------------------------------------------------------------- projects

def _index(pid, doc) -> list[dict]:
    """Every page: which sheet it is (title block, drawing index), its scales, whether it has text."""
    index = drawings.sheet_index(doc)
    out = []
    for page in doc:
        info = drawings.read_sheet(page, index)
        scales = drawings.scales_on(page)
        out.append({"page_no": page.number, **info, "width": page.rect.width, "height": page.rect.height,
                    "scales": [{"label": s["label"], "ft_per_pt": s["ft_per_pt"], "rect": s["rect"]} for s in scales[:6]],
                    "scale": {"ft_per_pt": scales[0]["ft_per_pt"], "label": scales[0]["label"], "source": "text"} if scales else None,
                    "has_text": bool(page.get_text("text").strip())})
    return out


def _create(user, name: str, data_path: Path, file_name: str, source: dict) -> dict:
    try:
        doc = fitz.open(data_path)
    except Exception:
        data_path.unlink(missing_ok=True)
        raise HTTPException(400, "That isn't a PDF that can be read")
    if doc.needs_pass:
        raise HTTPException(400, "The PDF is password protected")
    if doc.page_count > MAX_PAGES:
        raise HTTPException(400, f"That's more than {MAX_PAGES} pages")
    pid = uuid.uuid4()
    pages = _index(pid, doc)
    doc.close()
    DIR.mkdir(parents=True, exist_ok=True)
    shutil.move(str(data_path), _pdf(pid))
    with connection() as conn:
        conn.execute("INSERT INTO tk_projects (id, user_id, name, file_name, source, page_count) VALUES (%s,%s,%s,%s,%s,%s)",
                     (pid, user["id"], name.strip() or file_name, file_name, Jsonb(source), len(pages)))
        for p in pages:
            conn.execute("INSERT INTO tk_pages (project_id, page_no, sheet_no, title, discipline, width, height, scale, scales, has_text) "
                         "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                         (pid, p["page_no"], p["sheet_no"], p["title"], p["discipline"], p["width"], p["height"],
                          Jsonb(p["scale"]) if p["scale"] else None, Jsonb(p["scales"]), p["has_text"]))
    return get_project(str(pid), user)


@router.get("/projects")
def list_projects(user=User):
    with connection() as conn:
        rows = conn.execute("SELECT p.*, (SELECT count(*) FROM tk_items i WHERE i.project_id = p.id AND i.status = 'confirmed') AS items, "
                            "(SELECT count(*) FROM tk_pages g WHERE g.project_id = p.id AND g.included) AS included "
                            "FROM tk_projects p WHERE user_id = %s ORDER BY updated_at DESC", (user["id"],)).fetchall()
    return [{"id": str(r["id"]), "name": r["name"], "file_name": r["file_name"], "page_count": r["page_count"], "items": r["items"],
             "included": r["included"], "updated_at": r["updated_at"]} for r in rows]


@router.post("/projects")
async def upload_project(file: UploadFile = File(...), name: str = Form(""), user=User):
    """A new project from a PDF drawing set."""
    from starlette.concurrency import run_in_threadpool
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = DIR / f".upload-{uuid.uuid4()}.pdf"
    size = 0
    with open(tmp, "wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                out.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, "That PDF is too large (300 MB at most)")
            out.write(chunk)
    return await run_in_threadpool(_create, user, name, tmp, file.filename or "drawings.pdf", {"kind": "upload"})


class FromFile(BaseModel):
    record_id: str
    name: str = ""


@router.post("/projects/from-file")
def project_from_file(body: FromFile, user=User):
    """A new project from a PDF in one of your files tables (S3 / WebDAV)."""
    with connection() as conn:
        table, rec, keys = files._own_record(conn, body.record_id, user["id"])
        item = (rec["data"].get(keys["file"]) or [{}])[0]
        drv = files._storage(conn, table)
    if not (item.get("name") or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Choose a PDF")
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = DIR / f".upload-{uuid.uuid4()}.pdf"
    _, _, chunks = drv.get(rec["caldav_href"])
    size = 0
    with open(tmp, "wb") as out:
        for chunk in chunks:
            size += len(chunk)
            if size > MAX_BYTES:
                out.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, "That PDF is too large (300 MB at most)")
            out.write(chunk)
    return _create(user, body.name, tmp, item.get("name") or "drawings.pdf",
                   {"kind": "files", "record_id": str(rec["id"]), "table_id": str(table["id"])})


@router.get("/projects/{pid}")
def get_project(pid: str, user=User):
    with connection() as conn:
        p = _project(conn, pid, user)
        pages = conn.execute("SELECT * FROM tk_pages WHERE project_id = %s ORDER BY page_no", (pid,)).fetchall()
        items = conn.execute("SELECT * FROM tk_items WHERE project_id = %s ORDER BY position, created_at", (pid,)).fetchall()
    return {"id": str(p["id"]), "name": p["name"], "file_name": p["file_name"], "source": p["source"], "page_count": p["page_count"],
            "settings": p["settings"], "pages": [_page_out(g) for g in pages], "items": [_item_out(i) for i in items],
            "summary": summary(pid, user)}


class ProjectIn(BaseModel):
    name: str | None = None
    settings: dict | None = None


@router.patch("/projects/{pid}")
def update_project(pid: str, body: ProjectIn, user=User):
    with connection() as conn:
        _project(conn, pid, user)
        if body.name is not None and body.name.strip():
            conn.execute("UPDATE tk_projects SET name = %s WHERE id = %s", (body.name.strip(), pid))
        if body.settings is not None:
            conn.execute("UPDATE tk_projects SET settings = settings || %s WHERE id = %s", (Jsonb(body.settings), pid))
        _touch(conn, pid)
    return {"ok": True}


@router.delete("/projects/{pid}")
def delete_project(pid: str, user=User):
    with connection() as conn:
        _project(conn, pid, user)
        conn.execute("DELETE FROM tk_projects WHERE id = %s", (pid,))
    _pdf(pid).unlink(missing_ok=True)
    shutil.rmtree(DIR / "cache" / pid, ignore_errors=True)
    return {"ok": True}


@router.get("/projects/{pid}/pdf")
def project_pdf(pid: str, user=User):
    with connection() as conn:
        p = _project(conn, pid, user)
    return FileResponse(_pdf(pid), media_type="application/pdf", filename=p["file_name"], content_disposition_type="inline")


# ---------------------------------------------------------------- sheets

@router.get("/projects/{pid}/pages/{n}/image")
def page_image(pid: str, n: int, dpi: int = 100, user=User):
    """A picture of a sheet (PNG; kept, so it's quick the next time)."""
    with connection() as conn:
        _project(conn, pid, user)
    dpi = min(DPIS, key=lambda d: abs(d - dpi))
    cache = DIR / "cache" / pid / f"p{n}-{dpi}.png"
    if not cache.exists():
        doc = _open(pid)
        if not 0 <= n < doc.page_count:
            raise HTTPException(404, "Sheet not found")
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        drawings.render(doc[n], dpi).save(tmp, output="png")
        tmp.replace(cache)
    return FileResponse(cache, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


class PageIn(BaseModel):
    sheet_no: str | None = None
    title: str | None = None
    discipline: str | None = None
    included: bool | None = None
    confirmed: bool | None = None
    scale: dict | None = None        # {"ft_per_pt", "label"} or {} for none
    excluded: list[list[float]] | None = None


def _rescale(conn, pid, n, ft_per_pt):
    for m in conn.execute("SELECT id, geom FROM tk_marks WHERE project_id = %s AND page_no = %s AND geom->>'type' <> 'point'", (pid, n)).fetchall():
        conn.execute("UPDATE tk_marks SET value = %s WHERE id = %s", (_value(m["geom"], ft_per_pt), m["id"]))


@router.get("/projects/{pid}/pages/{n}/crop")
def page_crop(pid: str, n: int, rect: str, h: int = 60, user=User):
    """A small picture of part of a sheet (a legend symbol), h pixels high."""
    with connection() as conn:
        _project(conn, pid, user)
    try:
        r = [float(x) for x in rect.split(",")]
        assert len(r) == 4 and r[2] > r[0] and r[3] > r[1]
    except (ValueError, AssertionError):
        raise HTTPException(400, "rect is x0,y0,x1,y1")
    dpi = max(36, min(600, int(72 * min(h, 200) / (r[3] - r[1]))))
    pix = drawings.render(_open(pid)[n], dpi, clip=r)
    return Response(pix.tobytes("png"), media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.patch("/projects/{pid}/pages/{n}")
def update_page(pid: str, n: int, body: PageIn, user=User):
    with connection() as conn:
        _project(conn, pid, user)
        _page(conn, pid, n)
        sets, vals = [], []
        for k in ("sheet_no", "title", "discipline", "included", "confirmed"):
            v = getattr(body, k)
            if v is not None:
                sets.append(f"{k} = %s")
                vals.append(v.strip() or None if isinstance(v, str) else v)
        if body.scale is not None:
            sc = body.scale if body.scale.get("ft_per_pt") else None
            if sc and not (0 < float(sc["ft_per_pt"]) < 1e4):
                raise HTTPException(400, "That scale isn't possible")
            sets.append("scale = %s")
            vals.append(Jsonb({"ft_per_pt": float(sc["ft_per_pt"]), "label": sc.get("label") or "", "source": sc.get("source") or "manual"}) if sc else None)
            _rescale(conn, pid, n, float(sc["ft_per_pt"]) if sc else None)
        if body.excluded is not None:
            sets.append("excluded = %s")
            vals.append(Jsonb([[float(x) for x in r[:4]] for r in body.excluded if len(r) >= 4]))
        if sets:
            conn.execute(f"UPDATE tk_pages SET {', '.join(sets)} WHERE project_id = %s AND page_no = %s", (*vals, pid, n))
            _touch(conn, pid)
        return _page_out(_page(conn, pid, n))


class PagesIn(BaseModel):
    pages: list[int]
    included: bool | None = None
    confirmed: bool | None = None


@router.patch("/projects/{pid}/pages")
def update_pages(pid: str, body: PagesIn, user=User):
    """Several sheets at once (e.g. all electrical sheets in the takeoff)."""
    with connection() as conn:
        _project(conn, pid, user)
        for k in ("included", "confirmed"):
            v = getattr(body, k)
            if v is not None:
                conn.execute(f"UPDATE tk_pages SET {k} = %s WHERE project_id = %s AND page_no = ANY(%s)", (v, pid, body.pages))
        _touch(conn, pid)
    return {"ok": True}


class Calibrate(BaseModel):
    points: list[list[float]]
    feet: float


@router.post("/projects/{pid}/pages/{n}/calibrate")
def calibrate(pid: str, n: int, body: Calibrate, user=User):
    """The scale from two points a known distance apart (a dimension on the drawing)."""
    if len(body.points) != 2 or body.feet <= 0:
        raise HTTPException(400, "Click two points and enter the distance between them")
    d = math.dist(body.points[0], body.points[1])
    if d < 2:
        raise HTTPException(400, "The two points are too close together")
    ft = body.feet
    label = f"calibrated: {int(ft)}'-{round((ft - int(ft)) * 12, 2):g}\" over {d / 72:.2f}\""
    return update_page(pid, n, PageIn(scale={"ft_per_pt": ft / d, "label": label, "source": "calibrated"}), user)


@router.get("/projects/{pid}/search")
def search(pid: str, q: str, pages: str = "included", user=User):
    """Text on the sheets (all sheets, or the ones in the takeoff): [{"page_no", "rects": [...]}]."""
    q = q.strip()
    if len(q) < 1:
        return []
    with connection() as conn:
        _project(conn, pid, user)
        rows = conn.execute("SELECT page_no FROM tk_pages WHERE project_id = %s" + (" AND included" if pages == "included" else "")
                            + " ORDER BY page_no", (pid,)).fetchall()
    doc = _open(pid)
    out = []
    for r in rows:
        hits = drawings.search(doc[r["page_no"]], q)
        if hits:
            out.append({"page_no": r["page_no"], "rects": hits[:500]})
    return out


@router.get("/projects/{pid}/pages/{n}/legends")
def page_legends(pid: str, n: int, user=User):
    """Legends and schedules on a sheet, as suggested items (nothing is saved until they're added)."""
    with connection() as conn:
        _project(conn, pid, user)
    return drawings.legends(_open(pid)[n])


class Region(BaseModel):
    rect: list[float]
    heading: str | None = None


@router.post("/projects/{pid}/pages/{n}/legend-in")
def legend_in(pid: str, n: int, body: Region, user=User):
    """The entries of a legend boxed by hand."""
    with connection() as conn:
        _project(conn, pid, user)
    return drawings.legend_in(_open(pid)[n], body.rect, body.heading)


@router.get("/projects/{pid}/pages/{n}/tables")
def page_tables(pid: str, n: int, user=User):
    with connection() as conn:
        _project(conn, pid, user)
    return drawings.tables_on(_open(pid)[n])


@router.get("/projects/{pid}/pages/{n}/words")
def page_words(pid: str, n: int, user=User):
    """The sheet's words with where they are (for picking a label to count, or a dimension)."""
    with connection() as conn:
        _project(conn, pid, user)
    return [[round(w[0], 1), round(w[1], 1), round(w[2], 1), round(w[3], 1), w[4]] for w in drawings.words(_open(pid)[n])]


# ---------------------------------------------------------------- items

class ItemIn(BaseModel):
    name: str | None = None
    category: str | None = None
    kind: str | None = None          # count | length | area
    unit: str | None = None
    color: str | None = None
    tag: str | None = None
    symbol: dict | None = None       # {"page", "rect", "legend"?}: its picture (and the legend it's in)
    details: dict | None = None
    status: str | None = None        # suggested | confirmed
    waste_pct: float | None = None
    per_run: float | None = None
    unit_cost: float | None = None
    notes: str | None = None
    position: int | None = None


UNITS = {"count": "EA", "length": "LF", "area": "SF"}


def _clean_item(body: ItemIn, partial: bool) -> dict:
    d = {k: v for k, v in body.model_dump().items() if v is not None or (partial and k in body.model_fields_set)}
    if "kind" in d and d["kind"] not in UNITS:
        raise HTTPException(400, "An item is counted (count), measured along a run (length) or over an area (area)")
    if "status" in d and d["status"] not in ("suggested", "confirmed"):
        raise HTTPException(400, "Unknown status")
    if "color" in d and d["color"] and not re.match(r"^#[0-9a-fA-F]{6}$", d["color"]):
        raise HTTPException(400, "A color is written #rrggbb")
    if "symbol" in d and d["symbol"]:
        s = d["symbol"]
        if not isinstance(s.get("page"), int) or len(s.get("rect") or []) != 4:
            raise HTTPException(400, "A symbol is a page and a rectangle")
        d["symbol"] = {"page": s["page"], "rect": [float(x) for x in s["rect"]]}
        if len(s.get("legend") or []) == 4:  # the legend it's from: left out when looking for things on that sheet
            d["symbol"]["legend"] = [float(x) for x in s["legend"]]
    for k in ("name", "category", "tag", "unit", "notes"):
        if isinstance(d.get(k), str):
            d[k] = d[k].strip() or None
    if not partial and not d.get("name"):
        raise HTTPException(400, "Give the item a name")
    return d


class ItemsIn(BaseModel):
    items: list[ItemIn]


@router.post("/projects/{pid}/items")
def add_items(pid: str, body: ItemsIn, user=User):
    """Add items (several at once: the entries of a legend)."""
    out = []
    with connection() as conn:
        _project(conn, pid, user)
        n = conn.execute("SELECT count(*) AS n FROM tk_items WHERE project_id = %s", (pid,)).fetchone()["n"]
        for i, it in enumerate(body.items):
            d = _clean_item(it, False)
            kind = d.get("kind") or "count"
            row = conn.execute(
                "INSERT INTO tk_items (project_id, name, category, kind, unit, color, tag, symbol, details, status, waste_pct, per_run, "
                "unit_cost, notes, position) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (pid, d["name"], d.get("category"), kind, d.get("unit") or UNITS[kind], d.get("color") or COLORS[(n + i) % len(COLORS)],
                 d.get("tag"), Jsonb(d["symbol"]) if d.get("symbol") else None, Jsonb(d.get("details") or {}), d.get("status") or "confirmed",
                 d.get("waste_pct") or 0, d.get("per_run") or 0, d.get("unit_cost"), d.get("notes"), n + i)).fetchone()
            out.append(_item_out(row))
        _touch(conn, pid)
    return out


@router.patch("/items/{iid}")
def update_item(iid: str, body: ItemIn, user=User):
    with connection() as conn:
        it = _item(conn, iid, user)
        d = _clean_item(body, True)
        if d.get("kind") and d["kind"] != it["kind"] and "unit" not in d:
            d["unit"] = UNITS[d["kind"]]
        if d:
            cols = ", ".join(f"{k} = %s" for k in d)
            conn.execute(f"UPDATE tk_items SET {cols} WHERE id = %s",
                         (*[Jsonb(v) if k in ("symbol", "details") and v is not None else v for k, v in d.items()], iid))
            _touch(conn, it["project_id"])
        return _item_out(_item(conn, iid, user))


@router.delete("/items/{iid}")
def delete_item(iid: str, user=User):
    with connection() as conn:
        it = _item(conn, iid, user)
        conn.execute("DELETE FROM tk_items WHERE id = %s", (iid,))
        _touch(conn, it["project_id"])
    return {"ok": True}


@router.get("/items/{iid}/symbol.png")
def symbol_png(iid: str, user=User):
    with connection() as conn:
        it = _item(conn, iid, user)
    if not it["symbol"]:
        raise HTTPException(404, "No symbol")
    doc = _open(it["project_id"])
    pix = drawings.render(doc[it["symbol"]["page"]], 200, clip=it["symbol"]["rect"])
    return Response(pix.tobytes("png"), media_type="image/png", headers={"Cache-Control": "private, max-age=60"})


class FindIn(BaseModel):
    pages: list[int] | None = None   # default: the sheets in the takeoff
    method: str = "auto"             # symbol | tag | auto (the label if it has one, else the symbol)
    threshold: float = 0.75
    rotate: bool = True


@router.post("/items/{iid}/find")
def find_item(iid: str, body: FindIn, user=User):
    """Look for an item on sheets (its symbol, or its label). The finds are suggestions to review; finding again
    replaces the earlier suggestions (accepted and rejected ones stay)."""
    with connection() as conn:
        it = _item(conn, iid, user)
        pid = str(it["project_id"])
        q = "SELECT * FROM tk_pages WHERE project_id = %s AND " + ("page_no = ANY(%s)" if body.pages is not None else "included")
        pages = conn.execute(q + " ORDER BY page_no", (pid, body.pages) if body.pages is not None else (pid,)).fetchall()
        others = conn.execute("SELECT symbol FROM tk_items WHERE project_id = %s AND symbol IS NOT NULL", (pid,)).fetchall()
        kept = conn.execute("SELECT page_no, geom FROM tk_marks WHERE item_id = %s AND status <> 'suggested'", (iid,)).fetchall()
    method = body.method if body.method != "auto" else ("tag" if it["tag"] else "symbol")
    if method == "tag" and not it["tag"]:
        raise HTTPException(400, "This item has no label to look for: enter its label (e.g. A or EM-2)")
    if method == "symbol" and not it["symbol"]:
        raise HTTPException(400, "This item has no symbol yet: box its symbol in the legend")
    if not pages:
        raise HTTPException(400, "No sheets to look on: include sheets in the takeoff first")
    doc = _open(pid)
    tpl = None
    if method == "symbol":
        tpl = symbols.symbol_picture(doc[it["symbol"]["page"]], it["symbol"]["rect"])
        if tpl is None:
            raise HTTPException(400, "Nothing is drawn in the symbol's box: box it again")
    threshold = min(max(body.threshold, 0.4), 0.99)
    results = []
    for pg in pages:
        page = doc[pg["page_no"]]
        # left out: the sheet's left-out areas, and the legend pictures themselves
        excluded = list(pg["excluded"] or [])
        excluded += [o["symbol"].get("legend") or o["symbol"]["rect"] for o in others
                     if o["symbol"] and o["symbol"]["page"] == pg["page_no"]]
        if method == "tag":
            finds = drawings.find_tags(page, it["tag"], excluded)
        else:
            finds = [f for f in symbols.find(symbols.sheet_picture(page), tpl, threshold, body.rotate)
                     if not drawings._inside(f["point"], excluded)]
        # not where something is already accepted or rejected
        taken = [m["geom"]["points"][0] for m in kept if m["page_no"] == pg["page_no"] and m["geom"]["type"] == "point"]
        finds = [f for f in finds if not any(math.dist(f["point"], t) < 6 for t in taken)]
        with connection() as conn:
            conn.execute("DELETE FROM tk_marks WHERE item_id = %s AND page_no = %s AND status = 'suggested'", (iid, pg["page_no"]))
            for f in finds:
                conn.execute("INSERT INTO tk_marks (item_id, project_id, page_no, geom, value, status, source, score) "
                             "VALUES (%s,%s,%s,%s,1,'suggested',%s,%s)",
                             (iid, pid, pg["page_no"], Jsonb({"type": "point", "points": [f["point"]]}), method, f["score"]))
        results.append({"page_no": pg["page_no"], "sheet_no": pg["sheet_no"], "found": len(finds)})
    with connection() as conn:
        _touch(conn, pid)
    return {"method": method, "pages": results, "found": sum(r["found"] for r in results)}


# ---------------------------------------------------------------- marks

@router.get("/projects/{pid}/marks")
def list_marks(pid: str, page: int | None = None, item: str | None = None, user=User):
    with connection() as conn:
        _project(conn, pid, user)
        q, args = "SELECT * FROM tk_marks WHERE project_id = %s", [pid]
        if page is not None:
            q += " AND page_no = %s"
            args.append(page)
        if item:
            q += " AND item_id = %s"
            args.append(item)
        rows = conn.execute(q + " ORDER BY created_at", args).fetchall()
    return [_mark_out(m) for m in rows]


class MarkIn(BaseModel):
    item_id: str
    page_no: int
    geom: dict
    status: str = "confirmed"
    source: str = "manual"


def _check_geom(g: dict):
    t, pts = g.get("type"), g.get("points")
    need = {"point": 1, "line": 2, "area": 3}.get(t)
    if not need or not isinstance(pts, list) or len(pts) < need or len(pts) > 5000 \
            or not all(isinstance(p, list) and len(p) == 2 and all(isinstance(c, (int, float)) for c in p) for p in pts):
        raise HTTPException(400, "A mark is a point, a line (2 points or more) or an area (3 points or more)")
    return {"type": t, "points": [[round(float(p[0]), 2), round(float(p[1]), 2)] for p in pts]}


@router.post("/projects/{pid}/marks")
def add_mark(pid: str, body: MarkIn, user=User):
    with connection() as conn:
        _project(conn, pid, user)
        it = _item(conn, body.item_id, user)
        if str(it["project_id"]) != pid:
            raise HTTPException(400, "That item belongs to another project")
        pg = _page(conn, pid, body.page_no)
        g = _check_geom(body.geom)
        m = conn.execute("INSERT INTO tk_marks (item_id, project_id, page_no, geom, value, status, source) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                         (body.item_id, pid, body.page_no, Jsonb(g), _value(g, _ft_per_pt(pg)),
                          body.status if body.status in ("confirmed", "suggested") else "confirmed",
                          body.source if body.source in ("manual", "search", "tag", "symbol") else "manual")).fetchone()
        _touch(conn, pid)
    return _mark_out(m)


class MarkPatch(BaseModel):
    status: str | None = None
    geom: dict | None = None
    item_id: str | None = None


def _mark(conn, mid, user):
    m = conn.execute("SELECT m.* FROM tk_marks m JOIN tk_projects p ON p.id = m.project_id WHERE m.id = %s AND p.user_id = %s",
                     (mid, user["id"])).fetchone()
    return m or store.not_found("Mark not found")


@router.patch("/marks/{mid}")
def update_mark(mid: str, body: MarkPatch, user=User):
    with connection() as conn:
        m = _mark(conn, mid, user)
        if body.status is not None:
            if body.status not in ("confirmed", "rejected", "suggested"):
                raise HTTPException(400, "Unknown status")
            conn.execute("UPDATE tk_marks SET status = %s WHERE id = %s", (body.status, mid))
        if body.geom is not None:
            g = _check_geom(body.geom)
            conn.execute("UPDATE tk_marks SET geom = %s, value = %s WHERE id = %s",
                         (Jsonb(g), _value(g, _ft_per_pt(_page(conn, m["project_id"], m["page_no"]))), mid))
        if body.item_id is not None:
            it = _item(conn, body.item_id, user)
            if it["project_id"] != m["project_id"]:
                raise HTTPException(400, "That item belongs to another project")
            conn.execute("UPDATE tk_marks SET item_id = %s WHERE id = %s", (body.item_id, mid))
        _touch(conn, m["project_id"])
        return _mark_out(_mark(conn, mid, user))


@router.delete("/marks/{mid}")
def delete_mark(mid: str, user=User):
    with connection() as conn:
        m = _mark(conn, mid, user)
        conn.execute("DELETE FROM tk_marks WHERE id = %s", (mid,))
        _touch(conn, m["project_id"])
    return {"ok": True}


class Review(BaseModel):
    ids: list[str] | None = None
    item_id: str | None = None
    page_no: int | None = None
    min_score: float | None = None     # with item_id: every suggestion at least this alike
    status: str                        # confirmed | rejected


@router.post("/projects/{pid}/marks/review")
def review(pid: str, body: Review, user=User):
    """Accept or reject suggestions at once (chosen ones, or an item's suggestions above a score)."""
    if body.status not in ("confirmed", "rejected"):
        raise HTTPException(400, "Accept (confirmed) or reject (rejected)")
    with connection() as conn:
        _project(conn, pid, user)
        q, args = "UPDATE tk_marks SET status = %s WHERE project_id = %s AND status = 'suggested'", [body.status, pid]
        if body.ids is not None:
            q += " AND id = ANY(%s::uuid[])"
            args.append(body.ids)
        if body.item_id:
            q += " AND item_id = %s"
            args.append(body.item_id)
        if body.page_no is not None:
            q += " AND page_no = %s"
            args.append(body.page_no)
        if body.min_score is not None:
            q += " AND COALESCE(score, 1) >= %s"
            args.append(body.min_score)
        if body.ids is None and not body.item_id:
            raise HTTPException(400, "Choose the suggestions or the item")
        n = conn.execute(q, args).rowcount
        _touch(conn, pid)
    return {"changed": n}


# ---------------------------------------------------------------- the takeoff

def summary(pid: str, user) -> list[dict]:
    """Per item: quantity per sheet and in all, with waste, cost; how many suggestions are still to review."""
    with connection() as conn:
        _project(conn, pid, user)
        items = conn.execute("SELECT * FROM tk_items WHERE project_id = %s AND status = 'confirmed' ORDER BY position, created_at", (pid,)).fetchall()
        rows = conn.execute("SELECT m.item_id, m.page_no, m.status, count(*) AS n, sum(m.value) AS v FROM tk_marks m "
                            "WHERE m.project_id = %s GROUP BY 1, 2, 3", (pid,)).fetchall()
        pages = {p["page_no"]: p for p in conn.execute("SELECT page_no, sheet_no, scale FROM tk_pages WHERE project_id = %s", (pid,)).fetchall()}
    out = []
    for it in items:
        per_page, pending, unscaled = [], 0, []
        for r in rows:
            if r["item_id"] != it["id"]:
                continue
            if r["status"] == "suggested":
                pending += r["n"]
                continue
            if r["status"] != "confirmed":
                continue
            pg = pages.get(r["page_no"]) or {}
            if it["kind"] == "count":
                qty = r["n"]
            else:
                qty = (r["v"] or 0) + (it["per_run"] or 0) * r["n"] * (it["kind"] == "length")
                if not (pg.get("scale") or {}).get("ft_per_pt"):
                    unscaled.append(pg.get("sheet_no") or f"page {r['page_no'] + 1}")
            per_page.append({"page_no": r["page_no"], "sheet_no": pg.get("sheet_no"), "qty": round(qty, 2), "marks": r["n"]})
        per_page.sort(key=lambda p: p["page_no"])
        qty = round(sum(p["qty"] for p in per_page), 2)
        total = qty * (1 + (it["waste_pct"] or 0) / 100)
        total = math.ceil(total - 1e-9) if it["kind"] == "count" else round(total, 2)
        out.append({"item_id": str(it["id"]), "name": it["name"], "category": it["category"], "kind": it["kind"], "unit": it["unit"],
                    "tag": it["tag"], "color": it["color"], "qty": qty, "waste_pct": it["waste_pct"], "total": total,
                    "unit_cost": it["unit_cost"], "extended": round(total * it["unit_cost"], 2) if it["unit_cost"] is not None else None,
                    "pages": per_page, "pending": pending, "unscaled": unscaled, "details": it["details"], "notes": it["notes"]})
    return out


@router.get("/projects/{pid}/summary")
def get_summary(pid: str, user=User):
    return summary(pid, user)


def _sheets(s: dict) -> str:
    return ", ".join(f"{p['sheet_no'] or 'p' + str(p['page_no'] + 1)}: {p['qty']:g}" for p in s["pages"])


@router.get("/projects/{pid}/export.csv")
def export_csv(pid: str, user=User):
    with connection() as conn:
        p = _project(conn, pid, user)
    rows = summary(pid, user)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Item", "Category", "Label", "Quantity", "Unit", "Waste %", "Total", "Unit cost", "Extended", "By sheet", "Details", "Notes"])
    for s in rows:
        w.writerow([s["name"], s["category"] or "", s["tag"] or "", f"{s['qty']:g}", s["unit"], f"{s['waste_pct']:g}", f"{s['total']:g}",
                    "" if s["unit_cost"] is None else f"{s['unit_cost']:g}", "" if s["extended"] is None else f"{s['extended']:.2f}",
                    _sheets(s), "; ".join(f"{k}: {v}" for k, v in (s["details"] or {}).items()), s["notes"] or ""])
    slug = re.sub(r"[^A-Za-z0-9]+", "-", p["name"]).strip("-") or "takeoff"
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{slug}-takeoff.csv"'})


def _rgb(hex_color: str):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


@router.get("/projects/{pid}/export.pdf")
def export_pdf(pid: str, all_pages: bool = False, user=User):
    """The drawing set with the takeoff drawn on it: each mark in its item's color, and a key with the quantities on
    every sheet that has marks."""
    with connection() as conn:
        p = _project(conn, pid, user)
        items = {str(i["id"]): i for i in conn.execute("SELECT * FROM tk_items WHERE project_id = %s AND status = 'confirmed'", (pid,)).fetchall()}
        marks = conn.execute("SELECT * FROM tk_marks WHERE project_id = %s AND status = 'confirmed' ORDER BY page_no", (pid,)).fetchall()
        included = [r["page_no"] for r in conn.execute("SELECT page_no FROM tk_pages WHERE project_id = %s AND included ORDER BY page_no", (pid,)).fetchall()]
    doc = _open(pid)
    by_page: dict[int, list] = {}
    for m in marks:
        if str(m["item_id"]) in items:
            by_page.setdefault(m["page_no"], []).append(m)
    keep = sorted(set(included) | set(by_page)) if not all_pages else list(range(doc.page_count))
    for n, ms in by_page.items():
        page = doc[n]
        page.wrap_contents()  # (drawing over pages with odd content transforms)
        shape = page.new_shape()
        counts: dict[str, list] = {}
        for m in ms:
            it = items[str(m["item_id"])]
            col = _rgb(it["color"])
            pts = [fitz.Point(*pt) for pt in m["geom"]["points"]]
            if m["geom"]["type"] == "point":
                shape.draw_circle(pts[0], 7)
                shape.finish(color=col, fill=col, fill_opacity=0.25, width=1.6)
            elif m["geom"]["type"] == "line":
                shape.draw_polyline(pts)
                shape.finish(color=col, width=2.2, closePath=False, stroke_opacity=0.85)
            else:
                shape.draw_polyline(pts + [pts[0]])
                shape.finish(color=col, fill=col, fill_opacity=0.18, width=1.6)
            c = counts.setdefault(str(m["item_id"]), [0, 0.0])
            c[0] += 1
            c[1] += m["value"]
        # the key, top left
        lines = []
        for iid, (n_marks, v) in counts.items():
            it = items[iid]
            qty = n_marks if it["kind"] == "count" else round(v + (it["per_run"] or 0) * n_marks * (it["kind"] == "length"), 1)
            lines.append((it, f"{it['name'][:48]}  {qty:g} {it['unit']}"))
        x, y = 24, 24
        h = 14 + 12 * len(lines)
        shape.draw_rect(fitz.Rect(x, y, x + 300, y + h))
        shape.finish(color=(0.3, 0.3, 0.3), fill=(1, 1, 1), fill_opacity=0.92, width=0.6)
        shape.insert_text(fitz.Point(x + 6, y + 11), f"TAKEOFF: {p['name'][:40]}", fontsize=7.5, fontname="hebo")
        for i, (it, text) in enumerate(lines):
            yy = y + 14 + 12 * i
            shape.draw_rect(fitz.Rect(x + 6, yy + 2, x + 14, yy + 10))
            shape.finish(color=_rgb(it["color"]), fill=_rgb(it["color"]))
            shape.insert_text(fitz.Point(x + 18, yy + 9), text, fontsize=7.5, fontname="helv")
        shape.commit()
    if keep and len(keep) < doc.page_count:
        doc.select(keep)
    data = doc.tobytes(garbage=3, deflate=True)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", p["name"]).strip("-") or "takeoff"
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{slug}-marked-up.pdf"'})


class ToTable(BaseModel):
    base_id: str
    name: str = ""


def _new_table(conn, user, base_id, name, columns: list[tuple[str, str, dict | None]]):
    """A Probyte table with these columns (the first is the title column, text)."""
    store.get_base(conn, base_id, user["id"])
    t = store.create_table(conn, user["id"], base_id, name)
    fields = store.get_fields(conn, t["id"])
    by_name = {f["name"]: f for f in fields}
    conn.execute("DELETE FROM fields WHERE id = %s", (by_name["Status"]["id"],))
    conn.execute("UPDATE fields SET name = %s WHERE id = %s", (columns[0][0], by_name["Name"]["id"]))
    conn.execute("UPDATE fields SET name = %s, position = 1000 WHERE id = %s", ("Notes", by_name["Notes"]["id"]))
    ids = [str(by_name["Name"]["id"])]
    for name_, type_, opts in columns[1:]:
        if name_ == "Notes":
            ids.append(str(by_name["Notes"]["id"]))
            continue
        ids.append(str(store.create_field(conn, t["id"], name_, type_, opts)["id"]))
    return t, ids


@router.post("/projects/{pid}/export/table")
def export_table(pid: str, body: ToTable, user=User):
    """The takeoff as a Probyte table (to price it, share it, track it like any other table)."""
    rows = summary(pid, user)
    money = {"numberFormat": "currency", "currency": "$", "precision": 2}
    cols = [("Item", "text", None), ("Category", "text", None), ("Label", "text", None), ("Quantity", "number", {"precision": 2}),
            ("Unit", "text", None), ("Waste %", "number", {"precision": 1}), ("Total", "number", {"precision": 2}),
            ("Unit cost", "number", money), ("Extended", "number", money), ("By sheet", "text", None),
            ("Details", "long_text", None), ("Notes", "long_text", None)]
    with connection() as conn:
        p = _project(conn, pid, user)
        t, ids = _new_table(conn, user, body.base_id, body.name.strip() or f"Takeoff – {p['name']}", cols)
        for s in rows:
            vals = [s["name"], s["category"], s["tag"], s["qty"], s["unit"], s["waste_pct"], s["total"], s["unit_cost"], s["extended"],
                    _sheets(s), "\n".join(f"{k}: {v}" for k, v in (s["details"] or {}).items()) or None, s["notes"]]
            store.insert_record(conn, t["id"], {fid: v for fid, v in zip(ids, vals) if v not in (None, "")}, user_id=user["id"])
    return {"table_id": str(t["id"]), "rows": len(rows)}


class TableToTable(ToTable):
    cells: list[list[str]]
    header: bool = True


@router.post("/projects/{pid}/export/schedule")
def export_schedule(pid: str, body: TableToTable, user=User):
    """A schedule from a sheet (fixture schedule, panel schedule…) as a Probyte table."""
    cells = [r for r in body.cells if any((c or "").strip() for c in r)]
    if len(cells) < 1 or not cells[0]:
        raise HTTPException(400, "The schedule is empty")
    width = max(len(r) for r in cells)
    head = cells[0] if body.header else [f"Column {i + 1}" for i in range(width)]
    names, seen = [], set()
    for i in range(width):
        n = (head[i] if i < len(head) else "").strip()[:60] or f"Column {i + 1}"
        while n in seen:
            n += " (2)"
        seen.add(n)
        names.append(n)
    with connection() as conn:
        p = _project(conn, pid, user)
        t, ids = _new_table(conn, user, body.base_id, body.name.strip() or f"Schedule – {p['name']}",
                            [(names[0], "text", None)] + [(n, "text", None) for n in names[1:]])
        for r in cells[1:] if body.header else cells:
            store.insert_record(conn, t["id"], {fid: v for fid, v in zip(ids, r) if (v or "").strip()}, user_id=user["id"])
    return {"table_id": str(t["id"]), "rows": len(cells) - (1 if body.header else 0)}


@router.get("/projects/{pid}/export.json", include_in_schema=False)
def export_json(pid: str, user=User):
    """Everything about the project (for a backup or another tool)."""
    proj = get_project(pid, user)
    proj["marks"] = list_marks(pid, user=user)
    return Response(json.dumps(proj, default=str), media_type="application/json")
