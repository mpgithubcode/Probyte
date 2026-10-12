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


def sheet_index(doc, known: set[str] | None = None) -> dict[str, str]:
    """The drawing index (usually on the cover sheet): sheet number -> title, from lines like "E1.0 ELECTRICAL POWER PLAN".
    known: the sheet numbers read from the title blocks; the index is the page listing the most of them (a note like
    "SEE SHEET E1.2 FOR …" elsewhere isn't an index)."""
    best: dict[str, str] = {}
    for page in doc:
        found = {}
        for row in _rows(flat_words(page)):
            for i, w in enumerate(row[:-1]):
                if not SHEET_RE.match(w[4]) or (known and w[4] not in known):
                    continue
                title = []
                for nxt in row[i + 1:]:
                    if SHEET_RE.match(nxt[4]) and (not known or nxt[4] in known) or nxt[0] - (title[-1][2] if title else w[2]) > 60:
                        break
                    title.append(nxt)
                text = " ".join(t[4] for t in title).strip()
                if len(text) >= 4 and text.upper() == text and re.search(r"[A-Z]{3}", text) and w[4] not in found:
                    found[w[4]] = text
        if len(found) > len(best):
            best = found
    return best if len(best) >= 2 else {}


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
    return flat_words(page)


def search(page, query: str) -> list[list[float]]:
    return [list(r) for r in page.search_for(query)]


def _inside(pt, rects) -> bool:
    return any(r[0] <= pt[0] <= r[2] and r[1] <= pt[1] <= r[3] for r in rects)


def norm_tag(t: str) -> str:
    """EM-EL-2, EM-EL2 and EM EL 2 are the same label."""
    return re.sub(r"[\s\-_./]", "", t or "").upper()


def find_tags(page, tag: str, excluded: list) -> list[dict]:
    """Where a type label ("A", "EM-2") is written on a plan: words exactly equal to it that stand alone (a word in a
    sentence of the notes isn't a label) and aren't in a left-out area."""
    ws = flat_words(page)
    rows = _rows(ws)
    out = []
    want = norm_tag(tag)
    for row in rows:
        for i, w in enumerate(row):
            if norm_tag(w[4]) != want:
                continue
            size = max(w[3] - w[1], 1)
            prev_gap = w[0] - row[i - 1][2] if i else 1e9
            next_gap = row[i + 1][0] - w[2] if i + 1 < len(row) else 1e9
            if prev_gap < size * 0.9 or next_gap < size * 0.9:  # part of a sentence
                continue
            after = " ".join(x[4] for x in row[i + 1:i + 6] if x[0] - w[2] < size * 12)
            if re.search(r"\b(PLAN|ELEVATION|SECTION|DETAIL|SCHEDULE|LEGEND)S?\b", after):  # a drawing title's bubble
                continue
            c = ((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
            if _inside(c, excluded):
                continue
            out.append({"point": [round(c[0], 2), round(c[1], 2)], "rect": list(w[:4]), "score": 1.0})
    return out


# ---------------------------------------------------------------- legends and schedules
#
# A legend (or schedule) is found from its heading ("FIRE ALARM LEGEND", "LIGHT FIXTURE SCHEDULE"): the ruled lines
# under it give its rows, its long vertical lines (or its column headings) its columns. The column headed SYMBOL holds
# the pictures, TYPE / MARK / TAG the labels written on the plans (or the text inside the symbol), DESCRIPTION the
# names; the other columns are kept as details (manufacturer, catalog number, wattage…).

HEADING_RE = re.compile(r"\b(LEGENDS?|SCHEDULES?)\b", re.I)
HEADER_WORDS = {"SYMBOL", "SYMBOLS", "DESCRIPTION", "DESCRIPTIONS", "MARK", "TYPE", "MANUFACTURER", "MFR", "MFG", "ORDER", "NUMBER",
                "CATALOG", "LAMP", "LAMPS", "VOLTAGE", "VOLTS", "WATTAGE", "WATTS", "FIXTURE", "QTY", "REMARKS", "NOTES", "MOUNTING",
                "TAG", "ID", "ITEM", "NAME", "MODEL", "FINISH", "SIZE", "LOCATION", "KEY"}
PIC_RE = re.compile(r"^SYMBOLS?$|^KEY$")
TAG_COL_RE = re.compile(r"^(TYPE|MARK|TAG|ID|LABEL|KEY)$")
DESC_RE = re.compile(r"^(DESCRIPTIONS?|NAME|ITEM)$")
LABEL_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-/.]{0,9}(?: ?[A-Z0-9\-]{1,4})?$")


def flat_words(page) -> list[tuple]:
    """Words written across the page, as (x0, y0, x1, y1, text): built from the characters of horizontal lines, so
    sideways notes are left out; text written twice (CAD bold) is kept once."""
    out, seen = [], set()

    def flush(chars):
        if not chars:
            return
        text = "".join(c["c"] for c in chars)
        box = (min(c["bbox"][0] for c in chars), min(c["bbox"][1] for c in chars),
               max(c["bbox"][2] for c in chars), max(c["bbox"][3] for c in chars))
        k = (text, round(box[0]), round(box[1]))
        if k not in seen:
            seen.add(k)
            out.append((*box, text))

    for b in page.get_text("rawdict")["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            if abs(ln["dir"][1]) > 0.2 or ln["dir"][0] <= 0:
                continue
            for sp in ln["spans"]:
                cur = []
                for ch in sp["chars"]:
                    if not ch["c"].strip():
                        flush(cur)
                        cur = []
                    elif cur and ch["bbox"][0] - cur[-1]["bbox"][2] > max(sp["size"] * 0.6, 2):  # a gap without a space
                        flush(cur)
                        cur = [ch]
                    else:
                        cur.append(ch)
                flush(cur)
    return out


def touches(r, c, pad=0.5) -> bool:
    """Rectangles overlap (lines, which have no area, included: fitz.Rect.intersects leaves those out)."""
    return r[0] <= c[2] + pad and r[2] >= c[0] - pad and r[1] <= c[3] + pad and r[3] >= c[1] - pad


def rules(page, clip=None, drawings=None) -> tuple[list, list]:
    """Long straight lines: horizontal ones (y, x0, x1) and vertical ones (x, y0, y1), from lines and rectangle edges."""
    hs, vs = [], []
    c = fitz.Rect(clip) if clip else None

    def add(a, b):
        if c and not touches((min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y)), c):
            return
        if abs(a.y - b.y) < 0.8 and abs(a.x - b.x) > 20:
            hs.append(((a.y + b.y) / 2, min(a.x, b.x), max(a.x, b.x)))
        elif abs(a.x - b.x) < 0.8 and abs(a.y - b.y) > 20:
            vs.append(((a.x + b.x) / 2, min(a.y, b.y), max(a.y, b.y)))
    for d in (drawings if drawings is not None else page.get_drawings()):
        if c and not touches(d["rect"], c):
            continue
        for it in d["items"]:
            if it[0] == "l":
                add(it[1], it[2])
            elif it[0] == "re":
                r = it[1]
                if r.height < 1.6 and r.width > 20:      # a rule drawn as a thin bar
                    hs.append(((r.y0 + r.y1) / 2, r.x0, r.x1))
                elif r.width < 1.6 and r.height > 20:
                    vs.append(((r.x0 + r.x1) / 2, r.y0, r.y1))
                else:
                    add(r.tl, r.tr); add(r.bl, r.br); add(r.tl, r.bl); add(r.tr, r.br)
    return _join(hs), _join(vs)


def _join(segs, tol=1.0, gap=3.0):
    """Pieces of the same line (a table drawn cell by cell) joined into one."""
    out = []
    for pos, a, b in sorted(segs, key=lambda s: (round(s[0] / tol), s[1])):
        if out and abs(out[-1][0] - pos) <= tol and a <= out[-1][2] + gap:
            out[-1] = (out[-1][0], out[-1][1], max(out[-1][2], b))
        else:
            out.append((pos, a, b))
    return out


def _cover(segs, lo, hi, tol=1.5):
    """Lines at the same height (or x) joined: [(pos, how much of lo..hi they cover)]."""
    out = []
    for pos, a, b in sorted(segs):
        a, b = max(a, lo), min(b, hi)
        if b <= a:
            continue
        if out and abs(pos - out[-1][0]) <= tol:
            out[-1][1].append((a, b))
        else:
            out.append([pos, [(a, b)]])
    res = []
    for pos, ivs in out:
        ivs.sort()
        total, cur = 0.0, list(ivs[0])
        for a, b in ivs[1:]:
            if a <= cur[1] + 2:
                cur[1] = max(cur[1], b)
            else:
                total += cur[1] - cur[0]
                cur = [a, b]
        total += cur[1] - cur[0]
        res.append((pos, total))
    return res


def _dedupe(vals, tol=2.0):
    out = []
    for v in sorted(vals):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def headings(page) -> list[dict]:
    """Lines that name a legend or schedule: {"text", "rect"}."""
    out = []
    for row in _rows(flat_words(page), tol=2.5):
        # split a page-wide row into phrases (words close together)
        phrase = [row[0]]
        for w in row[1:] + [None]:
            if w is not None and w[0] - phrase[-1][2] < max(phrase[-1][3] - phrase[-1][1], 4) * 1.5:
                phrase.append(w)
                continue
            text = " ".join(x[4] for x in phrase)
            if (HEADING_RE.fullmatch(phrase[-1][4].strip(":")) and len(phrase) <= 8 and text.upper() == text
                    and phrase[0][0] < page.rect.width * 0.88):
                out.append({"text": text, "rect": [min(x[0] for x in phrase), min(x[1] for x in phrase),
                                                   max(x[2] for x in phrase), max(x[3] for x in phrase)]})
            phrase = [w] if w is not None else []
    return out


def legend_regions(page, drawings=None) -> list[dict]:
    """Where legends and schedules are: under each heading, the block of ruled lines (or a table PyMuPDF finds)."""
    hs, _ = rules(page, None, drawings)
    regions = []
    for h in headings(page):
        hx = (h["rect"][0] + h["rect"][2]) / 2
        below = sorted((r for r in hs if r[1] - 4 <= hx <= r[2] + 4 and r[2] - r[1] > 80 and h["rect"][1] - 40 <= r[0] <= h["rect"][3] + 400),
                       key=lambda r: r[0])
        # rules above the heading's bottom (the box it sits in) and the run of rules under it, of the same width
        start = [r for r in below if r[0] >= h["rect"][1] - 40]
        if not start:
            continue
        first = next((r for r in start if r[0] >= h["rect"][3] - 2), start[0])
        x0, x1 = first[1], first[2]
        same = [r for r in hs if abs(r[1] - x0) < 25 and abs(r[2] - x1) < 25 and r[0] >= h["rect"][1] - 40]
        ys = _dedupe([r[0] for r in same])
        run = [ys[0]]
        for y in ys[1:]:
            if y - run[-1] > 220:  # a big gap: the legend ended
                break
            run.append(y)
        if len(run) < 2:
            continue
        top = min(run[0], h["rect"][1] - 2)
        rect = [x0, top, x1, run[-1]]
        if not any(_overlap(rect, r["rect"]) > 0.6 for r in regions):
            regions.append({"heading": h["text"], "rect": rect})
    try:
        tables = page.find_tables().tables
    except Exception:
        tables = []
    for t in tables:
        rect = list(t.bbox)
        if any(_overlap(rect, r["rect"]) > 0.3 for r in regions):
            continue
        first = " ".join(" ".join(c or "" for c in r) for r in t.extract()[:2])
        heading = _heading_above(page, rect)
        if HEADING_RE.search(first) or (heading and HEADING_RE.search(heading)) or re.search(r"\bSYMBOL\b", first):
            regions.append({"heading": heading if heading and HEADING_RE.search(heading) else None, "rect": rect})
    return regions


def _overlap(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])) or 1
    return ix * iy / area


def _heading_above(page, rect) -> str | None:
    words_above = [w for w in flat_words(page) if w[3] <= rect[1] + 2 and w[1] >= rect[1] - 30 and w[0] < rect[2] and w[2] > rect[0]]
    text = " ".join(w[4] for w in sorted(words_above, key=lambda w: (round(w[1]), w[0])))
    return text.strip() or None


def _picture(drawings, words, cell, label_words) -> list[float] | None:
    """The symbol in a cell: what's drawn there (not the cell's own lines), with any letters inside it.
    (Boxes are worked out by hand: fitz.Rect drops lines, which have no area, from unions.)"""
    x0, y0, x1, y1 = cell
    cw, ch = x1 - x0, y1 - y0
    bx = [1e9, 1e9, -1e9, -1e9]
    found = False
    for d in drawings:
        for it in d["items"]:
            if it[0] == "l":
                pts = [it[1], it[2]]
            elif it[0] == "re":
                pts = [it[1].tl, it[1].br]
            elif it[0] == "qu":
                pts = [it[1].ul, it[1].ur, it[1].ll, it[1].lr]
            else:
                pts = list(it[1:5])
            xs, ys = [q.x for q in pts], [q.y for q in pts]
            r = (min(xs), min(ys), max(xs), max(ys))
            if r[0] < x0 - 1 or r[2] > x1 + 1 or r[1] < y0 - 1 or r[3] > y1 + 1:
                continue
            w, h = r[2] - r[0], r[3] - r[1]
            if (w > cw * 0.85 and h < 1.5) or (h > ch * 0.85 and w < 1.5) or (w > cw * 0.85 and h > ch * 0.85):
                continue  # the cell's own lines, or its box
            bx = [min(bx[0], r[0]), min(bx[1], r[1]), max(bx[2], r[2]), max(bx[3], r[3])]
            found = True
    if not found:
        return None
    for w in label_words:  # letters inside the symbol (S, F, WP, CO) are part of its picture; a type label above it isn't
        c = ((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
        if bx[0] - 1 <= c[0] <= bx[2] + 1 and bx[1] - 1 <= c[1] <= bx[3] + 1:
            bx = [min(bx[0], w[0]), min(bx[1], w[1]), max(bx[2], w[2]), max(bx[3], w[3])]
    return [round(bx[0] - 1.5, 2), round(bx[1] - 1.5, 2), round(bx[2] + 1.5, 2), round(bx[3] + 1.5, 2)]


def parse_legend(page, rect, heading: str | None = None, drawings=None, words=None) -> dict:
    """The entries of a legend / schedule in rect: {"heading", "rect", "columns", "rows": [entry…], "cells"}."""
    x0, y0, x1, y1 = rect
    W, H = x1 - x0, y1 - y0
    words = [w for w in (words if words is not None else flat_words(page))
             if x0 - 1 <= (w[0] + w[2]) / 2 <= x1 + 1 and y0 - 1 <= (w[1] + w[3]) / 2 <= y1 + 1]
    drawings = [d for d in (drawings if drawings is not None else page.get_drawings()) if touches(d["rect"], rect)]
    hs, vs = rules(page, rect, drawings)
    # (one continuous line each: pieces drawn cell by cell are joined already; a symbol's own strokes are too short)
    ys = _dedupe([y for y, a, b in hs if min(b, x1) - max(a, x0) >= W * 0.55 and y0 - 2 <= y <= y1 + 2])
    xs = _dedupe([x for x, a, b in vs if min(b, y1) - max(a, y0) >= H * 0.45 and x0 + 4 < x < x1 - 4])
    # rows: between rules, or text lines when there are none
    if len(ys) >= 3:
        bands = [(a, b) for a, b in zip([y0] + ys, ys + [y1]) if b - a > 5]
    else:
        bands = []
        for row in _rows(words, tol=3):
            bands.append((min(w[1] for w in row) - 1, max(w[3] for w in row) + 1))
    in_band = lambda b: [w for w in words if b[0] <= (w[1] + w[3]) / 2 <= b[1]]
    # the heading, and the column names (a line mostly of words like SYMBOL, DESCRIPTION, MANUFACTURER; with the
    # lines under it in the same row: two-line names)
    header_i, header_words, head_text = None, [], heading
    for i, b in enumerate(bands[:5]):
        lines = _rows(in_band(b), tol=2.5)
        for j, line in enumerate(lines):
            t = " ".join(w[4] for w in line)
            if HEADING_RE.search(t) and not head_text:
                head_text = t
            named = [w for w in line if w[4].upper().strip(":,.()") in HEADER_WORDS]
            if named and len(named) >= len(line) * 0.5 and not HEADING_RE.search(t):
                header_i, header_words = i, [w for ln in lines[j:] for w in ln]
                break
        if header_i is not None:
            break
    start = (header_i + 1) if header_i is not None else 0
    if header_i is None:  # no column names: skip the heading's row
        while start < len(bands) and HEADING_RE.search(" ".join(w[4] for w in in_band(bands[start]))):
            start += 1
    # columns: long vertical rules, else gaps between the column names, else the picture zone and the text
    if xs:
        edges = [x0] + xs + [x1]
    elif header_words:
        groups = []
        for w in sorted(header_words, key=lambda w: w[0]):
            if groups and w[0] <= groups[-1][1] + 6:
                groups[-1] = [groups[-1][0], max(groups[-1][1], w[2])]
            else:
                groups.append([w[0], w[2]])
        edges = [x0] + [(a[1] + b[0]) / 2 for a, b in zip(groups, groups[1:])] + [x1]
    else:
        starts = [round(min(w[0] for w in in_band(b)) / 4) * 4 for b in bands[start:] if in_band(b)]
        text_x = Counter(starts).most_common(1)[0][0] if starts else x0
        edges = [x0, max(x0 + 8, text_x - 3), x1] if text_x - x0 > 12 else [x0, x1]
    if not xs and len(edges) > 2:  # descriptions often start left of their (centred) column name
        firsts = [min(w[0] for w in in_band(b)) for b in bands[start:] if in_band(b)]
        firsts = [round(f / 3) * 3 for f in firsts if f > edges[0] + 8]
        if firsts:
            mode = Counter(firsts).most_common(1)[0][0]
            if edges[0] + 8 < mode - 3 < edges[1]:
                edges[1] = mode - 3
    cols = [(a, b) for a, b in zip(edges, edges[1:]) if b - a > 6]
    names = [" ".join(w[4] for w in sorted([w for w in header_words if a <= (w[0] + w[2]) / 2 <= b], key=lambda w: (round(w[1]), w[0])))
             for a, b in cols]
    pic = next((i for i, n in enumerate(names) if any(PIC_RE.match(t) for t in n.upper().split())), None)
    tagc = next((i for i, n in enumerate(names) if i != pic and any(TAG_COL_RE.match(t) for t in n.upper().split())), None)
    desc = next((i for i, n in enumerate(names) if any(DESC_RE.match(t) for t in n.upper().split())), None)
    body = bands[start:]
    if pic is None and desc is None:
        pic = 0
    if desc is None:  # the column with the most text
        lens = [sum(len(w[4]) for b in body for w in in_band(b) if a <= (w[0] + w[2]) / 2 <= c) for a, c in cols]
        cand = [i for i in range(len(cols)) if i not in (pic, tagc)]
        desc = max(cand, key=lambda i: lens[i]) if cand else len(cols) - 1
    if pic is None:
        pic = 0 if desc != 0 else None
    rows, cells = [], [names]
    for b in body:
        ws = in_band(b)
        if not ws and pic is None:
            continue
        text = lambda i: " ".join(w[4] for w in sorted([w for w in ws if cols[i][0] <= (w[0] + w[2]) / 2 <= cols[i][1]],
                                                       key=lambda w: (round(w[1] / 3), w[0])))
        row_cells = [text(i) for i in range(len(cols))]
        name = row_cells[desc] if desc is not None and desc < len(cols) else ""
        label_src = tagc if tagc is not None else pic
        label = row_cells[label_src].strip() if label_src is not None else ""
        label = label if label and len(label) <= 12 and LABEL_RE.match(label) else None
        sym = None
        if pic is not None:
            a, c = cols[pic]
            in_pic = [w for w in ws if a <= (w[0] + w[2]) / 2 <= c]
            sym = _picture(drawings, ws, [a, b[0], c, b[1]], in_pic)
        if not name and not label and not sym:
            continue
        if len(ys) < 3 and rows and not label and name and (not sym or _overlap(sym, rows[-1]["symbol"] or [0, 0, 0, 0]) > 0.3) \
                and b[0] - rows[-1]["rect"][3] < 14:
            # (no rules) a line without its own symbol or label continues the entry above
            rows[-1]["name"] = (rows[-1]["name"] + " " + name).strip()
            rows[-1]["rect"][3] = b[1]
            if sym and rows[-1]["symbol"]:
                rows[-1]["symbol"] = [min(sym[0], rows[-1]["symbol"][0]), min(sym[1], rows[-1]["symbol"][1]),
                                      max(sym[2], rows[-1]["symbol"][2]), max(sym[3], rows[-1]["symbol"][3])]
            continue
        details = {names[i] or f"Column {i + 1}": row_cells[i] for i in range(len(cols))
                   if i not in (pic, desc, tagc) and row_cells[i] and names[i]}
        cells.append(row_cells)
        rows.append({"name": name or label or "", "tag": label, "symbol": sym, "details": details, "category": head_text,
                     "rect": [x0, b[0], x1, b[1]]})
    return {"heading": head_text, "rect": list(rect), "columns": names, "rows": [r for r in rows if r["name"]], "cells": cells}


def legends(page) -> list[dict]:
    """Every legend / schedule on a sheet with its entries."""
    drawings = page.get_drawings()
    words = flat_words(page)
    out = []
    for reg in legend_regions(page, drawings):
        lg = parse_legend(page, reg["rect"], reg["heading"], drawings, words)
        if len(lg["rows"]) >= 2 or (lg["rows"] and lg["heading"]):
            out.append(lg)
    return out


def legend_in(page, rect: list[float], heading: str | None = None) -> list[dict]:
    """A legend boxed by hand."""
    return parse_legend(page, rect, heading)["rows"]


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


def straighten(doc) -> bool:
    """Pages stored turned (CAD sets often are: /Rotate 90 or 270) are straightened in place, keeping how they look, so
    that text, lines and pictures all use the coordinates the sheet is seen in. True when something changed."""
    turned = [p for p in doc if p.rotation]
    for p in turned:
        p.remove_rotation()
    return bool(turned)


def upright(path) -> bool:
    """straighten() a PDF file in place. True when the file changed."""
    doc = fitz.open(path)
    if not straighten(doc):
        doc.close()
        return False
    tmp = path.with_suffix(".upright.pdf")
    doc.save(tmp, garbage=1, deflate=True)
    doc.close()
    tmp.replace(path)
    return True
