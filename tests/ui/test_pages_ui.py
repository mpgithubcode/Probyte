"""Records as pages, linked records shown as tables / cards, and adding a base with a link, in a real browser."""
from playwright.sync_api import expect

from conftest import BASE, Client
from test_ui_flows import browser, eventually, new_page, sign_in  # noqa: F401  (browser: the fixture)


def setup(u):
    tasks = u.table(name="Tasks")
    T = u.fields(tasks["id"])
    hours = u.j("POST", f"/tables/{tasks['id']}/fields", json={"name": "Hours", "type": "number", "options": {}})
    projects = u.table(name="Projects")
    link = u.j("POST", f"/tables/{projects['id']}/fields", json={"name": "Jobs", "type": "link", "options": {
        "foreignTableId": tasks["id"], "symmetric": False, "views": {"cell": "count", "record": "table", "fields": [T["Name"], hours["id"]]}}})
    brief = u.j("POST", f"/tables/{projects['id']}/fields", json={"name": "Brief", "type": "long_text", "options": {}})
    a = u.j("POST", f"/tables/{tasks['id']}/records", json={"data": {T["Name"]: "Wire panel", hours["id"]: 3}})
    b = u.j("POST", f"/tables/{tasks['id']}/records", json={"data": {T["Name"]: "Pull cable", hours["id"]: 5}})
    P = u.fields(projects["id"])
    p = u.j("POST", f"/tables/{projects['id']}/records", json={"data": {P["Name"]: "Mill St", link["id"]: [b["id"], a["id"]]}})
    return tasks, projects, link, brief, a, b, p


def test_linked_records_as_a_table_and_records_as_pages(browser):
    u = Client()
    tasks, projects, link, brief, a, b, p = setup(u)
    notes = u.fields(projects["id"])["Notes"]   # (a new table's first long text: the page's body until another is chosen)
    pg = new_page(browser)
    sign_in(pg, u)
    # in the table: how many; on the record: a table of the linked records with their fields
    pg.goto(f"{BASE}/#/table/{projects['id']}")
    expect(pg.locator(".grid tbody .count-value").first).to_have_text("2 records")
    pg.goto(f"{BASE}/#/table/{projects['id']}?record={p['id']}")
    table = pg.locator(".record-modal .linked-table")
    expect(table.locator("tbody tr")).to_have_count(2)
    expect(table.locator("thead")).to_contain_text("Hours")
    expect(table.locator("tbody tr").first).to_contain_text("Pull cable")
    expect(table.locator("tbody tr").first).to_contain_text("5")
    # a linked record opens over it, editable
    table.locator("tbody tr").first.click()
    expect(pg.locator(".modal .record-title").last).to_have_text("Pull cable")
    pg.keyboard.press("Escape")
    # unlink one
    pg.click(".record-modal [aria-label='Unlink Wire panel']")
    eventually(lambda: u.record(projects["id"], p["id"])["data"][link["id"]] == [b["id"]] or None)
    expect(table.locator("tbody tr")).to_have_count(1)

    # the record as a page: its title, the long text as the page's body
    pg.click(".record-modal button:has-text('Page')")
    expect(pg.locator(".page-view .page-title input")).to_have_value("Mill St")
    pg.click(".page-bar button:has-text('Edit page')")
    pg.click(".page-design-note input[type=checkbox]")            # open this table's records as pages
    eventually(lambda: (u.j("GET", f"/tables/{projects['id']}")["table"]["page"] or {}).get("enabled") or None)
    pg.click(".page-add button:has-text('Heading')")
    pg.locator(".page-heading-input").fill("Work")
    pg.click(".page-add button:has-text('Field')")
    pg.click(".popover .menu-item:has-text('Jobs')")
    pg.locator(".page-block", has_text="Jobs").locator("select[aria-label='Show as']").select_option("cards")
    pg.click(".page-bar button:has-text('Done')")
    expect(pg.locator(".page-view h2.page-heading")).to_have_text("Work")
    expect(pg.locator(".page-view .linked-card")).to_have_count(1)
    blocks = eventually(lambda: u.j("GET", f"/tables/{projects['id']}")["table"]["page"]["blocks"])
    assert [x["type"] for x in blocks] == ["properties", "doc", "heading", "field"]
    assert blocks[3] == {**blocks[3], "field": link["id"], "view": "cards"}
    # write in the page's body, and place a field in the text
    pg.locator(".page-doc-body .ProseMirror").click()
    pg.keyboard.type("Scope of work")
    pg.click(".page-doc .rt-btn:has-text('Field')")
    pg.click(".popover .menu-item:has-text('Jobs')")
    expect(pg.locator(".page-embed")).to_contain_text("Jobs")
    eventually(lambda: "Scope of work" in (u.record(projects["id"], p["id"])["data"].get(notes) or "")
               and "{{field:" + link["id"] + "}}" in u.record(projects["id"], p["id"])["data"][notes] or None)
    expect(pg.locator(".page-props")).to_contain_text("Brief")   # (not the body: listed with the other fields)
    # the record panel is still there
    pg.click(".page-bar button:has-text('Record')")
    expect(pg.locator(".record-modal")).to_be_visible()
    assert not pg.errors, pg.errors

    # on a phone: the page opens by itself (the table says so), one column, nothing wider than the screen
    ph = new_page(browser, phone=True)
    sign_in(ph, u)
    ph.goto(f"{BASE}/#/table/{projects['id']}?record={p['id']}")
    expect(ph.locator(".page-view .page-title")).to_be_visible()
    expect(ph.locator(".page-embed")).to_contain_text("Jobs")
    assert ph.evaluate("() => { const s = document.querySelector('.page-scroll'); return s.scrollWidth <= s.clientWidth + 1 }")
    assert not ph.errors, ph.errors


def test_adding_a_base_with_a_link(browser):
    owner, other = Client(), Client()
    owner.table(name="Shared jobs")
    s = owner.j("POST", "/shares", json={"name": "Jobs", "base_id": owner.base_id, "whole_base": True})
    pg = new_page(browser)
    sign_in(pg, other)
    pg.click(".side-head [aria-label='Add a base']")
    pg.click(".popover .menu-item:has-text('Add base with a link')")
    pg.fill(".modal input[placeholder='https://…']", f"{BASE}/s/{s['token']}")
    pg.click(".modal-foot button:has-text('Add base')")
    expect(pg.locator(".page-head h1")).to_have_text("Shared jobs")
    assert any(b.get("linked") for b in other.workspace()["bases"])
    # an invite to a base on this server: shown, then joined
    inv = owner.j("POST", f"/bases/{owner.base_id}/invites", json={"role": "viewer"})
    pg.click(".side-head [aria-label='Add a base']")
    pg.click(".popover .menu-item:has-text('Add base with a link')")
    pg.fill(".modal input[placeholder='https://…']", f"{BASE}/#/join/{inv['token']}")
    pg.click(".modal-foot button:has-text('Continue')")
    pg.click(".modal-foot button:has-text('Join')")
    eventually(lambda: any(b.get("role") == "viewer" for b in other.workspace()["bases"]) or None)
    assert not pg.errors, pg.errors
