"""Browser checks on a phone and a desktop: every view renders cleanly, layout fixes stay fixed."""
import pytest
from playwright.sync_api import sync_playwright

from conftest import BASE, Client

NO_SIDEWAYS = "() => document.documentElement.scrollWidth <= innerWidth + 1"
TOUCH = """([x, y, kind]) => { const el = kind === 'pointerdown' ? document.elementFromPoint(x, y) : window;
  el.dispatchEvent(new PointerEvent(kind, {bubbles: true, pointerType: 'touch', clientX: x, clientY: y, button: 0, isPrimary: true})); }"""


@pytest.fixture(scope="module")
def seeded():
    u = Client()
    t = u.table("mixed", "Personal")
    F = u.fields(t["id"])
    rows = [("Buy groceries", "Event", "2026-10-10", "2026-10-11", None), ("Dinner with Sam", "Event", "2026-10-11T18:00:00Z", "2026-10-11T20:00:00Z", None),
            ("Call mom", "Task", None, "2026-10-12", "To Do"), ("Plan trip", "Task", "2026-10-05", "2026-10-19", "In Progress"),
            ("File taxes", "Task", "2026-09-28", "2026-10-02", "Completed"), ("Read 3 books", "Task", None, None, "To Do")]
    for title, kind, start, end, status in rows:
        data = {F["summary"]: title, F["component"]: kind}
        if start: data[F["dtstart"]] = start
        if end: data[F["dtend"]] = end
        if status: data[F["status"]] = status
        u.j("POST", f"/tables/{t['id']}/records", json={"data": data})
    return u, t


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield p, b
        b.close()


def open_page(browser, seeded, phone):
    p, b = browser
    u, t = seeded
    ctx = b.new_context(**p.devices["iPhone 13"]) if phone else b.new_context(viewport={"width": 1280, "height": 860})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(BASE)
    pg.wait_for_selector(".login-card")
    pg.fill("input[autocomplete=username]", u.username)
    pg.fill("input[type=password]", u.password)
    pg.click("button:has-text('Sign in')")
    pg.wait_for_selector(".page, .welcome")
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.wait_for_selector(".view-tab")
    return pg, errors


@pytest.mark.parametrize("phone", [True, False], ids=["phone", "desktop"])
def test_every_view_renders(browser, seeded, phone):
    pg, errors = open_page(browser, seeded, phone)
    for view in ("Grid", "Calendar", "Board", "Timeline", "List", "Gallery", "Chart"):
        if not pg.locator(f".view-tab:has-text('{view}')").count():
            pg.click(".view-tab.add")
            pg.click(f".view-type-item:has-text('{ {'Grid': 'Table'}.get(view, view) }')")
        else:
            pg.locator(f".view-tab:has-text('{view}')").scroll_into_view_if_needed()
            pg.click(f".view-tab:has-text('{view}')")
        pg.wait_for_timeout(300)
        assert pg.evaluate(NO_SIDEWAYS), f"{view} scrolls sideways"
    assert pg.locator(".view-options .new-record-btn").count() == 1
    assert not errors, errors


def test_phone_header_buttons_flush_and_equal(browser, seeded):
    pg, _ = open_page(browser, seeded, True)
    right, last, sizes = pg.evaluate("""() => { const r = document.querySelector('.title-row').getBoundingClientRect();
      const kids = [...document.querySelectorAll('.title-row > *')].filter(e => e.offsetWidth);
      return [Math.round(r.right), Math.round(kids.pop().getBoundingClientRect().right),
              [...document.querySelectorAll('.title-row .btn.sm, .title-row > .icon-btn:not(.menu-btn)')].map(e => e.offsetWidth)] }""")
    assert right == last and len(set(sizes)) == 1


@pytest.mark.parametrize("phone", [True, False], ids=["phone", "desktop"])
def test_list_columns_line_up(browser, seeded, phone):
    pg, _ = open_page(browser, seeded, phone)
    pg.locator(".view-tab:has-text('List')").scroll_into_view_if_needed()
    pg.click(".view-tab:has-text('List')")
    pg.click(".tool-btn:has-text('Properties')")
    for name, zone in (("Type", "Left"), ("Status", "Center")):
        pg.locator(".list-opt-row").filter(has_text=name).first.locator(f"button:has-text('{zone}')").click()
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(200)
    xs = lambda z: set(pg.locator(f".list-row .list-zone.{z}").evaluate_all("e => e.map(x => Math.round(x.getBoundingClientRect().left))"))
    assert len(xs("left")) == 1 and len(xs("center")) == 1
    mid_row, mid_zone = pg.evaluate("""() => { const r = document.querySelector('.list-row').getBoundingClientRect(),
      z = document.querySelector('.list-row .list-zone.center').getBoundingClientRect(); return [r.left + r.width / 2, z.left + z.width / 2] }""")
    assert abs(mid_row - mid_zone) <= 1


def test_phone_week_scrolls_sideways(browser, seeded):
    pg, _ = open_page(browser, seeded, True)
    pg.click(".view-tab:has-text('Calendar')")
    pg.click(".cal-head .segmented button:has-text('Week')")
    pg.wait_for_selector(".tgrid")
    sw, cw = pg.locator(".tgrid").evaluate("e => [e.scrollWidth, e.clientWidth]")
    assert sw > cw * 1.5


def test_phone_drag_needs_a_hold(browser, seeded):
    pg, _ = open_page(browser, seeded, True)
    pg.click(".view-tab:has-text('Board')")
    card = pg.locator(".kanban-col .card").filter(has_text="Read 3 books")
    card.scroll_into_view_if_needed()
    b = card.bounding_box()
    x, y = b["x"] + b["width"] / 2, b["y"] + 20
    pg.evaluate(TOUCH, [x, y, "pointerdown"]); pg.wait_for_timeout(300)
    pg.evaluate(TOUCH, [x + 60, y, "pointermove"]); pg.evaluate(TOUCH, [x + 60, y, "pointerup"])
    assert pg.locator(".touch-ghost").count() == 0
    pg.evaluate(TOUCH, [x, y, "pointerdown"]); pg.wait_for_timeout(3200)
    assert pg.locator(".touch-ghost").count() == 1
    pg.evaluate(TOUCH, [x, y, "pointerup"])


def test_settings_tabs(browser, seeded):
    pg, errors = open_page(browser, seeded, True)
    pg.click(".menu-btn")
    pg.click(".sidebar.open .side-user .side-item")
    for tab in ("Account", "Sync accounts", "Phone sync", "Public links", "API & MCP"):
        pg.click(f".settings-tabs button:has-text('{tab}')")
        pg.wait_for_timeout(150)
        assert pg.evaluate(NO_SIDEWAYS), tab
    assert not errors


def test_changes_appear_live_and_tables_reopen_from_memory(browser, seeded):
    pg, errors = open_page(browser, seeded, False)
    u, t = seeded
    F = u.fields(t["id"])
    if pg.locator(".view-tab:has-text('Grid')").count():
        pg.click(".view-tab:has-text('Grid')")
    u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Added from a phone", F["component"]: "Task"}})
    pg.wait_for_selector("text=Added from a phone", timeout=8000)   # no reload: pushed over /api/events
    other = u.table(name="Other")
    pg.goto(f"{BASE}/#/table/{other['id']}")
    pg.wait_for_selector("h1:has-text('Other')")
    loads = []
    pg.on("request", lambda r: loads.append(r.url) if r.url.endswith(f"/api/tables/{t['id']}") else None)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.wait_for_selector("text=Added from a phone", timeout=3000)
    pg.wait_for_timeout(500)
    assert not loads, "reopening a table should use the copy in memory plus /changes"
    assert not errors, errors
