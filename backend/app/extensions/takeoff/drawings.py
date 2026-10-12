"""Reading a drawing set (PyMuPDF): which sheet each page is, its scale, its words, legends and schedules, and
pictures of it. Everything here is a suggestion: the takeoff screens show it for a person to confirm or correct.

Coordinates are PDF points (1/72 inch) from the page's top left, as PyMuPDF gives them.
"""
import re
from collections import Counter
from fractions import Fraction

import fitz  # PyMuPDF

# ---------------------------------------------------------------- sheets

# E1.0, A-101, LS.1, FP2.01, M1.0A
SHEET_RE = re.compile(r"^[A-Z]{1,3}[-.]?\d{1,3}(?:\.\d{1,2})?[A-Z]?$")
DISCIPLINES = [  # longest prefix first
    ("FA", "Fire alarm"), ("FP", "Fire protection"), ("LS", "Life safety"), ("EL", "Electrical"), ("ES", "Electrical site"),
    ("TC", "Telecom"), ("AV", "Audio / video"), ("SP", "Specifications"),
    ("A", "Architectural"), ("C", "Civil"), ("D", "Demolition"), ("E", "Electrical"), ("F", "Fire protection"),
    ("G", "General"), ("I", "Interiors"), ("L", "Landscape"), ("M", "Mechanical"), ("P", "Plumbing"),
    ("Q", "Equipment"), ("S", "Structural"), ("T", "Title / general"),
]


def discipline_of(sheet_no: str | None) -> str | None:
    if not sheet_no:
        return None
    letters = re.match(r"^[A-Z]+", sheet_no.upper())
    if not letters:
        return None
    for prefix, name in DISCIPLINES:
        if letters.group(0).startswith(prefix) and (len(prefix) > 1 or len(letters.group(0)) <= 2):
            return name
    return None


def _rows(words, tol=3.0):
    """Words (x0, y0, x1, y1, text, …) grouped into lines across the page (by their vertical middle)."""
    rows = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        mid = (w[1] + w[3]) / 2
        if rows and abs(rows[-1][0] - mid) <= tol:
            rows[-1][1].append(w)
        else:
            rows.append([mid, [w]])
    return [sorted(r[1], key=lambda w: w[0]) for r in rows]


def sheet_index(doc) -> dict[str, str]:
    """The drawing index (usually on the cover sheet): sheet number -> title, from lines like "E1.0 ELECTRICAL POWER PLAN"."""
    out = {}
    for page in doc:
        for row in _rows(page.get_text("words")):
            for i, w in enumerate(row[:-1]):
                if not SHEET_RE.match(w[4]):
                    continue
                title = []
                for nxt in row[i + 1:]:
                    if SHEET_RE.match(nxt[4]) or nxt[0] - (title[-1][2] if title else w[2]) > 40:
                        break
                    title.append(nxt)
                text = " ".join(t[4] for t in title).strip()
                if len(text) >= 4 and text.upper() == text and re.search(r"[A-Z]{3}", text) and w[4] not in out:
                    out[w[4]] = text
    return out


def read_sheet(page, index: dict[str, str]) -> dict:
    """Sheet number (the biggest sheet-number-like text in the title block), title and discipline of a page."""
    w, h = page.rect.width, page.rect.height
    best, best_score = None, 0.0
    for b in page.get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            for s in line["spans"]:
                t = s["text"].strip()
                if not SHEET_RE.match(t):
                    continue
                x0, y0, x1, y1 = s["bbox"]
                in_block = x0 > w * 0.78 or y0 > h * 0.82          # title blocks sit at the right or the bottom
                score = s["size"] * (3 if in_block else 1) * (1.5 if y0 > h * 0.6 else 1)
                if score > best_score:
                    best, best_score = (t, s["bbox"]), score
    sheet_no = best[0] if best else None
    title = index.get(sheet_no) if sheet_no else None
    if sheet_no and not title:  # the words after "TITLE:" in the title block
        words = [x for x in page.get_text("words") if x[0] > w * 0.78 or x[1] > h * 0.82]
        label = next((x for x in words if x[4].upper().startswith("TITLE")), None)
        if label:
            below = [x for x in words if label[1] - 2 <= x[1] <= label[3] + 40 and x[0] >= label[0] - 20 and x is not label
                     and not SHEET_RE.match(x[4]) and x[4].upper() not in ("TITLE:", "SHEET:")]
            title = " ".join(x[4] for x in sorted(below, key=lambda x: (round(x[1]), x[0])))[:120] or None
    return {"sheet_no": sheet_no, "title": title, "discipline": discipline_of(sheet_no)}


# ---------------------------------------------------------------- scales

_NUM = r"\d+(?:\s+\d+/\d+|/\d+|\.\d+)?"
ARCH_RE = re.compile(rf"({_NUM})\s*\"\s*=\s*({_NUM})\s*'(?:\s*-?\s*({_NUM})\s*\"?)?")   # 1/4" = 1'-0"   1" = 20'
RATIO_RE = re.compile(r"\b1\s*:\s*(\d{1,5})\b")                                      # 1:100


def _num(s: str) -> float:
    return float(sum(Fraction(p) for p in s.split()))


def scales_on(page) -> list[dict]:
    """The scales written on a sheet, most likely first: {"label", "ft_per_pt", "count", "rect"}."""
    found: dict[str, dict] = {}
    for b in page.get_text("blocks"):
        text = " ".join(b[4].split())
        for m in ARCH_RE.finditer(text):
            paper_in = _num(m.group(1))
            real_ft = _num(m.group(2)) + (_num(m.group(3)) / 12 if m.group(3) else 0)
            if paper_in <= 0 or real_ft <= 0:
                continue
            label = f'{m.group(1)}" = {m.group(2)}\'' + (f'-{m.group(3)}"' if m.group(3) is not None else "")
            e = found.setdefault(label, {"label": label, "ft_per_pt": real_ft / (paper_in * 72), "count": 0, "rect": list(b[:4])})
            e["count"] += 1
            e["rect"] = list(b[:4])  # the last one: plan titles tend to be low on the sheet
        for m in RATIO_RE.finditer(text):
            n = int(m.group(1))
            if n < 2:
                continue
            label = f"1:{n}"
            e = found.setdefault(label, {"label": label, "ft_per_pt": n / 72 / 12, "count": 0, "rect": list(b[:4])})
            e["count"] += 1
    plan_words = [x for x in page.get_text("words") if x[4].upper().rstrip(".:") == "PLAN"]

    def near_plan(e):  # a scale next to a "… PLAN" title is the plan's
        r = e["rect"]
        return min((abs(r[1] - p[1]) + abs(r[0] - p[0]) / 4 for p in plan_words), default=1e9)
    return sorted(found.values(), key=lambda e: (near_plan(e), -e["count"]))


# ---------------------------------------------------------------- words and search

TAG_RE = re.compile(r"^[A-Z]{1,3}(?:[-/]?[A-Z0-9]{1,4})?$")


def words(page) -> list[tuple]:
    return page.get_text("words")


def search(page, query: str) -> list[list[float]]:
    return [list(r) for r in page.search_for(query)]


def _inside(pt, rects) -> bool:
    return any(r[0] <= pt[0] <= r[2] and r[1] <= pt[1] <= r[3] for r in rects)


def find_tags(page, tag: str, excluded: list) -> list[dict]:
    """Where a type label ("A", "EM-2") is written on a plan: words exactly equal to it that stand alone (a word in a
    sentence of the notes isn't a label) and aren't in a left-out area."""
    ws = words(page)
    rows = _rows(ws)
    out = []
    for row in rows:
        for i, w in enumerate(row):
            if w[4] != tag:
                continue
            size = max(w[3] - w[1], 1)
            prev_gap = w[0] - row[i - 1][2] if i else 1e9
            next_gap = row[i + 1][0] - w[2] if i + 1 < len(row) else 1e9
            if prev_gap < size * 0.9 or next_gap < size * 0.9:  # part of a sentence
                continue
            c = ((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
            if _inside(c, excluded):
                continue
            out.append({"point": [round(c[0], 2), round(c[1], 2)], "rect": list(w[:4]), "score": 1.0})
    return out


# ---------------------------------------------------------------- legends and schedules

HEADING_RE = re.compile(r"LEGEND|SCHEDULE|SYMBOLS?\b|KEY\b", re.I)
HEADER_WORDS = {"SYMBOL", "DESCRIPTION", "MARK", "TYPE", "MANUFACTURER", "ORDER", "NUMBER", "LAMP", "VOLTAGE", "WATTAGE",
                "FIXTURE", "SYMBOLS", "DESCRIPTIONS", "QTY", "REMARKS", "NOTES", "CATALOG"}


def _drawn(page, rect, drawings) -> list[float] | None:
    """The bounding box of the lines drawn inside rect (a symbol's picture), leaving out its frame."""
    x0, y0, x1, y1 = rect
    boxes = []
    for d in drawings:
        r = d["rect"]
        if r.x0 >= x0 - 0.5 and r.y0 >= y0 - 0.5 and r.x1 <= x1 + 0.5 and r.y1 <= y1 + 0.5:
            if r.width > (x1 - x0) * 0.95 and r.height > (y1 - y0) * 0.95:  # the cell's own border
                continue
            boxes.append(r)
    if not boxes:
        return None
    u = boxes[0]
    for r in boxes[1:]:
        u = u | r
    return [u.x0 - 1.5, u.y0 - 1.5, u.x1 + 1.5, u.y1 + 1.5]


def _entry(page, drawings, symbol_rect, symbol_text, desc, details, heading, source_rect):
    tag = symbol_text if symbol_text and len(symbol_text) <= 8 and TAG_RE.match(symbol_text) else None
    pic = _drawn(page, symbol_rect, drawings) if symbol_rect else None
    if not desc and not tag:
        return None
    return {"name": desc or tag, "tag": tag, "symbol": pic, "details": details, "category": heading, "rect": source_rect}


def _heading_above(page, rect) -> str | None:
    words_above = [w for w in page.get_text("words") if w[3] <= rect[1] + 2 and w[1] >= rect[1] - 30 and w[0] < rect[2] and w[2] > rect[0]]
    text = " ".join(w[4] for w in sorted(words_above, key=lambda w: (round(w[1]), w[0])))
    return text.strip() or None


def legends(page) -> list[dict]:
    """Legends and schedules found as tables: [{"heading", "rect", "columns", "rows": [entry…], "cells": [[text…]…]}]."""
    out = []
    drawings = page.get_drawings()
    try:
        tables = page.find_tables().tables
    except Exception:  # a sheet PyMuPDF can't read as tables: the legend can still be boxed by hand
        tables = []
    for t in tables:
        cells = t.extract()
        if len(cells) < 2:
            continue
        rect = list(t.bbox)
        heading = _heading_above(page, rect)
        first = " ".join(c or "" for c in cells[0])
        if HEADING_RE.search(first) and not (heading and HEADING_RE.search(heading)):
            heading = " ".join((c or "").strip() for c in cells[0] if c).strip()
        if not (heading and HEADING_RE.search(heading)) and not HEADING_RE.search(" ".join(" ".join(c or "" for c in r) for r in cells[:2])):
            continue
        # header row(s): mostly header words; the column with the longest texts is the description
        header_idx = [i for i, r in enumerate(cells[:3]) if sum(1 for c in r if c and c.strip().upper().split()[0:1]
                                                                 and c.strip().upper().split()[0] in HEADER_WORDS) >= 1
                      or (i == 0 and HEADING_RE.search(" ".join(c or "" for c in r)))]
        columns = [(c or "").strip() for c in cells[header_idx[-1]]] if header_idx else []
        body = [(i, r) for i, r in enumerate(cells) if i not in header_idx]
        if not body:
            continue
        ncol = len(cells[0])
        lens = [sum(len((r[j] or "")) for _, r in body) for j in range(ncol)]
        desc_col = next((j for j, c in enumerate(columns) if c.upper().startswith("DESCRIPTION")), None)
        if desc_col is None:
            desc_col = max(range(ncol), key=lambda j: lens[j]) if ncol else 0
        rows = []
        for i, r in body:
            row_cells = t.rows[i].cells if i < len(t.rows) else [None] * ncol
            sym_rect = list(row_cells[0]) if row_cells and row_cells[0] else None
            sym_text = " ".join((r[0] or "").split()) if ncol > 1 and desc_col != 0 else None
            desc = " ".join((r[desc_col] or "").split())
            details = {columns[j] or f"Column {j + 1}": " ".join((r[j] or "").split())
                       for j in range(ncol) if j not in (0, desc_col) and (r[j] or "").strip()}
            row_rect = list(t.rows[i].bbox) if i < len(t.rows) else rect
            e = _entry(page, drawings, sym_rect, sym_text, desc, details, heading, row_rect)
            if e:
                rows.append(e)
        if rows:
            out.append({"heading": heading, "rect": rect, "columns": columns, "rows": rows,
                        "cells": [[" ".join((c or "").split()) for c in r] for r in cells]})
    return out


def legend_in(page, rect: list[float], heading: str | None = None) -> list[dict]:
    """A legend boxed by hand: each line of text in the box is an entry, with the picture drawn to its left."""
    x0, y0, x1, y1 = rect
    ws = [w for w in page.get_text("words") if w[0] >= x0 and w[2] <= x1 and w[1] >= y0 and w[3] <= y1]
    drawings = [d for d in page.get_drawings() if d["rect"].x0 >= x0 and d["rect"].x1 <= x1 and d["rect"].y0 >= y0 and d["rect"].y1 <= y1]
    rows = _rows(ws, tol=4)
    out = []
    heading = heading or None
    for row in rows:
        text = " ".join(w[4] for w in row)
        if not heading and HEADING_RE.search(text) and row is rows[0]:
            heading = text
            continue
        if all(w[4].upper().strip(":") in HEADER_WORDS for w in row):
            continue
        ry0, ry1 = min(w[1] for w in row) - 4, max(w[3] for w in row) + 4
        tag, desc_words = None, row
        if len(row) > 1 and TAG_RE.match(row[0][4]) and row[1][0] - row[0][2] > 8:  # "A    MID-HIGH OUTPUT…"
            tag, desc_words = row[0][4], row[1:]
        sym = [x0, ry0, desc_words[0][0] - 2, ry1]
        e = _entry(page, drawings, sym if sym[2] - sym[0] > 4 else None, tag, " ".join(w[4] for w in desc_words), {}, heading,
                   [x0, ry0, x1, ry1])
        if e:
            out.append(e)
    return out


def tables_on(page) -> list[dict]:
    """Every table on a sheet (panel schedules, fixture schedules…), as text, to copy into a Probyte table."""
    try:
        tables = page.find_tables().tables
    except Exception:
        return []
    out = []
    for t in tables:
        cells = [[" ".join((c or "").split()) for c in r] for r in t.extract()]
        if len(cells) >= 2 and len(cells[0]) >= 2:
            out.append({"rect": list(t.bbox), "heading": _heading_above(page, list(t.bbox)), "cells": cells})
    return out


# ---------------------------------------------------------------- pictures

def render(page, dpi: int, clip=None, gray=False) -> fitz.Pixmap:
    return page.get_pixmap(dpi=dpi, clip=fitz.Rect(clip) if clip else None, colorspace=fitz.csGRAY if gray else fitz.csRGB, alpha=False)


def most_common(values):
    c = Counter(v for v in values if v)
    return c.most_common(1)[0][0] if c else None
