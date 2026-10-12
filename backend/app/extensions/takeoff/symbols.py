"""Finding a legend symbol on a sheet: the symbol's picture is looked for in a picture of the sheet (OpenCV template
matching on grey pictures), turned four ways and a little larger and smaller. Every find is a suggestion with a score
(how alike, 0–1), for a person to accept or reject.
"""
import cv2
import numpy as np

from . import drawings

DPI = 100  # sheets are looked at at this resolution (a 36" × 24" sheet: 3600 × 2400)


def _gray(pix) -> np.ndarray:
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()


def sheet_picture(page) -> np.ndarray:
    return _gray(drawings.render(page, DPI, gray=True))


def symbol_picture(page, rect) -> np.ndarray | None:
    pic = _gray(drawings.render(page, DPI, clip=rect, gray=True))
    if pic.size == 0 or pic.shape[0] < 4 or pic.shape[1] < 4 or float(pic.std()) < 4:  # nothing drawn there
        return None
    return pic


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


def find(sheet: np.ndarray, tpl: np.ndarray, threshold: float = 0.75, rotate: bool = True, sizes=(1.0, 0.9, 1.1),
         limit: int = 2000) -> list[dict]:
    """Places on the sheet that look like the symbol: [{"point": [x, y] (PDF points), "box": [...], "score"}]."""
    # thin lines: thicken both a little so a slightly different line weight still matches
    kernel = np.ones((2, 2), np.uint8)
    sheet_i = cv2.erode(sheet, kernel)
    hits = []
    for t in _variants(cv2.erode(tpl, kernel), rotate, sizes):
        if t.shape[0] >= sheet_i.shape[0] or t.shape[1] >= sheet_i.shape[1]:
            continue
        res = cv2.matchTemplate(sheet_i, t, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(res >= threshold)
        for y, x in zip(ys.tolist(), xs.tolist()):
            hits.append((float(res[y, x]), x, y, t.shape[1], t.shape[0]))
    hits.sort(reverse=True)
    kept = []
    for score, x, y, w, h in hits:  # one find per place: the best
        cx, cy = x + w / 2, y + h / 2
        if any(abs(cx - k[0]) < max(w, k[2]) * 0.6 and abs(cy - k[1]) < max(h, k[3]) * 0.6 for k in kept):
            continue
        kept.append((cx, cy, w, h, score))
        if len(kept) >= limit:
            break
    f = 72 / DPI
    return [{"point": [round(cx * f, 2), round(cy * f, 2)], "box": [round((cx - w / 2) * f, 2), round((cy - h / 2) * f, 2),
                                                                 round((cx + w / 2) * f, 2), round((cy + h / 2) * f, 2)],
             "score": round(score, 3)} for cx, cy, w, h, score in kept]
