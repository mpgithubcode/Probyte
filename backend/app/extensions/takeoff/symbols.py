"""Finding a legend symbol on a sheet: the symbol's picture is looked for in a picture of the sheet (OpenCV template
matching on grey pictures), turned four ways and a little larger and smaller. Every find is a suggestion with a score
(how alike, 0–1), for a person to accept or reject.
"""
import cv2
import numpy as np

from app.extensions.takeoff import drawings

DPI = 100  # sheets are looked at at this resolution (a 36" × 24" sheet: 3600 × 2400)


def _gray(pix) -> np.ndarray:
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()


def sheet_picture(page) -> np.ndarray:
    return _gray(drawings.render(page, DPI, gray=True))


MARGIN = 1  # white kept around a symbol (px; more would miss symbols drawn tight against walls)


def symbol_picture(page, rect) -> np.ndarray | None:
    pic = _gray(drawings.render(page, DPI, clip=rect, gray=True))
    if pic.size == 0 or pic.shape[0] < 4 or pic.shape[1] < 4 or float(pic.std()) < 4:  # nothing drawn there
        return None
    return cv2.copyMakeBorder(pic, MARGIN, MARGIN, MARGIN, MARGIN, cv2.BORDER_CONSTANT, value=255)


def plainness(tpl: np.ndarray) -> str | None:
    """Why a symbol can't be told apart from ordinary linework (a bare line, a dot), or None."""
    ink = tpl < 128
    ys, xs = np.where(ink)
    if len(xs) < 12:
        return "it's too small"
    h, w = ys.max() - ys.min() + 1, xs.max() - xs.min() + 1
    if (min(h, w) <= 4 and max(h, w) > 12) or (min(h, w) <= 9 and max(h, w) > min(h, w) * 6):
        return "it's just a line"
    return None


def _variants(tpl: np.ndarray, rotate: bool, sizes):
    for s in sizes:
        t = tpl if s == 1 else cv2.resize(tpl, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        if t.shape[0] < 4 or t.shape[1] < 4:
            continue
        yield t
        if rotate:
            yield cv2.rotate(t, cv2.ROTATE_90_CLOCKWISE)
            yield cv2.rotate(t, cv2.ROTATE_180)
            yield cv2.rotate(t, cv2.ROTATE_90_COUNTERCLOCKWISE)


class TooMany(Exception):
    pass


def find(sheet: np.ndarray, tpl: np.ndarray, threshold: float = 0.75, rotate: bool = True, sizes=(1.0, 0.9, 1.1),
         limit: int = 400) -> list[dict]:
    """Places on the sheet that look like the symbol: [{"point": [x, y] (PDF points), "box": [...], "score"}].
    TooMany when it matches more places than `limit` (it looks like ordinary linework)."""
    kernel = np.ones((2, 2), np.uint8)  # thin lines: thicken both a little so a slightly different line weight still matches
    sheet_i = cv2.erode(sheet, kernel)
    hits = []
    for t in _variants(cv2.erode(tpl, kernel), rotate, sizes):
        if t.shape[0] >= sheet_i.shape[0] or t.shape[1] >= sheet_i.shape[1]:
            continue
        res = cv2.matchTemplate(sheet_i, t, cv2.TM_CCOEFF_NORMED)
        res = np.nan_to_num(res, nan=0.0)
        # the best place in each neighbourhood (half the symbol's size), above the likeness asked for
        k = np.ones((max(3, t.shape[0] // 2), max(3, t.shape[1] // 2)), np.uint8)
        peaks = (res >= threshold) & (res >= cv2.dilate(res, k) - 1e-6)
        ys, xs = np.where(peaks)
        if len(xs) > limit * 6:
            raise TooMany()
        for y, x in zip(ys.tolist(), xs.tolist()):
            hits.append((float(res[y, x]), x, y, t.shape[1], t.shape[0]))
    hits.sort(reverse=True)
    kept = []
    for score, x, y, w, h in hits:  # one find per place: the best
        cx, cy = x + w / 2, y + h / 2
        if any(abs(cx - k[0]) < max(w, k[2]) * 0.4 and abs(cy - k[1]) < max(h, k[3]) * 0.4 for k in kept):
            continue
        kept.append((cx, cy, w, h, score))
        if len(kept) > limit:
            raise TooMany()
    f = 72 / DPI
    m = MARGIN
    return [{"point": [round(cx * f, 2), round(cy * f, 2)], "box": [round((cx - w / 2 + m) * f, 2), round((cy - h / 2 + m) * f, 2),
                                                                 round((cx + w / 2 - m) * f, 2), round((cy + h / 2 - m) * f, 2)],
             "score": round(score, 3)} for cx, cy, w, h, score in kept]


def score_at(sheet: np.ndarray, tpl: np.ndarray, box_pt, rotate: bool = True, sizes=(1.0, 0.9, 1.1)) -> float:
    """How well a symbol fits one place of the sheet (to tell similar symbols apart: the better fit wins)."""
    f = DPI / 72
    cx, cy = (box_pt[0] + box_pt[2]) / 2 * f, (box_pt[1] + box_pt[3]) / 2 * f
    kernel = np.ones((2, 2), np.uint8)
    best = 0.0
    for t in _variants(cv2.erode(tpl, kernel), rotate, sizes):
        pad = 6
        x0, y0 = int(max(0, cx - t.shape[1] / 2 - pad)), int(max(0, cy - t.shape[0] / 2 - pad))
        crop = cv2.erode(sheet[y0:y0 + t.shape[0] + 2 * pad, x0:x0 + t.shape[1] + 2 * pad], kernel)
        if crop.shape[0] < t.shape[0] or crop.shape[1] < t.shape[1]:
            continue
        best = max(best, float(np.nan_to_num(cv2.matchTemplate(crop, t, cv2.TM_CCOEFF_NORMED)).max()))
    return best
