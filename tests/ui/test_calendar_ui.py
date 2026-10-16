"""Calendar views when several events happen at once: in the day / week grid, overlapping events that don't fit side by
side are one block saying how many, opening their list; a busy day of the month shows "+N more"."""
import datetime

from playwright.sync_api import expect

from conftest import BASE, Client
from test_ui_flows import browser, new_page, sign_in  # noqa: F401  (browser: the fixture)


def busy_calendar(u):
    t = u.table(name="Site visits")
    F = u.fields(t["id"])
    start = u.j("POST", f"/tables/{t['id']}/fields", json={"name": "Starts", "type": "date", "options": {"includeTime": True}})
    end = u.j("POST", f"/tables/{t['id']}/fields", json={"name": "Ends", "type": "date", "options": {"includeTime": True}})
    today = datetime.date.today()
    day = today.isoformat()
    add = lambda name, a, b: u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: name, start["id"]: a, end["id"]: b}})
    for i, n in enumerate(["Mill St panel", "Rivera EV charger", "Office LED", "Smoke detectors"]):     # four at once, 10–11
        add(n, f"{day}T10:{i * 5:02d}:00Z", f"{day}T11:00:00Z")
    add("Lunch", f"{day}T13:00:00Z", f"{day}T13:30:00Z")                                              # on its own
    for n in ("Permit pickup", "Order breakers", "Invoice Rivera"):                                    # three all-day ones today
        add(n, day, day)
    busy = (today.replace(day=15) if today.day != 15 else today.replace(day=16)).isoformat()          # a day of the month with six
    for i in range(6):
        add(f"Inspection {i + 1}", busy, busy)
    cal = u.j("POST", f"/tables/{t['id']}/views", json={"name": "Calendar", "type": "calendar",
                                                         "config": {"dateField": start["id"], "endField": end["id"], "calMode": "week"}})
    return t, cal, busy


def test_overlapping_events_become_one_block_that_lists_them(browser):
    u = Client()
    t, cal, busy = busy_calendar(u)
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}/{cal['id']}")
    stack = pg.locator(".tgrid-stack")
    expect(stack).to_have_count(1)
    expect(stack).to_contain_text("4 events")
    expect(pg.locator(".tgrid-block", has_text="Lunch")).to_have_count(1)           # an event on its own stays as it is
    expect(pg.locator(".tgrid-block", has_text="Mill St panel")).to_have_count(0)
    stack.click()
    items = pg.locator(".popover .tgrid-stack-item")
    expect(items).to_have_count(4)
    expect(items.first).to_contain_text("Mill St panel")
    items.nth(1).click()
    expect(pg.locator(".record-modal .record-title")).to_contain_text("Rivera EV charger")
    pg.keyboard.press("Escape")
    # more than two all-day events on a day: one block, listing them
    today_stack = pg.locator(".tgrid-allday-stack", has_text="3 events")
    expect(today_stack).to_have_count(1)
    expect(pg.locator(".tgrid-allday-item", has_text="Permit pickup")).to_have_count(0)
    today_stack.click()
    expect(pg.locator(".popover .tgrid-stack-item")).to_have_count(3)
    pg.keyboard.press("Escape")
    # the month: a busy day shows a few, then "+3 more", which lists them all
    pg.click(".cal-head .segmented button:has-text('Month')")
    more = pg.locator(f".cal-day[data-day='{busy}'] .cal-more")
    expect(more).to_have_text("+3 more")
    expect(pg.locator(f".cal-day[data-day='{busy}'] .cal-item")).to_have_count(3)
    more.click()
    expect(pg.locator(".popover .tgrid-stack-item")).to_have_count(6)
    assert not pg.errors, pg.errors


def test_on_a_phone_any_overlap_is_one_block(browser):
    u = Client()
    t, cal, _ = busy_calendar(u)
    pg = new_page(browser, phone=True)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}/{cal['id']}")
    expect(pg.locator(".tgrid-head .tgrid-day")).to_have_count(3)
    expect(pg.locator(".tgrid-allday-stack", has_text="3 events")).to_have_count(1)
    expect(pg.locator(".tgrid-stack")).to_contain_text("4 events")
    pg.locator(".tgrid-stack").click()
    expect(pg.locator(".popover .tgrid-stack-item")).to_have_count(4)
    assert pg.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1")
    assert not pg.errors, pg.errors


def test_events_overlapping_in_a_chain_are_grouped_where_they_meet(browser):
    """A morning like the one that looked wrong: overlaps one after another in a narrow week column. Each pair at the same
    time is one "2 events" block with their titles (not slivers with cut-off titles); short events at the same moment
    are recognised as overlapping too. On the day view there's room: they're side by side, each widened where it can."""
    u = Client()
    t = u.table(name="Bids calendar")
    F = u.fields(t["id"])
    start = u.j("POST", f"/tables/{t['id']}/fields", json={"name": "Starts", "type": "date", "options": {"includeTime": True}})
    end = u.j("POST", f"/tables/{t['id']}/fields", json={"name": "Ends", "type": "date", "options": {"includeTime": True}})
    day = datetime.date.today().isoformat()
    for name, a, b in (("Ticket: Boston", "09:00", "10:30"), ("Pre-bid Lowell", "10:00", "11:00"), ("Pre-bid Salem", "11:00", "12:00"),
                       ("Pre-bid Quincy", "11:00", "12:00"), ("Filed sub-bid A", "12:00", "12:10"), ("Filed sub-bid B", "12:05", "12:15"),
                       ("Request meeting", "14:00", "15:00")):
        u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: name, start["id"]: f"{day}T{a}:00Z", end["id"]: f"{day}T{b}:00Z"}})
    cal = u.j("POST", f"/tables/{t['id']}/views", json={"name": "Calendar", "type": "calendar",
                                                         "config": {"dateField": start["id"], "endField": end["id"], "calMode": "week"}})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}/{cal['id']}")
    stacks = pg.locator(".tgrid-stack")
    expect(stacks).to_have_count(3)
    expect(stacks.nth(0)).to_contain_text("2 events")
    expect(pg.locator(".tgrid-stack", has_text="Pre-bid Salem")).to_contain_text("Pre-bid Quincy")   # titles shown in the block
    expect(pg.locator(".tgrid-stack", has_text="Filed sub-bid A")).to_have_count(1)                   # 10-minute ones: drawn overlapping
    alone = pg.locator(".tgrid-block", has_text="Request meeting")
    expect(alone).to_have_count(1)
    col_w = pg.locator(".tgrid-cols").evaluate("e => e.clientWidth") / 7
    assert alone.evaluate("e => e.offsetWidth") > col_w - 8          # a lone event: the whole column
    # the day view: room for two side by side
    pg.click(".cal-head .segmented button:has-text('Day')")
    expect(pg.locator(".tgrid-stack")).to_have_count(0)
    a, b = (pg.locator(".tgrid-block", has_text=n) for n in ("Ticket: Boston", "Pre-bid Lowell"))
    ba, bb = a.bounding_box(), b.bounding_box()
    assert ba["x"] + ba["width"] <= bb["x"] + 1 and ba["width"] > 150
    assert not pg.errors, pg.errors
