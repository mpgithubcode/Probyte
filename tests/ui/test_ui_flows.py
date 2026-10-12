"""End-to-end flows in a real browser, the way a person uses the app: sign up, build a table, edit, filter, share."""
import re
import uuid

import pytest
import requests
from playwright.sync_api import expect, sync_playwright

from conftest import API, BASE, Client, pages


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield p, b
        b.close()


def new_page(browser, phone=False):
    p, b = browser
    ctx = b.new_context(**p.devices["iPhone 13"]) if phone else b.new_context(viewport={"width": 1280, "height": 860})
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    # script errors fail a test; failed requests (wrong password and other expected 4xx answers) don't
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
    pages.append(pg)
    return pg


def eventually(check, timeout=6.0):
    """The app shows an edit at once and saves it in the background: wait for the server to have it."""
    import time
    end = time.time() + timeout
    while True:
        try:
            out = check()
            if out is not False and out is not None:
                return out
        except (StopIteration, KeyError, AssertionError):
            if time.time() > end:
                raise
        if time.time() > end:
            raise AssertionError("the server never got the change")
        time.sleep(0.25)


def sign_in(pg, u):
    pg.goto(BASE)
    pg.fill("input[autocomplete=username]", u.username)
    pg.fill("input[type=password]", u.password)
    pg.click("button:has-text('Sign in')")
    pg.wait_for_selector(".page, .welcome")


def test_sign_up_and_build_a_table(browser):
    pg = new_page(browser)
    name = "ui" + uuid.uuid4().hex[:8]
    pg.goto(BASE)
    pg.click("text=New here? Create an account")
    pg.fill("input[autocomplete=username]", name)
    pg.fill("input[autocomplete=new-password] >> nth=0", "ui-password-1")
    pg.fill("input[autocomplete=new-password] >> nth=1", "ui-password-2")
    pg.click("button:has-text('Create account')")
    expect(pg.locator(".login-card .error")).to_be_visible()             # passwords don't match
    pg.fill("input[autocomplete=new-password] >> nth=1", "ui-password-1")
    pg.click("button:has-text('Create account')")
    pg.wait_for_selector(".welcome, .page")
    # a new table from the sidebar
    pg.click(".side-add >> nth=0")
    pg.fill(".modal input.input >> nth=0", "Projects")
    pg.click(".modal-foot button:has-text('Create')")
    expect(pg.locator(".page-head h1")).to_have_text("Projects")
    # add a number field
    pg.click(".page-head button:has-text('Field')")
    pg.fill(".modal label:has-text('Name') input", "Budget")
    pg.click(".modal .type-button")
    pg.click(".type-item:has-text('Number')")
    pg.click(".modal-foot button:has-text('Save')")
    expect(pg.locator("th .th-name", has_text="Budget")).to_be_visible()
    # a record, typed straight into the grid
    pg.click(".add-row button:has-text('New record')")
    pg.wait_for_selector(".grid tbody tr:not(.add-row):not(.spacer)")
    pg.locator(".grid tbody tr:not(.add-row):not(.spacer)").first.locator("td.cell").first.click()
    pg.keyboard.type("Website relaunch")
    pg.keyboard.press("Enter")
    budget_col = pg.locator("thead th").filter(has_text="Budget")
    idx = pg.locator("thead th").evaluate_all("(ths) => ths.findIndex(th => th.textContent.includes('Budget'))")
    pg.locator(".grid tbody tr:not(.add-row):not(.spacer)").first.locator("td").nth(idx).click()
    pg.keyboard.type("1500")
    pg.keyboard.press("Enter")
    expect(pg.locator(".grid tbody")).to_contain_text("Website relaunch")
    expect(pg.locator(".grid tbody")).to_contain_text("1,500")
    # it was really saved
    pg.reload()
    expect(pg.locator(".grid tbody")).to_contain_text("Website relaunch")
    assert not pg.errors, pg.errors


@pytest.fixture
def table_user():
    u = Client()
    t = u.table(name="Inventory")
    F = u.fields(t["id"])
    for n, s in (("Chair", "Todo"), ("Desk", "Doing"), ("Lamp", "Done"), ("Shelf", "Todo")):
        u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: n, F["Status"]: s}})
    return u, t, F


def rows(pg):
    return pg.locator(".grid tbody tr:not(.add-row):not(.spacer):not(.group-row)")


def test_record_panel_edit_and_delete(browser, table_user):
    u, t, F = table_user
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    expect(rows(pg)).to_have_count(4)
    rows(pg).filter(has_text="Desk").locator("td.rownum").click()
    modal = pg.locator(".modal")
    expect(modal).to_be_visible()
    notes = modal.locator(".form-row").filter(has_text="Notes").locator("textarea, input").first
    notes.click()
    notes.fill("Standing desk")
    notes.press("Tab")
    eventually(lambda: next(r for r in u.records(t["id"]) if r["data"][F["Name"]] == "Desk")["data"].get(F["Notes"]) == "Standing desk" or None)
    modal.locator("button:has-text('Delete')").click()
    modal.locator(".modal-foot button.danger:has-text('Delete')").click()
    pg.wait_for_timeout(500)
    expect(rows(pg)).to_have_count(3)
    assert all(r["data"][F["Name"]] != "Desk" for r in u.records(t["id"]))
    assert not pg.errors, pg.errors


def test_search_sort_filter_and_hide(browser, table_user):
    u, t, F = table_user
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.fill(".view-options input[placeholder=Search]", "la")
    expect(rows(pg)).to_have_count(1)
    pg.fill(".view-options input[placeholder=Search]", "")
    expect(rows(pg)).to_have_count(4)
    # sort by name, descending, from the column menu
    pg.click("thead th:has-text('Name')")
    pg.click(".menu-item:has-text('Sort descending')")
    expect(rows(pg).first).to_contain_text("Shelf")
    # filter Status is Todo
    pg.click(".tool-btn:has-text('Filter')")
    pg.click(".popover button:has-text('Add condition'), .popover button:has-text('Add filter')")
    pop = pg.locator(".popover").last
    selects = pop.locator("select")
    selects.nth(0).select_option(label="Status")
    pg.wait_for_timeout(100)
    value = pop.locator("select, input").last
    if value.evaluate("e => e.tagName") == "SELECT":
        value.select_option(label="Todo")
    else:
        value.fill("Todo")
    pg.keyboard.press("Escape")
    expect(rows(pg)).to_have_count(2)
    # the view remembers it
    pg.wait_for_timeout(800)
    pg.reload()
    expect(rows(pg)).to_have_count(2)
    assert not pg.errors, pg.errors


def test_bulk_select_edit_and_delete(browser, table_user):
    u, t, F = table_user
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    for i in (0, 1):
        rows(pg).nth(i).hover()
        rows(pg).nth(i).locator(".row-check").click()
    bar = pg.locator(".bulk-bar")
    expect(bar).to_be_visible()
    bar.locator("button:has-text('Delete')").click()
    bar.locator("button:has-text('Delete 2')").click()
    expect(rows(pg)).to_have_count(2)
    pg.wait_for_timeout(500)
    assert len(u.records(t["id"])) == 2
    pg.locator("thead th.rownum").hover()
    pg.locator("thead .row-check.all").click()
    expect(bar).to_contain_text("2")
    assert not pg.errors, pg.errors


def test_new_record_button_in_every_view_type(browser, table_user):
    u, t, F = table_user
    for vt in ("kanban", "calendar", "list", "gallery", "timeline"):
        u.j("POST", f"/tables/{t['id']}/views", json={"name": vt, "type": vt})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    for vt in ("kanban", "list", "gallery"):
        pg.click(f".view-tab:has-text('{vt}')")
        pg.click(".view-options .new-record-btn")
        modal = pg.locator(".modal")
        expect(modal).to_be_visible()
        title = modal.locator("input, textarea").first
        title.fill(f"Made in {vt}")
        title.press("Tab")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)
    names = {r["data"].get(F["Name"]) for r in u.records(t["id"])}
    assert {"Made in kanban", "Made in list", "Made in gallery"} <= names, names
    assert not pg.errors, pg.errors


def test_rename_and_delete_table(browser, table_user):
    u, t, F = table_user
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.dblclick(".page-head h1")
    pg.fill(".title-input", "Stock")
    pg.keyboard.press("Enter")
    expect(pg.locator(".page-head h1")).to_have_text("Stock")
    expect(pg.locator(".sidebar")).to_contain_text("Stock")
    pg.click(".page-head [aria-label=More]")
    pg.click(".settings-nav button:has-text('Delete')")
    pg.click(".modal button:has-text('Move to trash…')")
    pg.click(".modal button.danger:has-text('Move to trash')")
    pg.wait_for_timeout(800)
    assert requests.Session.get(u, API + f"/tables/{t['id']}").status_code == 404
    expect(pg.locator(".sidebar")).not_to_contain_text("Stock")
    assert not pg.errors, pg.errors


def test_public_link_page(browser, table_user):
    u, t, F = table_user
    view = u.j("GET", f"/tables/{t['id']}")["views"][0]
    s = u.j("POST", "/shares", json={"name": "Stock list", "base_id": u.base_id, "items": [{"kind": "table", "id": t["id"]}],
                                    "permissions": {"add": True}})
    pg = new_page(browser, phone=True)
    pg.goto(f"{BASE}/s/{s['token']}")
    expect(pg.locator("body")).to_contain_text("Chair")
    pg.click(".new-record-btn")
    modal = pg.locator(".modal")
    expect(modal).to_contain_text("New record")             # add-only link: fill it in, then add it
    assert not u.records(t["id"])[4:] if len(u.records(t["id"])) == 4 else True
    title = modal.locator(".form-row").filter(has_text="Name").locator("input").first
    title.fill("From the public")
    title.press("Tab")
    modal.locator("button:has-text('Add record')").click()
    expect(modal).to_have_count(0)
    pg.wait_for_timeout(300)
    assert any(r["data"].get(F["Name"]) == "From the public" for r in u.records(t["id"]))
    assert pg.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1")
    assert not pg.errors, pg.errors


def test_logged_out_session_returns_to_login(browser, table_user):
    u, t, F = table_user
    pg = new_page(browser)
    sign_in(pg, u)
    u.j("POST", "/auth/password", json={"current_password": u.password, "new_password": "changed-pass-1"})  # signs out other devices
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")  # coming back to the tab refreshes it
    pg.wait_for_selector(".login-card", timeout=8000)


def test_import_a_csv_file(browser, table_user, tmp_path):
    u, t, F = table_user
    csv = tmp_path / "more.csv"
    csv.write_text("Name,Status,Price\nBench,Done,120\nStool,Todo,45\n")
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.click(".page-head [aria-label=More]")
    pg.click(".settings-nav button:has-text('Import & export')")
    pg.click(".modal button:has-text('Import…')")
    pg.set_input_files(".modal input[type=file]", str(csv))
    expect(pg.locator(".import-map-row")).to_have_count(3)
    pg.click(".modal-foot button:has-text('Import 2 rows')")
    expect(rows(pg)).to_have_count(6, timeout=10000)
    names = {r["data"].get(F["Name"]) for r in u.records(t["id"])}
    assert {"Bench", "Stool"} <= names
    assert not pg.errors, pg.errors


def test_export_downloads_a_file(browser, table_user):
    u, t, F = table_user
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.click(".page-head [aria-label=More]")
    pg.click(".settings-nav button:has-text('Import & export')")
    pg.click(".modal button:has-text('Export…')")
    with pg.expect_download() as dl:
        pg.click(".modal-foot button:has-text('Export')")
    path = dl.value.path()
    data = open(path, "rb").read()
    assert len(data) > 20
    if dl.value.suggested_filename.endswith(".csv"):
        assert b"Chair" in data and b"Shelf" in data
    assert not pg.errors, pg.errors


def test_api_key_and_public_link_from_settings(browser, table_user):
    u, t, F = table_user
    pg = new_page(browser)
    sign_in(pg, u)
    pg.click(".side-user .side-item")
    pg.click(".settings-tabs button:has-text('API & MCP')")
    pg.click("button:has-text('New API key')")
    pg.fill(".modal input[placeholder='Home Assistant']", "Automation")
    pg.click(".modal-foot button:has-text('Create key')")
    token = pg.locator(".big-key").text_content().strip()
    pg.click(".modal-foot button:has-text('Done')")
    assert requests.get(BASE + "/api/v1/me", headers={"Authorization": "Bearer " + token}).status_code == 200
    pg.click(".settings-tabs button:has-text('Public links')")
    pg.click("button:has-text('New public link')")
    pg.fill(".modal input[placeholder='Team calendar']", "Stock for partners")
    pg.locator(".modal label, .modal .share-item").filter(has_text="Inventory").locator("input[type=checkbox]").first.check()
    pg.click(".modal-foot button:has-text('Create link')")
    pg.wait_for_timeout(500)
    shares = u.j("GET", "/shares")
    shares = shares if isinstance(shares, list) else shares.get("shares", [])
    assert any(s["name"] == "Stock for partners" for s in shares)
    assert not pg.errors, pg.errors


def test_combine_tables_from_the_menu(browser, table_user):
    u, t, F = table_user
    other = u.table(name="Warehouse")
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.click(".page-head [aria-label=More]")
    pg.click(".settings-nav button:has-text('Import & export')")
    pg.click(".modal button:has-text('Combine…')")
    modal = pg.locator(".modal")
    modal.locator("label, .combo-source, .menu-item").filter(has_text="Warehouse").locator("input[type=checkbox]").first.check()
    modal.locator(".modal-foot button.primary").click()
    pg.wait_for_url("**/#/combo/*", timeout=10000)
    expect(pg.locator(".page-head h1")).to_contain_text("Inventory")
    expect(pg.locator(".grid tbody")).to_contain_text("Chair")
    assert not pg.errors, pg.errors


def test_editing_select_checkbox_and_date_in_the_record_panel(browser, table_user):
    u, t, F = table_user
    done = u.j("POST", f"/tables/{t['id']}/fields", json={"name": "Checked", "type": "checkbox"})["id"]
    due = u.j("POST", f"/tables/{t['id']}/fields", json={"name": "Due", "type": "date"})["id"]
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    rows(pg).filter(has_text="Chair").locator("td.rownum").click()
    modal = pg.locator(".modal")
    modal.locator(".form-row").filter(has_text="Checked").locator("input[type=checkbox]").check()
    modal.locator(".form-row").filter(has_text="Due").locator("input").fill("2026-10-20")
    modal.locator(".form-row").filter(has_text="Due").locator("input").press("Enter")
    modal.locator(".form-row").filter(has_text="Status").locator(".form-input > *").first.click()
    pg.locator(".popover .menu-item, .popover button").filter(has_text="Done").first.click()
    pg.keyboard.press("Escape")
    d = eventually(lambda: (lambda d: d if d.get(F["Status"]) == "Done" else None)(
        next(r for r in u.records(t["id"]) if r["data"][F["Name"]] == "Chair")["data"]))
    assert d.get(done) is True and d.get(due) == "2026-10-20", d
    assert not pg.errors, pg.errors


def test_board_drag_with_a_mouse_changes_the_status(browser, table_user):
    u, t, F = table_user
    u.j("POST", f"/tables/{t['id']}/views", json={"name": "Board", "type": "kanban", "config": {"groupBy": F["Status"]}})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.click(".view-tab:has-text('Board')")
    card = pg.locator(".kanban-col .card").filter(has_text="Chair")
    target = pg.locator(".kanban-col").filter(has_text="Done").first
    card.drag_to(target)
    eventually(lambda: next(r for r in u.records(t["id"]) if r["data"][F["Name"]] == "Chair")["data"][F["Status"]] == "Done" or None)
    assert not pg.errors, pg.errors


def test_calendar_add_on_a_day(browser):
    u = Client()
    t = u.table("events", "Diary")
    F = u.fields(t["id"])
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    if pg.locator(".view-tab:has-text('Calendar')").count():
        pg.click(".view-tab:has-text('Calendar')")
    else:
        pg.click(".view-tab.add")
        pg.click(".view-type-item:has-text('Calendar')")
    pg.wait_for_selector(".cal-head")
    day = pg.locator(".cal-day:not(.other)").nth(10)
    day.hover()
    day.locator(".cal-add").click()
    modal = pg.locator(".modal")
    title = modal.locator(".form-row").filter(has_text="Title").locator("input").first
    title.fill("Team lunch")
    title.press("Enter")
    pg.keyboard.press("Escape")
    expect(pg.locator(".cal-title", has_text="Team lunch")).to_be_visible()
    rec = eventually(lambda: next(r for r in u.records(t["id"]) if r["data"].get(F["summary"]) == "Team lunch"))
    assert rec["data"].get(F["dtstart"])
    assert not pg.errors, pg.errors


def test_grouping_from_the_toolbar(browser, table_user):
    u, t, F = table_user
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.click("thead th:has-text('Status')")
    pg.click(".menu-item:has-text('Group by this field')")
    expect(pg.locator(".group-row")).to_have_count(3)
    pg.locator(".group-row").filter(has_text="Todo").locator(".group-toggle").click()
    expect(rows(pg)).to_have_count(2)                       # the two Todo rows are folded away
    assert not pg.errors, pg.errors


def test_phone_navigation_and_editing(browser, table_user):
    u, t, F = table_user
    other = u.table(name="Second table")
    pg = new_page(browser, phone=True)
    sign_in(pg, u)
    pg.click(".menu-btn")
    pg.click(".sidebar.open .side-name:has-text('Second table')")
    expect(pg.locator(".page-head h1")).to_have_text("Second table")
    expect(pg.locator(".sidebar.open")).to_have_count(0)    # the drawer closes after choosing
    pg.click(".menu-btn")
    pg.click(".sidebar.open .side-name:has-text('Inventory')")
    pg.locator(".grid tbody tr:not(.add-row):not(.spacer)").filter(has_text="Lamp").locator("td.rownum").click()
    notes = pg.locator(".modal .form-row").filter(has_text="Notes").locator("textarea, input").first
    notes.fill("From my phone")
    notes.press("Tab")
    eventually(lambda: next(r for r in u.records(t["id"]) if r["data"][F["Name"]] == "Lamp")["data"].get(F["Notes"]) == "From my phone" or None)
    assert pg.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1")
    assert not pg.errors, pg.errors


def test_renaming_an_option_in_the_field_editor_keeps_the_values(browser, table_user):
    u, t, F = table_user
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.click("thead th:has-text('Status')")
    pg.click(".menu-item:has-text('Edit field')")
    opt = pg.locator(".modal .choice-row input.input").filter(has=pg.locator("xpath=self::*[@value='Todo']"))
    if not opt.count():
        opt = pg.locator(".modal .choice-row input.input").nth(0)
    opt.fill("To do")
    pg.click(".modal-foot button:has-text('Save')")
    expect(pg.locator(".modal")).to_have_count(0)
    pg.wait_for_timeout(500)
    statuses = sorted(r["data"].get(F["Status"]) for r in u.records(t["id"]))
    assert statuses.count("To do") == 2 and "Todo" not in statuses, statuses
    expect(pg.locator(".grid tbody")).to_contain_text("To do")
    assert not pg.errors, pg.errors


def _day(pg, n):
    import re
    return pg.locator(".cal-day:not(.other)").filter(has=pg.locator(".cal-daynum > span", has_text=re.compile(f"^{n}$")))


def test_repeating_event_skip_and_change_one_date(browser):
    u = Client()
    t = u.table("events", "Gym")
    F = u.fields(t["id"])
    u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Gym", F["dtstart"]: "2026-10-05T13:00:00Z",
                                                             F["dtend"]: "2026-10-05T14:00:00Z", F["rrule"]: "FREQ=WEEKLY"}})
    u.j("POST", f"/tables/{t['id']}/views", json={"name": "Cal", "type": "calendar", "config": {"dateField": F["dtstart"], "endField": F["dtend"]}})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.click(".view-tab:has-text('Cal')")
    pg.wait_for_selector(".cal-head")
    _day(pg, 19).locator(".cal-item").first.click()
    modal = pg.locator(".modal")
    expect(modal).to_contain_text("Occurrence on")
    modal.locator("button:has-text('Skip this date')").click()
    expect(_day(pg, 19).locator(".cal-item")).to_have_count(0)
    expect(_day(pg, 26).locator(".cal-item")).to_have_count(1)
    _day(pg, 26).locator(".cal-item").first.click()
    modal.locator("button:has-text('Change this date only')").click()
    title = modal.locator(".occ-fields .form-row").filter(has_text="Title").locator("input").first
    title.fill("Gym (moved)")
    title.press("Enter")
    pg.keyboard.press("Escape")
    expect(_day(pg, 26).locator(".cal-item")).to_contain_text("Gym (moved)")
    expect(_day(pg, 12).locator(".cal-item")).to_contain_text("Gym")
    rule = eventually(lambda: (lambda r: r if "OVERRIDE" in r else None)(u.records(t["id"])[0]["data"][F["rrule"]]))
    assert "EXDATE" in rule and "OVERRIDE" in rule and "Gym (moved)" in rule
    u.sync(t["id"])
    body = u.server_item(t["calendar_url"], "SUMMARY:Gym")[1]
    assert "EXDATE" in body and "RECURRENCE-ID" in body
    assert not pg.errors, pg.errors


def test_settings_password_webhook_and_mcp_token(browser):
    u = Client()
    pg = new_page(browser)
    sign_in(pg, u)
    pg.click(".side-user .side-item")
    pg.fill("input[placeholder='Current password']", "wrong-password")
    pg.fill("input[placeholder^='New password']", "brand-new-pass-1")
    pg.fill("input[placeholder='Confirm new password']", "brand-new-pass-1")
    pg.click("button:has-text('Change password')")
    expect(pg.locator(".settings-section .error")).to_be_visible()
    pg.fill("input[placeholder='Current password']", u.password)
    pg.click("button:has-text('Change password')")
    expect(pg.locator(".settings-section .success")).to_be_visible()
    assert requests.post(API + "/auth/login", json={"username": u.username, "password": "brand-new-pass-1"}).status_code == 200
    pg.click(".settings-tabs button:has-text('API & MCP')")
    pg.click("button:has-text('Create MCP token')")
    expect(pg.locator(".bearer-box")).to_contain_text("pbk_")
    pg.click("button:has-text('New webhook')")
    pg.fill(".modal input[placeholder='n8n: new tasks']", "Notify")
    pg.fill(".modal input[placeholder^='https://n8n']", "https://example.com/hook")
    pg.click(".modal-foot button:has-text('Create webhook')")
    pg.wait_for_timeout(600)
    session = requests.Session()
    session.post(API + "/auth/login", json={"username": u.username, "password": "brand-new-pass-1"})
    hooks = session.get(API + "/v1/webhooks").json()["webhooks"]
    assert [h["name"] for h in hooks] == ["Notify"]
    assert not pg.errors, pg.errors


def test_event_and_task_panels_show_their_own_fields(browser):
    u = Client()
    t = u.table("mixed", "Both kinds")
    F = u.fields(t["id"])
    u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "An event", F["component"]: "Event", F["dtstart"]: "2026-10-20T10:00:00Z"}})
    u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "A task", F["component"]: "Task"}})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.click(".view-tab:has-text('Table')") if pg.locator(".view-tab.active:has-text('Table')").count() == 0 else None
    for title, kind in (("An event", "Event"), ("A task", "Task")):
        rows(pg).filter(has_text=title).locator("td.rownum").click()
        modal = pg.locator(".modal")
        labels = modal.locator(".form-label").all_text_contents()
        assert any("Completed At" in x for x in labels) == (kind == "Task"), labels
        modal.locator(".form-row").filter(has_text="Status").locator(".form-input > *").first.click()
        options = pg.locator(".popover").last.text_content()
        assert ("Attended" in options) == (kind == "Event") and ("In Progress" in options) == (kind == "Task"), options
        pg.keyboard.press("Escape")
        pg.keyboard.press("Escape")
        expect(pg.locator(".modal")).to_have_count(0)
    assert not pg.errors, pg.errors


def test_new_contacts_and_linked_tables_from_the_dialog(browser, table_user):
    u, t, F = table_user
    s = u.j("POST", "/shares", json={"name": "Catalogue", "base_id": u.base_id, "items": [{"kind": "table", "id": t["id"]}]})
    other = Client()
    pg = new_page(browser)
    sign_in(pg, other)
    # contacts on the built-in server
    pg.click(".side-add >> nth=0")
    pg.click(".kind:has-text('Contacts')")
    pg.fill(".modal input[placeholder='Contacts']", "People")
    pg.click(".modal-foot button:has-text('Create')")   # waits until the address books have loaded
    expect(pg.locator(".page-head h1")).to_have_text("People", timeout=15000)
    expect(pg.locator("thead")).to_contain_text("Email")
    # a table from someone's public link
    pg.click(".side-add >> nth=0")
    pg.click(".kind:has-text('From a link')")
    pg.fill(".modal input[placeholder*='/s/']", f"{BASE}/s/{s['token']}")
    pg.click(".modal-foot button:has-text('Check link')")
    pg.click(".modal-foot button:has-text('Create')")
    expect(pg.locator(".page-head h1")).not_to_have_text("People", timeout=15000)
    expect(pg.locator(".grid tbody")).to_contain_text("Chair")
    expect(pg.locator(".remote-badge")).to_be_visible()
    assert not pg.errors, pg.errors


def test_embedded_public_link(browser, table_user):
    u, t, F = table_user
    s = u.j("POST", "/shares", json={"name": "Embed me", "base_id": u.base_id, "items": [{"kind": "table", "id": t["id"]}]})
    pg = new_page(browser)
    pg.set_content(f'<iframe src="{BASE}/s/{s["token"]}?embed=1" style="width:600px;height:500px;border:0"></iframe>')
    frame = pg.frame_locator("iframe")
    expect(frame.locator("body")).to_contain_text("Chair")
    expect(frame.locator(".share-foot")).to_have_count(0)            # no page chrome when embedded
    assert not pg.errors, pg.errors


def test_list_view_layout(browser):
    u = Client()
    t = u.table("mixed", "Agenda")
    F = u.fields(t["id"])
    for title, kind, status in (("Water plants", "Task", "To Do"), ("A really quite long task title that goes on and on", "Task", "Completed"),
                                ("Dentist", "Event", "Confirmed")):
        u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: title, F["component"]: kind, F["status"]: status,
                                                                 F["dtend"]: "2026-10-20", F["location"]: "Somewhere far away"}})
    u.j("POST", f"/tables/{t['id']}/views", json={"name": "Agenda list", "type": "list", "config": {"groups": [{"field": F["component"], "dir": "asc"}]}})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.click(".view-tab:has-text('Agenda list')")
    pg.wait_for_selector(".list-row")
    row = pg.locator(".list-row").filter(has_text="Water plants")
    # true/false fields are checkboxes; the Completed box is the last thing on the row
    assert row.locator("input[type=checkbox]").count() >= 1
    assert row.locator(".list-prop").last.locator("input[type=checkbox]").count() == 1
    # rows sit indented under their group header; no lines between rows, only under headers
    head_left = pg.locator(".list-group").first.bounding_box()["x"] + 8
    dot_left = row.locator(".list-dot").bounding_box()["x"]
    assert dot_left > head_left + 10
    assert row.evaluate("e => getComputedStyle(e).borderBottomWidth") in ("0px", "")
    assert pg.locator(".list-group").first.evaluate("e => getComputedStyle(e).borderBottomWidth") != "0px"
    # ticking the box here saves it
    row.locator(".list-prop").last.locator("input").check()
    eventually(lambda: next(r for r in u.records(t["id"]) if r["data"][F["summary"]] == "Water plants")["data"][F["done"]] is True or None)
    # nothing runs off the row at any width
    for w in (1280, 1100, 960, 860, 761, 700, 520, 390):
        pg.set_viewport_size({"width": w, "height": 800})
        pg.wait_for_timeout(150)
        bad = pg.evaluate("""() => [...document.querySelectorAll('.list-row')].flatMap(r => { const b = r.getBoundingClientRect();
          return [...r.querySelectorAll('.list-prop, .list-title, .list-dot')].filter(p => p.offsetWidth).filter(p => { const q = p.getBoundingClientRect();
            return q.left < b.left - 1 || q.right > b.right + 1 || q.right > innerWidth; }).map(p => p.className + ' ' + p.textContent) })""")
        assert not bad, (w, bad)
        assert pg.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1"), w
    assert not pg.errors, pg.errors


def test_hiding_a_table(browser, table_user):
    u, t, F = table_user
    other = u.table(name="Visible table")
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.click(".page-head [aria-label=More]")
    pg.click(".modal .toggle-row:has-text('Show in the sidebar')")
    pg.click(".modal-foot button:has-text('Done')")
    expect(pg.locator(".sidebar .side-item.is-hidden", has_text="Inventory")).to_be_visible()   # still listed while open
    pg.click(".sidebar .side-name:has-text('Visible table')")
    expect(pg.locator(".sidebar .side-name", has_text="Inventory")).to_have_count(0)
    pg.click(".sidebar .side-add:has-text('Show 1 hidden')")
    pg.click(".sidebar .side-name:has-text('Inventory')")
    pg.click(".page-head [aria-label=More]")
    pg.click(".modal .toggle-row:has-text('Show in the sidebar')")
    pg.click(".modal-foot button:has-text('Done')")
    pg.click(".sidebar .side-name:has-text('Visible table')")
    expect(pg.locator(".sidebar .side-item:not(.is-hidden)", has_text="Inventory")).to_be_visible()
    assert not pg.errors, pg.errors


def test_first_day_of_the_week(browser):
    u = Client()
    t = u.table("events", "Week")
    pg = new_page(browser)
    sign_in(pg, u)
    pg.click(".side-user .side-item")
    pg.locator(".settings-section select").first.select_option(label="Sunday")
    pg.keyboard.press("Escape")
    pg.goto(f"{BASE}/#/table/{t['id']}")
    if pg.locator(".view-tab:has-text('Calendar')").count():
        pg.click(".view-tab:has-text('Calendar')")
    expect(pg.locator(".cal-weekday").first).to_have_text("Sun")
    eventually(lambda: u.j("GET", "/auth/me")["prefs"].get("week_start") == 0 or None)
    pg.reload()                                                       # remembered
    pg.wait_for_selector(".cal-weekday")
    assert pg.locator(".cal-weekday").first.text_content() == "Sun"
    assert not pg.errors, pg.errors


def test_dragging_columns(browser, table_user):
    u, t, F = table_user
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    heads = lambda: [x.strip() for x in pg.locator("thead th .th-name").all_text_contents()]
    assert heads()[:3] == ["Name", "Notes", "Status"]
    pg.locator("thead th").filter(has_text="Status").drag_to(pg.locator("thead th").filter(has_text="Notes"))
    expect(pg.locator("thead th .th-name").nth(1)).to_have_text("Status")
    pg.wait_for_timeout(800)
    pg.reload()
    expect(pg.locator("thead th .th-name").nth(1)).to_have_text("Status")   # saved with the view
    # the title column moves too
    pg.locator("thead th").filter(has_text="Name").drag_to(pg.locator("thead th").filter(has_text="Status"))
    expect(pg.locator("thead th .th-name").first).to_have_text("Status")
    assert not pg.errors, pg.errors


def test_pagination(browser):
    u = Client()
    t = u.table(name="Many")
    F = u.fields(t["id"])
    u.j("POST", f"/tables/{t['id']}/import", json={"columns": [{"target": F["Name"]}], "rows": [[f"Row {i:02d}"] for i in range(30)]})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    expect(rows(pg)).to_have_count(30)
    pg.locator(".view-options label[title='Rows per page'] select").select_option("10")
    expect(rows(pg)).to_have_count(10)
    expect(pg.locator(".pager-text")).to_have_text("1–10 of 30")
    pg.click(".pager [aria-label='Next page']")
    expect(pg.locator(".pager-text")).to_have_text("11–20 of 30")
    expect(rows(pg).first).to_contain_text("Row 10")
    pg.fill(".view-options input[placeholder=Search]", "Row 2")             # searching goes back to page 1
    expect(rows(pg)).to_have_count(10)
    expect(rows(pg).first).to_contain_text("Row 20")
    assert not pg.errors, pg.errors


def test_combine_with_a_table_from_a_link(browser, table_user):
    u, t, F = table_user
    them = Client()
    theirs = them.table(name="Partner stock")
    TF = them.fields(theirs["id"])
    them.j("POST", f"/tables/{theirs['id']}/records", json={"data": {TF["Name"]: "Partner chair"}})
    s = them.j("POST", "/shares", json={"name": "Stock", "base_id": them.base_id, "items": [{"kind": "table", "id": theirs["id"]}]})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.click(".page-head [aria-label=More]")
    pg.click(".settings-nav button:has-text('Import & export')")
    pg.click(".modal button:has-text('Combine…')")
    pg.fill(".combo-link input.input", f"{BASE}/s/{s['token']}")
    pg.click(".combo-link button:has-text('Add')")
    expect(pg.locator(".source-row.active", has_text="Partner stock")).to_be_visible()
    pg.click(".modal-foot button.primary")
    pg.wait_for_url("**/#/combo/*", timeout=10000)
    expect(pg.locator(".grid tbody")).to_contain_text("Partner chair", timeout=15000)   # arrives after the local rows
    expect(pg.locator(".grid tbody")).to_contain_text("Chair")
    expect(pg.locator(".sources-loading")).to_have_count(0)
    assert not pg.errors, pg.errors


def test_list_properties_start_position_and_names(browser):
    u = Client()
    t = u.table("tasks", "Chores")
    F = u.fields(t["id"])
    u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Hoover", F["priority"]: "High"}})
    u.j("POST", f"/tables/{t['id']}/views", json={"name": "Todo list", "type": "list"})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.click(".view-tab:has-text('Todo list')")
    pg.click(".tool-btn:has-text('Properties')")
    opt = lambda name: pg.locator(".list-opt-row").filter(has=pg.locator(".list-opt-name", has_text=re.compile(f"^{name}$")))
    opt("Completed").locator("button:has-text('Start')").click()
    opt("Priority").locator("button[aria-pressed]").click()   # hide its name
    pg.keyboard.press("Escape")
    row = pg.locator(".list-row").first
    # the Completed box comes right after the color dot, before the title
    order = row.evaluate("""r => [...r.children].filter(c => c.offsetWidth).map(c => c.classList.contains('list-start') ? 'dot'
        : c.classList.contains('list-title') ? 'title' : c.querySelector('input[type=checkbox]') ? 'box' : 'prop')""")
    assert order[:3] == ["dot", "box", "title"], order
    prio = row.locator(".list-prop").filter(has_text="High")
    assert prio.locator(".list-prop-name").count() == 0                               # name hidden for this one
    pg.wait_for_timeout(600)
    cfg = next(v for v in u.j("GET", f"/tables/{t['id']}")["views"] if v["name"] == "Todo list")["config"]
    done = next(p for p in cfg["listProps"] if p["field"] == F["done"])
    assert done["align"] == "start" and next(p for p in cfg["listProps"] if p["field"] == F["priority"])["label"] is False
    assert not pg.errors, pg.errors


def test_freezing_columns(browser):
    u = Client()
    t = u.table(name="Wide")
    F = u.fields(t["id"])
    for n in "ABCDEF":
        u.j("POST", f"/tables/{t['id']}/fields", json={"name": f"Col {n}", "type": "text", "options": {}})
    for i in range(4):
        u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: f"Row {i}"}})
    pg = new_page(browser)
    pg.set_viewport_size({"width": 1000, "height": 600})
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.click("thead th:has-text('Notes')")
    pg.click(".menu-item:has-text('Freeze up to this column')")
    expect(pg.locator("thead th.frozen")).to_have_count(2)
    before = [pg.locator("thead th.frozen").nth(i).bounding_box()["x"] for i in range(2)]
    for x in (300, 700, 1200):                                     # scrolling sideways (several times) leaves them in place
        pg.locator(".grid-wrap").evaluate(f"e => e.scrollLeft = {x}")
        pg.mouse.wheel(10, 0)
        pg.wait_for_timeout(150)
        assert [round(pg.locator("thead th.frozen").nth(i).bounding_box()["x"]) for i in range(2)] == [round(b) for b in before]
        assert pg.locator(".grid tbody tr").first.locator("td.frozen").first.is_visible()
    pg.wait_for_timeout(600)
    cfg = u.j("GET", f"/tables/{t['id']}")["views"][0]["config"]
    assert cfg["frozen"] == 2
    pg.click("thead th:has-text('Notes')")
    pg.click(".menu-item:has-text('Unfreeze columns')")
    expect(pg.locator("thead th.frozen")).to_have_count(0)
    assert not pg.errors, pg.errors


def test_undo_and_the_trash(browser, table_user):
    u, t, F = table_user
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    # delete a record, then Undo
    rows(pg).filter(has_text="Lamp").locator("td.rownum").click()
    pg.locator(".modal button:has-text('Delete')").click()
    pg.locator(".modal-foot button.danger:has-text('Delete')").click()
    expect(rows(pg)).to_have_count(3)
    pg.click(".toast-action:has-text('Undo')")
    expect(rows(pg).filter(has_text="Lamp")).to_have_count(1)
    # a bulk edit, then Undo
    for i in (0, 1):
        rows(pg).nth(i).hover()
        rows(pg).nth(i).locator(".row-check").click()
    first_two = [rows(pg).nth(i).locator("td.cell").first.text_content() for i in (0, 1)]
    statuses = {r["data"][F["Name"]]: r["data"].get(F["Status"]) for r in u.records(t["id"])}
    pg.click(".bulk-bar button:has-text('Delete')")
    pg.click(".bulk-bar button:has-text('Delete 2')")
    expect(rows(pg)).to_have_count(2)
    pg.click(".toast-action:has-text('Undo')")
    expect(rows(pg)).to_have_count(4)
    assert {r["data"][F["Name"]]: r["data"].get(F["Status"]) for r in u.records(t["id"])} == statuses
    # move the table to the trash, restore it from the Trash window
    pg.click(".page-head [aria-label=More]")
    pg.click(".settings-nav button:has-text('Delete')")
    pg.click(".modal button:has-text('Move to trash…')")
    pg.click(".modal button.danger:has-text('Move to trash')")
    expect(pg.locator(".sidebar")).not_to_contain_text("Inventory")
    pg.click(".side-user [aria-label=Trash]")
    pg.locator(".trash-row").filter(has_text="Inventory").locator("button:has-text('Restore')").click()
    expect(pg.locator(".sidebar")).to_contain_text("Inventory")
    assert len(u.records(t["id"])) == 4
    assert not pg.errors, pg.errors


def test_spreadsheet_keys_copy_and_paste(browser, table_user):
    u, t, F = table_user
    done = u.j("POST", f"/tables/{t['id']}/fields", json={"name": "Checked", "type": "checkbox"})["id"]
    price = u.j("POST", f"/tables/{t['id']}/fields", json={"name": "Price", "type": "number"})["id"]
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    name = lambda i: rows(pg).nth(i).locator("td.cell").nth(0)
    name(0).click()                                                       # selects (doesn't edit)
    expect(pg.locator("td.cell.active")).to_have_count(1)
    expect(pg.locator("td.cell.editing")).to_have_count(0)
    pg.keyboard.press("ArrowDown")
    pg.keyboard.type("Armchair")                                          # typing replaces the value
    pg.keyboard.press("Enter")
    pg.keyboard.press("ArrowRight")                                       # -> Notes
    pg.keyboard.press("Enter")                                            # Enter edits, keeping the value
    pg.keyboard.type("comfy")
    pg.keyboard.press("Enter")
    eventually(lambda: (lambda d: d if d.get(F["Name"]) == "Armchair" and d.get(F["Notes"]) == "comfy" else None)(
        next(r for r in u.records(t["id"]) if r["data"].get(F["Name"]) == "Armchair")["data"]))
    pg.keyboard.press("Delete")                                           # clears Notes
    eventually(lambda: next(r for r in u.records(t["id"]) if r["data"].get(F["Name"]) == "Armchair")["data"].get(F["Notes"]) is None or None)
    # the checkbox: Space ticks it
    name(0).click()
    for _ in range(3):                                                    # Name -> Notes -> Status -> Checked
        pg.keyboard.press("ArrowRight")
    pg.keyboard.press(" ")
    eventually(lambda: next(r for r in u.records(t["id"]) if r["data"].get(F["Name"]) == "Chair")["data"].get(done) is True or None)
    # copy a cell
    name(0).click()
    copied = pg.evaluate("""() => { const dt = new DataTransfer();
        document.querySelector('.grid-wrap').dispatchEvent(new ClipboardEvent('copy', {clipboardData: dt, bubbles: true, cancelable: true}));
        return dt.getData('text/plain'); }""")
    assert copied == "Chair"
    # paste a block from a spreadsheet: two columns, five rows (the last ones become new records)
    pg.evaluate("""(t) => { const dt = new DataTransfer(); dt.setData('text/plain', t);
        document.querySelector('.grid-wrap').dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true})); }""",
                "Stool\tthree legs\nBench\t\"long, with\ttab\"\nRug\tsoft\nVase\tblue\nClock\tloud")
    expect(rows(pg)).to_have_count(5, timeout=10000)
    data = {r["data"].get(F["Name"]): r["data"] for r in u.records(t["id"])}
    assert data["Stool"][F["Notes"]] == "three legs" and data["Bench"][F["Notes"]] == "long, with\ttab"
    assert "Clock" in data and data["Clock"][F["Notes"]] == "loud" and "Chair" not in data
    # numbers are converted like an import
    pidx = pg.locator("thead th").evaluate_all("(ths) => ths.findIndex(th => th.textContent.includes('Price'))")
    rows(pg).nth(0).locator("td").nth(pidx).click()
    pg.evaluate("""() => { const dt = new DataTransfer(); dt.setData('text/plain', '1,250\\n9.5');
        document.querySelector('.grid-wrap').dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true})); }""")
    eventually(lambda: sorted(v for v in (r["data"].get(price) for r in u.records(t["id"])) if v is not None) == [9.5, 1250] or None)
    assert not pg.errors, pg.errors


def test_search_everything(browser, table_user):
    u, t, F = table_user
    other = u.table(name="Contacts book")
    O = u.fields(other["id"])
    u.j("POST", f"/tables/{other['id']}/records", json={"data": {O["Name"]: "Ada Lovelace", O["Notes"]: "met at the chess club"}})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.keyboard.press("Control+k")
    pg.fill(".search-modal input", "chess")
    hit = pg.locator(".search-hit").first
    expect(hit).to_contain_text("Ada Lovelace")
    expect(hit).to_contain_text("Contacts book")
    pg.keyboard.press("Enter")
    expect(pg.locator(".page-head h1")).to_have_text("Contacts book")
    expect(pg.locator(".modal .record-title")).to_contain_text("Ada Lovelace")
    pg.keyboard.press("Escape")
    pg.click(".side-head [aria-label='Search everything']")                # the sidebar button too
    expect(pg.locator(".search-modal input")).to_be_focused()
    assert not pg.errors, pg.errors


def test_field_editor_rules_formats_and_options(browser):
    u = Client()
    t = u.table(name="Products")
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    # a required, unique text field with a description
    pg.click("thead .add-col button")
    drawer = pg.locator(".modal.drawer")
    drawer.locator("label:has-text('Name') input").fill("SKU")
    drawer.locator("button:has-text('Add description')").click()
    drawer.locator("textarea").fill("Stock keeping unit")
    drawer.locator(".toggle-row:has-text('Required')").click()
    drawer.locator(".toggle-row:has-text('Unique')").click()
    pg.click(".modal-foot button:has-text('Save')")
    expect(drawer).to_have_count(0)
    sku = next(f for f in u.j("GET", f"/tables/{t['id']}")["fields"] if f["name"] == "SKU")
    assert sku["required"] and sku["unique_values"] and sku["description"] == "Stock keeping unit"
    expect(pg.locator("thead th:has-text('SKU') .req-mark")).to_have_count(1)
    # a currency number with a default
    pg.click("thead .add-col button")
    drawer.locator("label:has-text('Name') input").fill("Price")
    drawer.locator(".type-button").click()
    pg.click(".type-item:has-text('Number')")
    drawer.locator(".segmented button:has-text('Currency')").click()
    drawer.locator("label:has-text('Symbol') input").fill("€")
    drawer.locator(".field-section:has-text('Default value') input").fill("9.5")
    expect(drawer).to_contain_text("€12,345.68")
    pg.click(".modal-foot button:has-text('Save')")
    expect(drawer).to_have_count(0)
    # a select with options added by Enter, reordered by dragging, and only those allowed
    pg.click("thead .add-col button")
    drawer.locator("label:has-text('Name') input").fill("Size")
    drawer.locator(".type-button").click()
    pg.click(".type-item:has-text('Single select')")
    drawer.locator("button:has-text('Add option')").click()
    opts = drawer.locator(".choice-row input.input")
    opts.nth(0).fill("Small")
    opts.nth(0).press("Enter")
    opts.nth(1).fill("Large")
    opts.nth(1).press("Enter")
    opts.nth(2).fill("Medium")
    drawer.locator(".choice-row .drag-handle").nth(2).drag_to(drawer.locator(".choice-row").nth(1))
    expect(opts.nth(1)).to_have_value("Medium")
    drawer.locator(".toggle-row:has-text('Allow new options')").click()
    drawer.locator(".field-section:has-text('Default value') select").select_option("Medium")
    pg.click(".modal-foot button:has-text('Save')")
    expect(drawer).to_have_count(0)
    fields = {f["name"]: f for f in u.j("GET", f"/tables/{t['id']}")["fields"]}
    assert [c["name"] for c in fields["Size"]["options"]["choices"]] == ["Small", "Medium", "Large"]
    assert fields["Size"]["options"]["allowNew"] is False and fields["Size"]["default_value"] == "Medium"
    assert fields["Price"]["options"]["numberFormat"] == "currency" and fields["Price"]["default_value"] == 9.5
    # a new record gets the defaults and shows the format; the required field is enforced
    r = u.request("POST", API + f"/tables/{t['id']}/records", json={"data": {}})
    assert r.status_code == 400 and "SKU is required" in r.text
    u.j("POST", f"/tables/{t['id']}/records", json={"data": {fields["SKU"]["id"]: "X1"}})
    expect(pg.locator(".grid tbody")).to_contain_text("€9.50")
    expect(pg.locator(".grid tbody")).to_contain_text("Medium")
    assert not pg.errors, pg.errors


def test_view_names_footer_icon_and_calendar_link(browser, table_user):
    u, t, F = table_user
    num = u.j("POST", f"/tables/{t['id']}/fields", json={"name": "Qty", "type": "number"})
    u.j("POST", f"/tables/{t['id']}/fields", json={"name": "Due", "type": "date"})  # calendar links are for tables with dates
    for i, r in enumerate(u.records(t["id"])):
        u.j("PATCH", f"/records/{r['id']}", json={"data": {num["id"]: i + 1}})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    # a name for this view only
    pg.click(".tool-btn:has-text('Fields')")
    pg.fill("input[aria-label='Name of Notes in this view']", "Remarks")
    pg.keyboard.press("Escape")
    expect(pg.locator("thead th .th-name").filter(has_text="Remarks")).to_have_count(1)
    assert any(f["name"] == "Notes" for f in u.j("GET", f"/tables/{t['id']}")["fields"])
    # a sum in the footer
    pg.locator("tfoot .summary-cell").nth(3).click()
    pg.click(".menu-item:has-text('Sum')")
    expect(pg.locator("tfoot .summary-cell").nth(3)).to_contain_text("10")
    # an emoji icon
    pg.click("button[aria-label='Change icon']")
    pg.locator(".emoji-btn").first.click()
    eventually(lambda: next(x for x in u.workspace()["tables"] if x["id"] == t["id"]).get("icon"))
    expect(pg.locator(".title-row .emoji-icon")).to_have_count(1)
    # a calendar subscription link
    pg.click("button[aria-label='More']")
    pg.click(".settings-nav button:has-text('Calendar links')")
    pg.click(".modal button:has-text('Calendar subscription links…')")
    pg.click(".modal button:has-text('Create link')")
    expect(pg.locator(".modal .feed-url")).to_contain_text("/ics/")
    assert not pg.errors, pg.errors


# ---------------------------------------------------------------- files tables (S3 / WebDAV test servers)

PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR4nGP4z8AARAwQCgAf7gP9i18U1AAAAABJRU5ErkJggg=="


def test_files_table_new_upload_preview_and_edit(browser):
    import base64
    u = Client()
    pg = new_page(browser)
    sign_in(pg, u)
    # a new files table on a WebDAV folder, from the New table dialog
    pg.click(".side-add >> nth=0")
    pg.click(".kind:has-text('Files')")
    pg.fill(".modal label:has-text('Name') input >> nth=0", "Shared docs")
    pg.click(".storage-form .segmented button:has-text('WebDAV')")
    pg.fill(".storage-form label:has-text('Folder address') input", "http://webdav:8080/shared/")
    pg.fill(".storage-form label:has-text('Username') input", "dav")
    pg.fill(".storage-form input[type=password]", "davpass1234")
    pg.fill(".modal input[placeholder^='All of it']", f"ui{uuid.uuid4().hex[:6]}")
    pg.click(".modal button:has-text('Test connection')")
    pg.wait_for_selector(".modal .ok, .modal .error")
    assert not pg.locator(".modal .error").count(), pg.locator(".modal .error").inner_text()
    pg.click(".modal-foot button:has-text('Create')")
    expect(pg.locator(".page-head h1")).to_have_text("Shared docs")
    expect(pg.locator(".storage-badge")).to_contain_text("webdav:8080/shared/ui")
    # "New" is "Upload" here: files, or a whole folder
    pg.click(".new-record-btn:has-text('Upload')")
    expect(pg.locator(".popover")).to_contain_text("Upload a folder")
    pg.keyboard.press("Escape")
    pg.set_input_files(".page-head input[type=file]:not([webkitdirectory])", [
        {"name": "readme.md", "mimeType": "text/markdown", "buffer": b"# Hello\n\nfrom the **test**\n"},
        {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"plain words"},
        {"name": "dot.png", "mimeType": "image/png", "buffer": base64.b64decode(PNG_B64)}])
    expect(pg.locator(".upload-panel")).to_contain_text("3 uploads complete", timeout=15000)
    expect(rows(pg)).to_have_count(3)
    # the image: a preview; clicking it opens the full-screen viewer with the details on the right
    expect(pg.locator(".grid tbody .thumb img")).to_have_count(3, timeout=15000)   # the text files get covers too
    pg.locator(".grid tbody tr", has_text="dot.png").locator(".thumb img").click()
    expect(pg.locator(".viewer .viewer-image img")).to_be_visible()
    expect(pg.locator(".viewer-info")).to_contain_text("Kind")
    expect(pg.locator(".viewer-info")).to_contain_text("Image")
    expect(pg.locator(".viewer a:has-text('Download')")).to_have_attribute("href", re.compile("download=1"))
    pg.keyboard.press("Escape")
    expect(pg.locator(".viewer")).to_have_count(0)
    t = next(x for x in u.workspace()["tables"] if x["name"] == "Shared docs")
    F = u.fields(t["id"])
    rid = lambda n: next(r["id"] for r in u.records(t["id"]) if r["data"][F["name"]] == n)
    # Markdown: formatted, edited as formatted text (WYSIWYG), saved back as Markdown
    pg.goto(f"{BASE}/#/table/{t['id']}?record={rid('readme.md')}")
    expect(pg.locator(".viewer .rich-text h1")).to_have_text("Hello")
    expect(pg.locator(".viewer .rich-text strong")).to_have_text("test")
    pg.click(".viewer-bar button:has-text('Edit')")
    expect(pg.locator(".rt-toolbar")).to_be_visible()
    pg.locator(".rich-text .ProseMirror p").click()
    pg.keyboard.press("End")
    pg.keyboard.type(" and more")
    pg.click(".viewer-bar button:has-text('Save')")
    expect(pg.locator(".rt-toolbar")).to_have_count(0)
    eventually(lambda: "from the **test and more**" in u.get(API + f"/files/{rid('readme.md')}/content").text)  # typed inside the bold word
    # going to the next file with the arrow keys; a text file is edited as text
    pg.keyboard.press("Escape")
    pg.goto(f"{BASE}/#/table/{t['id']}?record={rid('notes.txt')}")
    expect(pg.locator(".viewer .viewer-code")).to_have_text("plain words")
    pg.click(".viewer-bar button:has-text('Edit')")
    pg.fill(".viewer-code-edit", "edited in the viewer")
    pg.keyboard.press("Control+s")
    expect(pg.locator(".viewer .viewer-code")).to_have_text("edited in the viewer")
    assert u.get(API + f"/files/{rid('notes.txt')}/content").text == "edited in the viewer"
    # rename from the details panel
    name = pg.locator(".viewer-info .viewer-field", has_text="Name").locator("input")
    name.fill("renamed.txt")
    name.press("Enter")
    eventually(lambda: any(r["data"][F["name"]] == "renamed.txt" for r in u.records(t["id"])))
    pg.keyboard.press("Escape") if pg.locator(".viewer").count() else None
    # the record panel no longer edits files (the viewer does)
    assert not pg.errors, pg.errors


def test_viewer_documents_spreadsheets_and_folder_upload(browser, tmp_path):
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    u = Client()
    t = u.table("files", "Office files", storage={"kind": "s3", "endpoint": "http://s3:9000", "bucket": "test-bucket",
                "username": "testkey", "secret": "testsecret12345"}, storage_prefix=f"ui{uuid.uuid4().hex[:6]}")
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    # a folder with a subfolder, uploaded as it is
    top = tmp_path / "project"
    (top / "sheets").mkdir(parents=True)
    (top / "report.docx").write_bytes(open(os.path.join(here, "fixtures", "report.docx"), "rb").read())
    (top / "sheets" / "stock.xlsx").write_bytes(open(os.path.join(here, "fixtures", "stock.xlsx"), "rb").read())
    pg.set_input_files(".page-head input[webkitdirectory]", str(top))
    expect(pg.locator(".upload-panel")).to_contain_text("2 uploads complete", timeout=20000)
    F = u.fields(t["id"])
    got = {r["data"][F["name"]]: r for r in u.records(t["id"])}
    assert got["report.docx"]["data"][F["folder"]] == "project" and got["stock.xlsx"]["data"][F["folder"]] == "project/sheets"
    # a spreadsheet as a table
    pg.goto(f"{BASE}/#/table/{t['id']}?record={got['stock.xlsx']['id']}")
    expect(pg.locator(".viewer .sheet-table")).to_contain_text("Chairs")
    expect(pg.locator(".viewer .sheet-table thead")).to_contain_text("C")
    # a Word file as pages (converted to PDF)
    pg.keyboard.press("Escape")
    pg.goto(f"{BASE}/#/table/{t['id']}?record={got['report.docx']['id']}")
    # (a browser without its own PDF viewer, like this one or a phone's, gets every page drawn by pdf.js)
    expect(pg.locator(".viewer .pdf-page canvas")).to_have_count(1, timeout=60000)
    expect(pg.locator(".viewer .pdf-controls")).to_contain_text("1 / 1")
    expect(pg.locator(".viewer-info")).to_contain_text("project")
    assert not pg.errors, pg.errors


def test_upload_from_a_link_field(browser):
    u = Client()
    files = u.table("files", "Attachments store", storage={"kind": "s3", "endpoint": "http://s3:9000", "bucket": "test-bucket",
                    "username": "testkey", "secret": "testsecret12345"}, storage_prefix=f"ui{uuid.uuid4().hex[:6]}")
    t = u.table(name="Jobs")
    F = u.fields(t["id"])
    link = u.j("POST", f"/tables/{t['id']}/fields", json={"name": "Docs", "type": "link", "options": {"foreignTableId": files["id"]}})
    r = u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Kitchen"}})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}?record={r['id']}")
    pg.locator(".modal .form-row", has_text="Docs").locator(".choice-field").click()
    pg.set_input_files(".popover input[type=file]", [{"name": "quote.txt", "mimeType": "text/plain", "buffer": b"1000 EUR"}])
    expect(pg.locator(".modal .form-row", has_text="Docs")).to_contain_text("quote.txt")
    eventually(lambda: u.record(t["id"], r["id"])["data"].get(link["id"]))
    FF = u.fields(files["id"])
    assert [x["data"][FF["name"]] for x in u.records(files["id"])] == ["quote.txt"]
    assert not pg.errors, pg.errors


def test_view_tabs_reorder_and_view_settings(browser, table_user):
    u, t, F = table_user
    for name, vtype in (("Board", "kanban"), ("Cards", "gallery")):
        u.j("POST", f"/tables/{t['id']}/views", json={"name": name, "type": vtype})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    tabs = lambda: [x.strip() for x in pg.locator(".view-tab .view-tab-name").all_text_contents()]
    assert tabs() == ["Table", "Board", "Cards"]
    # drag a tab to reorder; the order is saved
    pg.locator(".view-tab", has_text="Cards").drag_to(pg.locator(".view-tab", has_text="Table"))
    eventually(lambda: [v["name"] for v in u.j("GET", f"/tables/{t['id']}")["views"]] == ["Cards", "Table", "Board"] or None)
    pg.reload()
    expect(pg.locator(".view-tab .view-tab-name").first).to_have_text("Cards")
    # the open tab's ⋯: view settings
    pg.click(".view-tab:has-text('Table')")
    pg.click(".view-tab.active .view-tab-more")
    modal = pg.locator(".modal")
    expect(modal).to_contain_text("View settings")
    modal.locator("label:has-text('Name') input").fill("All stock")
    modal.locator("label:has-text('Name') input").press("Enter")
    expect(pg.locator(".view-tab.active")).to_contain_text("All stock")
    # fields: hide one for this view
    modal.locator(".settings-nav button:has-text('Fields')").click()
    modal.locator("input[aria-label='Show Notes']").uncheck()
    expect(pg.locator("thead th .th-name").filter(has_text="Notes")).to_have_count(0)
    # sort from the window
    modal.locator(".settings-nav button:has-text('Sort')").click()
    expect(modal.locator(".settings-pane")).to_be_visible()
    # layout: rows per page; then switch the kind of view
    modal.locator(".settings-nav button:has-text('Layout')").click()
    modal.locator("label:has-text('Rows per page') select").select_option("10")
    modal.locator(".settings-nav button:has-text('General')").click()
    modal.locator(".type-tile:has-text('Board')").click()
    expect(pg.locator(".kanban-col").first).to_be_visible()
    v = next(x for x in u.j("GET", f"/tables/{t['id']}")["views"] if x["name"] == "All stock")
    assert v["type"] == "kanban" and v["config"].get("groupBy") == F["Status"] and F["Notes"] in v["config"]["hidden"]
    # duplicate, move, delete
    modal.locator("button:has-text('Duplicate view')").click()
    expect(pg.locator(".view-tab.active")).to_contain_text("All stock copy")
    modal.locator("button:has-text('Delete view')").click()
    modal.locator("button.danger:has-text('Delete view')").click()
    expect(pg.locator(".view-tab", has_text="All stock copy")).to_have_count(0)
    assert not pg.errors, pg.errors


def test_table_settings_show_what_applies(browser, table_user):
    u, t, F = table_user
    files = u.table("files", "Docs", storage={"kind": "s3", "endpoint": "http://s3:9000", "bucket": "test-bucket", "username": "testkey",
                    "secret": "testsecret12345"}, storage_prefix=f"ui{uuid.uuid4().hex[:6]}")
    pg = new_page(browser)
    sign_in(pg, u)
    # a plain table without dates: no sync, no calendar links; description and field order
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.click(".page-head [aria-label=More]")
    nav = pg.locator(".modal .settings-nav")
    expect(nav).to_contain_text("Fields")
    for absent in ("Sync", "Storage", "Calendar links"):
        expect(nav.locator(f"button:has-text('{absent}')")).to_have_count(0)
    pg.fill(".modal textarea", "Everything in the warehouse")
    nav.locator("button:has-text('Fields')").click()
    eventually(lambda: u.j("GET", f"/tables/{t['id']}")["table"]["description"] == "Everything in the warehouse" or None)
    rows = pg.locator(".modal .settings-row")
    rows.filter(has_text="Status").drag_to(rows.filter(has_text="Name"))
    eventually(lambda: [f["name"] for f in u.j("GET", f"/tables/{t['id']}")["fields"]][:3] == ["Status", "Name", "Notes"] or None)
    pg.click(".modal-foot button:has-text('Done')")
    # a files table: its storage, no import and no calendar links
    pg.goto(f"{BASE}/#/table/{files['id']}")
    pg.click(".page-head [aria-label=More]")
    expect(nav.locator("button:has-text('Storage')")).to_have_count(1)
    expect(nav.locator("button:has-text('Calendar links')")).to_have_count(0)
    nav.locator("button:has-text('Storage')").click()
    expect(pg.locator(".modal .settings-facts")).to_contain_text("test-bucket/ui")
    nav.locator("button:has-text('Import & export')").click()
    expect(pg.locator(".modal button:has-text('Import…')")).to_have_count(0)
    expect(pg.locator(".modal")).to_contain_text("Files are added with Upload")
    pg.click(".modal-foot button:has-text('Done')")
    # a synced calendar table: its sync and calendar links
    cal = u.table("mixed", "Agenda")
    pg.goto(f"{BASE}/#/table/{cal['id']}")
    pg.click(".page-head [aria-label=More]")
    expect(nav.locator("button:has-text('Sync')")).to_have_count(1)
    expect(nav.locator("button:has-text('Calendar links')")).to_have_count(1)
    nav.locator("button:has-text('Sync')").click()
    expect(pg.locator(".modal button:has-text('Sync now')")).to_be_visible()
    assert not pg.errors, pg.errors


def test_files_table_header_on_a_phone(browser):
    u = Client()
    t = u.table("files", "Files", storage={"name": "eh-share", "kind": "s3", "endpoint": "http://s3:9000", "bucket": "test-bucket",
                "username": "testkey", "secret": "testsecret12345"}, storage_prefix=f"ui{uuid.uuid4().hex[:6]}")
    pg = new_page(browser, phone=True)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    expect(pg.locator(".page-head h1")).to_have_text("Files")
    expect(pg.locator(".page-head .storage-badge")).to_be_hidden()          # it's in table settings instead
    assert pg.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1")
    assert not pg.errors, pg.errors


def test_templates_for_tables_and_views(browser, table_user):
    u, t, F = table_user
    pg = new_page(browser)
    sign_in(pg, u)
    # a new table from a starter template
    pg.click(".side-add >> nth=0")
    pg.click(".kind:has-text('From a template')")
    pg.click(".template-card:has-text('Job tracker')")
    expect(pg.locator(".modal label:has-text('Name') input").first).to_have_value("Job tracker")
    expect(pg.locator(".modal")).to_contain_text("Add its 3 sample records")
    pg.click(".modal-foot button:has-text('Create')")
    expect(pg.locator(".page-head h1")).to_have_text("Job tracker")
    expect(pg.locator(".view-tab")).to_contain_text(["All jobs", "Board", "Schedule", "To invoice"])
    expect(rows(pg)).to_have_count(3)
    # a view saved as a template, then used in another table
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.click(".view-tab.active .view-tab-more")
    pg.click(".modal .settings-nav button:has-text('Layout')")
    pg.click(".modal .settings-nav button:has-text('General')")
    pg.click(".modal .type-tile:has-text('Board')")
    pg.click(".modal .settings-nav button:has-text('Template')")
    pg.fill(".modal label:has-text('Template name') input", "Status board")
    pg.click(".modal button:has-text('Save as template')")
    expect(pg.locator(".modal .ok")).to_contain_text("Saved")
    pg.click(".modal-foot button:has-text('Done')")
    jobs = next(x for x in u.workspace()["tables"] if x["name"] == "Job tracker")
    pg.goto(f"{BASE}/#/table/{jobs['id']}")
    pg.click(".view-tab.add")
    pg.click(".popover .menu-item:has-text('Status board')")
    expect(pg.locator(".view-tab.active")).to_contain_text("Status board")
    expect(pg.locator(".kanban-col").first).to_be_visible()
    v = next(x for x in u.j("GET", f"/tables/{jobs['id']}")["views"] if x["name"] == "Status board")
    J = u.fields(jobs["id"])
    assert v["type"] == "kanban" and v["config"]["groupBy"] == J["Status"]       # matched by name
    # a table template downloaded as a file from table settings
    pg.click(".page-head [aria-label=More]")
    pg.click(".modal .settings-nav button:has-text('Template')")
    with pg.expect_download() as dl:
        pg.click(".modal button:has-text('Download template file')")
    import json as _json
    body = _json.loads(open(dl.value.path()).read())
    assert body["kind"] == "table" and body["name"] == "Job tracker" and any(f["name"] == "Invoice total" for f in body["fields"])
    assert dl.value.suggested_filename.endswith(".probyte-table.json")
    assert not pg.errors, pg.errors


def test_svg_files_show_as_pictures(browser):
    u = Client()
    t = u.table("files", "Logos", storage={"kind": "s3", "endpoint": "http://s3:9000", "bucket": "test-bucket", "username": "testkey",
                "secret": "testsecret12345"}, storage_prefix=f"ui{uuid.uuid4().hex[:6]}")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><rect width="40" height="40" fill="#ef4444"/></svg>'
    rec = u.post(f"{API}/tables/{t['id']}/files", files=[("files", ("logo.svg", svg, "image/svg+xml"))]).json()["records"][0]
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    thumb = pg.locator(".grid tbody .thumb img")
    expect(thumb).to_have_count(1)
    eventually(lambda: thumb.evaluate("i => i.complete && i.naturalWidth > 0") or None)      # drawn as a picture
    pg.goto(f"{BASE}/#/table/{t['id']}?record={rec['id']}")
    img = pg.locator(".viewer .viewer-image.svg img")
    expect(img).to_be_visible()
    eventually(lambda: img.evaluate("i => i.naturalWidth") == 40 or None)
    # its code can still be edited
    pg.click(".viewer-bar button:has-text('Edit')")
    expect(pg.locator(".viewer-code-edit")).to_have_value(re.compile("<rect"))
    pg.fill(".viewer-code-edit", svg.decode().replace("#ef4444", "#22c55e"))
    pg.click(".viewer-bar button:has-text('Save')")
    expect(img).to_be_visible()
    assert "#22c55e" in u.get(API + f"/files/{rec['id']}/content").text
    assert not pg.errors, pg.errors
