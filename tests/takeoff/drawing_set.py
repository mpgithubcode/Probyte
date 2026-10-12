"""A small made-up drawing set for the takeoff tests (36" × 24" sheets, drawn with PyMuPDF): a cover with the
drawing index, an electrical lighting plan (fixture schedule, fixtures labelled A and B, a 20' wall), a power plan
(legend, receptacles) and an architectural plan at another scale. What's on it is known exactly."""
import fitz

W, H = 36 * 72, 24 * 72
A_SPOTS = [(400, 400), (600, 400), (800, 400), (400, 650), (600, 650), (800, 650), (1000, 900)]   # 7 type A fixtures
B_SPOTS = [(450, 1100), (650, 1100), (850, 1100), (1050, 1100)]                                     # 4 type B (one turned)
REC_SPOTS = [(500, 500), (700, 500), (900, 500), (1100, 500), (1300, 800)]                         # 5 duplex receptacles
TURN = -90
QUAD_SPOTS = [(500, 900), (800, 900), (1100, 1100)]                                                  # 3 quadruplex (look alike)


def title_block(page, sheet, title):
    page.draw_rect(fitz.Rect(W - 260, 20, W - 20, H - 20), color=(0, 0, 0), width=1.5)
    page.insert_text((W - 240, H - 150), "SHEET:", fontsize=10)
    page.insert_text((W - 240, H - 100), sheet, fontsize=36)
    page.insert_text((W - 240, H - 60), "TITLE:", fontsize=10)
    page.insert_text((W - 240, H - 44), title, fontsize=10)
    page.insert_text((W - 240, 60), "EH ELECTRICAL TEST SET", fontsize=12)


def fixture_a(page, x, y):  # a circle with an X
    page.draw_circle((x, y), 9, color=(0, 0, 0), width=1)
    page.draw_line((x - 6, y - 6), (x + 6, y + 6), color=(0, 0, 0), width=1)
    page.draw_line((x - 6, y + 6), (x + 6, y - 6), color=(0, 0, 0), width=1)


def fixture_b(page, x, y, turned=False):  # a square with a triangle in one corner
    page.draw_rect(fitz.Rect(x - 9, y - 9, x + 9, y + 9), color=(0, 0, 0), width=1)
    tri = [(x - 9, y - 9), (x + 3, y - 9), (x - 9, y + 3)] if not turned else [(x + 9, y - 9), (x + 9, y + 3), (x - 3, y - 9)]
    page.draw_polyline(tri + [tri[0]], color=(0, 0, 0), fill=(0, 0, 0), width=1)


def receptacle(page, x, y):  # a circle with two short lines through it
    page.draw_circle((x, y), 8, color=(0, 0, 0), width=1)
    page.draw_line((x - 3, y - 12), (x - 3, y + 12), color=(0, 0, 0), width=1)
    page.draw_line((x + 3, y - 12), (x + 3, y + 12), color=(0, 0, 0), width=1)


def quad(page, x, y):  # the same, crossed: a quadruplex receptacle
    receptacle(page, x, y)
    page.draw_line((x - 12, y), (x + 12, y), color=(0, 0, 0), width=1)


def line_symbol(page, x, y):  # a linear luminaire: just a line
    page.draw_line((x - 25, y), (x + 25, y), color=(0, 0, 0), width=1)


def table(page, x, y, heading, header, rows, draw):
    """A ruled schedule: heading above, a header row, then rows (a picture in the first column)."""
    widths = [70, 260, 140][: len(header)]
    rh = 30
    page.insert_text((x, y - 8), heading, fontsize=11)
    allrows = [header] + rows
    for r, cells in enumerate(allrows):
        cx = x
        for c, text in enumerate(cells):
            page.draw_rect(fitz.Rect(cx, y + r * rh, cx + widths[c], y + (r + 1) * rh), color=(0, 0, 0), width=0.8)
            if text:
                page.insert_text((cx + (40 if c == 0 and r else 6), y + r * rh + 19), text, fontsize=9)
            cx += widths[c]
        if r:
            draw(r - 1, x + 22, y + r * rh + rh / 2)


def power_plan(doc, sheet):
    p = doc.new_page(width=W, height=H)
    title_block(p, sheet, "POWER PLAN")
    table(p, 1700, 200, "ELECTRICAL LEGEND", ["SYMBOL", "DESCRIPTION"], [["", "DUPLEX RECEPTACLE"], ["", "QUADRUPLEX RECEPTACLE"]],
          lambda i, x, y: receptacle(p, x, y) if i == 0 else quad(p, x, y))
    for x, y in REC_SPOTS:
        receptacle(p, x, y)
    for x, y in QUAD_SPOTS:
        quad(p, x, y)
    p.insert_text((300, 1600), "A POWER PLAN", fontsize=14)
    p.insert_text((300, 1620), "SCALE: 1/4\" = 1'-0\"", fontsize=9)
    return p


def make(path):
    doc = fitz.open()
    # cover: the drawing index
    p = doc.new_page(width=W, height=H)
    title_block(p, "T1.0", "TITLE SHEET")
    p.insert_text((200, 200), "SHEET INDEX", fontsize=14)
    for i, (n, t) in enumerate([("T1.0", "TITLE SHEET"), ("E1.0", "ELECTRICAL LIGHTING PLAN"), ("E2.0", "ELECTRICAL POWER PLAN"),
                                ("A1.0", "ARCHITECTURAL FLOOR PLAN")]):
        p.insert_text((200, 230 + 20 * i), n, fontsize=10)
        p.insert_text((260, 230 + 20 * i), t, fontsize=10)
    # E1.0: lighting
    p = doc.new_page(width=W, height=H)
    title_block(p, "E1.0", "LIGHTING PLAN")
    table(p, 1700, 200, "LIGHT FIXTURE SCHEDULE", ["SYMBOL", "DESCRIPTION", "MANUFACTURER"],
          [["A", "2X4 LED TROFFER", "LITHONIA"], ["B", "LED DOWNLIGHT", "JUNO"], ["L", "8' LINEAR LUMINAIRE", "CORONET"]],
          lambda i, x, y: fixture_a(p, x, y) if i == 0 else fixture_b(p, x, y) if i == 1 else line_symbol(p, x + 10, y))
    # a note written sideways beside the schedule (as on real sheets): not part of any entry
    p.insert_text((1690, 330), "SEE SHEET A1.1 FOR MOUNTING", fontsize=7, rotate=90)
    for x, y in A_SPOTS:
        fixture_a(p, x, y)
        p.insert_text((x + 14, y - 10), "A", fontsize=8)
    for i, (x, y) in enumerate(B_SPOTS):
        fixture_b(p, x, y, turned=(i == 3))
        p.insert_text((x + 14, y - 10), "B", fontsize=8)
    p.draw_line((300, 1300), (300 + 20 * 18, 1300), color=(0, 0, 0), width=2)   # a 20'-0" wall at 1/4" = 1'-0"
    p.insert_text((400, 1290), "20'-0\"", fontsize=8)
    p.insert_text((300, 1500), "GENERAL NOTES: PROVIDE A NEW FIXTURE IN EACH ROOM AND A SWITCH.", fontsize=9)
    p.insert_text((300, 1600), "A LIGHTING PLAN", fontsize=14)
    p.insert_text((300, 1620), "SCALE: 1/4\" = 1'-0\"", fontsize=9)
    p.insert_text((1700, 1600), "1 DETAIL  SCALE: 1 1/2\" = 1'-0\"", fontsize=9)
    power_plan(doc, "E2.0")
    # A1.0: architectural, another scale
    p = doc.new_page(width=W, height=H)
    title_block(p, "A1.0", "FLOOR PLAN")
    p.insert_text((300, 1600), "A FLOOR PLAN", fontsize=14)
    p.insert_text((300, 1620), "SCALE: 1/8\" = 1'-0\"", fontsize=9)
    # E3.0: the power plan of another floor, stored turned as CAD sets often are: the page is portrait with the
    # drawing on its side, and /Rotate 270 shows it upright
    tmp = fitz.open()
    power_plan(tmp, "E3.0")
    q = doc.new_page(width=H, height=W)
    q.show_pdf_page(q.rect, tmp, 0, rotate=TURN)
    q.set_rotation(270)
    doc.save(path)
    return path


if __name__ == "__main__":
    import sys
    make(sys.argv[1] if len(sys.argv) > 1 else "drawing_set.pdf")
