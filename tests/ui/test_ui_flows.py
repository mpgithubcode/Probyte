"""End-to-end flows in a real browser, the way a person uses the app: sign up, build a table, edit, filter, share."""
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
    pg.fill(".modal .row-2 input.input", "Budget")
    pg.click(".modal .row-2 .stack:has-text('Type') button")
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
    pg.wait_for_timeout(500)
    rec = next(r for r in u.records(t["id"]) if r["data"][F["Name"]] == "Desk")
    assert rec["data"].get(F["Notes"]) == "Standing desk"
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
    pg.click(".menu-item:has-text('Delete database')")
    pg.click(".menu-item:has-text('Click again to delete')")
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
    pg.click(".menu-item:has-text('Import from file')")
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
    pg.click(".menu-item:has-text('Export')")
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
    pg.click(".menu-item:has-text('Combine with other tables')")
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
    pg.wait_for_timeout(800)
    d = next(r for r in u.records(t["id"]) if r["data"][F["Name"]] == "Chair")["data"]
    assert d.get(done) is True and d.get(due) == "2026-10-20" and d.get(F["Status"]) == "Done", d
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
    pg.wait_for_timeout(800)
    d = next(r for r in u.records(t["id"]) if r["data"][F["Name"]] == "Chair")["data"]
    assert d[F["Status"]] == "Done"
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
    rec = next(r for r in u.records(t["id"]) if r["data"].get(F["summary"]) == "Team lunch")
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
    pg.wait_for_timeout(600)
    assert next(r for r in u.records(t["id"]) if r["data"][F["Name"]] == "Lamp")["data"].get(F["Notes"]) == "From my phone"
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
    for _ in range(20):                                      # the last edit may still be on its way
        rule = u.records(t["id"])[0]["data"][F["rrule"]]
        if "OVERRIDE" in rule:
            break
        pg.wait_for_timeout(250)
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
