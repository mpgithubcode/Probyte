"""Blueprint takeoff's drawing reading on its own: sheets, legends and symbol finds of a PDF, as JSON, without a server.

    python -m app.tools.takeoff sheets set.pdf                       sheet number, title, discipline and scales of each page
    python -m app.tools.takeoff legends set.pdf --page 3             the legends / schedules on a page, with their entries
    python -m app.tools.takeoff find set.pdf --symbol 3:x0,y0,x1,y1 [--pages 4,5] [--threshold 0.75]
                                                                     where the symbol boxed on page 3 appears

Pages count from 0; coordinates are PDF points on the straightened page (as the app shows it).
"""
import json
import sys

import fitz

from app.extensions.takeoff import drawings, symbols


def _arg(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


def sheets(doc) -> list[dict]:
    index = drawings.sheet_index(doc)
    return [{"page": i, **drawings.read_sheet(p, index), "scales": drawings.scales_on(p)} for i, p in enumerate(doc)]


def find(doc, symbol_page: int, rect: list[float], pages: list[int], threshold: float) -> dict:
    tpl = symbols.symbol_picture(doc[symbol_page], rect)
    if tpl is None:
        raise SystemExit("error: nothing is drawn in that box")
    why = symbols.plainness(tpl)
    if why:
        raise SystemExit(f"error: the symbol can't be told apart from plain linework ({why})")
    out = {}
    for n in pages:
        try:
            out[n] = symbols.find(symbols.sheet_picture(doc[n]), tpl, threshold)
        except symbols.TooMany:
            out[n] = "too many matches: it looks like ordinary linework"
    return out


def main(argv):
    if len(argv) < 2 or argv[0] not in ("sheets", "legends", "find"):
        raise SystemExit(__doc__)
    doc = fitz.open(argv[1])
    drawings.straighten(doc)
    if argv[0] == "sheets":
        result = sheets(doc)
    elif argv[0] == "legends":
        result = drawings.legends(doc[int(_arg(argv, "--page", "0"))])
    else:
        spec = _arg(argv, "--symbol")
        if not spec or ":" not in spec:
            raise SystemExit(__doc__)
        page, box = spec.split(":", 1)
        pages = [int(x) for x in _arg(argv, "--pages", ",".join(str(i) for i in range(len(doc)))).split(",")]
        result = find(doc, int(page), [float(x) for x in box.split(",")], pages, float(_arg(argv, "--threshold", "0.75")))
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main(sys.argv[1:])
