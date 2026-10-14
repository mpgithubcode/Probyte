"""The working parts of a view: the toolbar's labeled choices, density, view tabs, empty views, the record panel beside
the view (going through records, sections, duplicating), selecting cards and list rows, and repeating records."""
import re

from playwright.sync_api import expect

from conftest import API, BASE, Client
from test_ui_flows import browser, new_page, rows, sign_in  # noqa: F401  (the browser fixture)

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82")


def seeded():
    u = Client()
    t = u.table(name="Jobs")
    F = u.fields(t["id"])
    for n, s in (("Alpha", "Todo"), ("Bravo", "Doing"), ("Charlie", "Done")):
        u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: n, F["Status"]: s}})
    return u, t, F


def test_toolbar_density_tabs_and_empty_views(browser):
    u, t, F = seeded()
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    expect(rows(pg)).to_have_count(3)
    tab = pg.locator(".view-tab.active")
    expect(tab.locator(".view-tab-count")).to_have_text("3")
    # a labeled choice instead of a bare dropdown: Density
    layout = pg.locator(".view-options button[aria-label^='Layout']")
    expect(layout).to_have_text("Layout")
    layout.click()
    pg.click(".popover [role=menuitemradio]:has-text('Compact')")
    expect(pg.locator("table.grid")).to_have_class(re.compile("density-compact"))
    expect(layout).to_contain_text("Compact")
    pg.wait_for_timeout(800)   # (view settings are saved a moment after they change)
    # a filter that matches nothing: the view says so, and clears it (with Undo)
    view = u.j("GET", f"/tables/{t['id']}")["views"][0]
    u.j("PATCH", f"/views/{view['id']}", json={"config": {**view["config"], "density": "compact",
        "filter": {"conjunction": "and", "items": [{"field": F["Name"], "op": "is", "value": "Zulu"}]}}})
    pg.reload()
    expect(pg.locator(".view-tab.active .view-tab-filtered")).to_have_count(1)
    notice = pg.locator(".empty-notice")
    expect(notice).to_contain_text("No records match this view's filter")
    expect(notice).to_contain_text("3 records are hidden by it")
    notice.locator("button:has-text('Clear filter')").click()
    expect(rows(pg)).to_have_count(3)
    expect(pg.locator(".empty-notice")).to_have_count(0)
    pg.click(".toast-action:has-text('Undo')")
    expect(pg.locator(".empty-notice")).to_be_visible()
    # a search that matches nothing
    notice.locator("button:has-text('Clear filter')").click()
    pg.fill(".view-options .search input", "nothing like it")
    expect(pg.locator(".empty-notice")).to_contain_text("No records match “nothing like it”")
    pg.click(".empty-notice button:has-text('Clear search')")
    expect(rows(pg)).to_have_count(3)
    assert not pg.errors, pg.errors


def test_the_record_panel_beside_the_view(browser):
    u, t, F = seeded()
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    pg.goto(f"{BASE}/#/table/{t['id']}?record=" + u.records(t["id"])[0]["id"])
    panel = pg.locator(".docked-panel")
    expect(panel).to_be_visible()
    expect(panel.locator(".modal-title")).to_contain_text("Alpha")
    expect(pg.locator(".backdrop")).to_have_count(0)                   # the view stays usable
    expect(panel.locator(".record-nav")).to_contain_text("1 of 3")
    pg.keyboard.press("j")
    expect(panel.locator(".modal-title")).to_contain_text("Bravo")
    panel.locator("button[aria-label='Next record']").click()
    expect(panel.locator(".modal-title")).to_contain_text("Charlie")
    pg.keyboard.press("k")
    expect(panel.locator(".modal-title")).to_contain_text("Bravo")
    # sections: Status under "Workflow"
    panel.locator("button[aria-label='Record actions']").click()
    pg.click(".popover .menu-item:has-text('Arrange into sections')")
    panel.locator("button:has-text('Add a section')").click()
    panel.locator("input[aria-label='Section 1 name']").fill("Workflow")
    panel.locator("select[aria-label='Section of Status']").select_option(label="Workflow")
    panel.locator("button:has-text('Done')").click()
    expect(panel.locator(".record-section-head")).to_contain_text("Workflow")
    expect(panel.locator(".record-section .form-label")).to_contain_text(["Status"])
    pg.wait_for_timeout(800)
    view = u.j("GET", f"/tables/{t['id']}")["views"][0]
    assert view["config"]["recordSections"][0]["name"] == "Workflow"
    # the record's ID and when it was added, at the bottom
    expect(panel.locator(".record-meta")).to_contain_text("added")
    # duplicate (the copy opens), then undo it
    panel.locator("button[aria-label='Record actions']").click()
    pg.click(".popover .menu-item:has-text('Duplicate')")
    expect(rows(pg)).to_have_count(4)
    pg.click(".toast-action:has-text('Undo')")
    expect(rows(pg)).to_have_count(3)
    expect(panel).to_have_count(0)    # (the copy it showed is gone)
    assert not pg.errors, pg.errors


def test_selecting_list_rows_and_cards(browser):
    u, t, F = seeded()
    lst = u.j("POST", f"/tables/{t['id']}/views", json={"name": "List", "type": "list"})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}/{lst['id']}")
    expect(pg.locator(".list-row")).to_have_count(3)
    pg.locator(".list-row", has_text="Alpha").hover()
    pg.locator(".list-row", has_text="Alpha").locator(".select-box").check()
    bar = pg.locator(".bulk-bar")
    expect(bar).to_contain_text("1 selected")
    bar.locator("button:has-text('Select all 3')").click()
    expect(bar).to_contain_text("3 selected")
    with pg.expect_download() as dl:
        bar.locator("button:has-text('Export CSV')").click()
    assert "Alpha" in open(dl.value.path(), encoding="utf-8-sig").read()
    bar.locator("button:has-text('Duplicate')").click()
    expect(pg.locator(".list-row")).to_have_count(6)
    pg.click(".toast-action:has-text('Undo')")
    expect(pg.locator(".list-row")).to_have_count(3)
    # a board's cards too
    board = u.j("POST", f"/tables/{t['id']}/views", json={"name": "Board", "type": "kanban"})
    pg.goto(f"{BASE}/#/table/{t['id']}/{board['id']}")
    pg.reload()   # (made through the API after the page loaded)
    card = pg.locator(".kanban .card", has_text="Bravo")
    card.hover()
    card.locator(".select-box").check()
    expect(pg.locator(".bulk-bar")).to_contain_text("1 selected")
    pg.locator(".bulk-bar button:has-text('Delete')").click()
    pg.locator(".bulk-bar button:has-text('Delete 1?')").click()
    expect(pg.locator(".kanban .card")).to_have_count(2)
    pg.click(".toast-action:has-text('Undo')")
    expect(pg.locator(".kanban .card")).to_have_count(3)
    assert not pg.errors, pg.errors


def test_files_shown_by_a_link_open_and_stay_open(browser):
    """A link showing its records' files: the viewer opened from the cell goes from file to file without closing."""
    u = Client()
    clients, jobs = u.table(name="Clients"), u.table(name="Jobs")
    C = u.fields(clients["id"])
    logo = u.j("POST", f"/tables/{clients['id']}/fields", json={"name": "Logo", "type": "attachment"})
    ups = [u.post(API + "/attachments", files={"file": (f"logo{i}.png", PNG, "image/png")}).json() for i in (1, 2)]
    acme = u.j("POST", f"/tables/{clients['id']}/records", json={"data": {C["Name"]: "acme", logo["id"]: ups}})
    link = u.j("POST", f"/tables/{jobs['id']}/fields", json={"name": "Client", "type": "link",
                                                             "options": {"foreignTableId": clients["id"], "displayFieldId": logo["id"]}})
    J = u.fields(jobs["id"])
    u.j("POST", f"/tables/{jobs['id']}/records", json={"data": {J["Name"]: "Panel", link["id"]: [acme["id"]]}})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{jobs['id']}")
    rows(pg).first.locator(".thumb").first.click()
    viewer = pg.locator(".viewer")
    expect(viewer).to_have_attribute("aria-label", "logo1.png")
    viewer.locator("button[aria-label='Next file']").click()
    expect(viewer).to_have_attribute("aria-label", "logo2.png")          # still open, on the next file
    viewer.locator("button[aria-label='Previous file']").click()
    expect(viewer).to_have_attribute("aria-label", "logo1.png")
    assert not pg.errors, pg.errors


def test_a_repeating_task_in_the_list_and_its_next_dates(browser):
    u = Client()
    t = u.table("tasks", "Chores")
    F = u.fields(t["id"])
    r = u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Trash", F["due"]: "2026-10-13", F["rrule"]: "FREQ=WEEKLY"}})
    lst = u.j("POST", f"/tables/{t['id']}/views", json={"name": "List", "type": "list"})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}/{lst['id']}")
    row = pg.locator(".list-row", has_text="Trash")
    expect(row.locator(".list-repeat")).to_have_attribute("title", "Weekly")
    row.click()
    up = pg.locator(".docked-panel .upcoming")
    expect(up.locator(".upcoming-row")).to_have_count(5)
    expect(up.locator(".upcoming-row").first).to_contain_text("current")
    up.locator(".upcoming-row").nth(2).locator("button:has-text('Done')").click()   # Oct 27, ahead of time
    expect(pg.locator(".list-row", has_text="Trash")).to_have_count(2)
    done = [x["data"] for x in u.records(t["id"]) if x["data"][F["done"]]]
    assert [d[F["due"]] for d in done] == ["2026-10-27"]
    assert "EXDATE:2026-10-27" in u.record(t["id"], r["id"])["data"][F["rrule"]]
    assert not pg.errors, pg.errors


def test_date_filters_groups_and_automatic_dates(browser):
    """A date filter with Teable's choices (today, days ago, a range…), grouping dates relative to today, and a
    Completed date added from the field types."""
    from datetime import date, timedelta
    u = Client()
    t = u.table(name="Visits")
    F = u.fields(t["id"])
    due = u.j("POST", f"/tables/{t['id']}/fields", json={"name": "Due", "type": "date"})
    done = u.j("POST", f"/tables/{t['id']}/fields", json={"name": "Done", "type": "checkbox"})
    today = date.today()
    for name, days in (("Old", -20), ("Now", 0), ("Soon", 1), ("Far", 60)):
        u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: name, due["id"]: (today + timedelta(days=days)).isoformat()}})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    expect(rows(pg)).to_have_count(4)
    pg.click(".tool-btn:has-text('Filter')")
    pg.click(".popover button:has-text('Add condition')")
    pop = pg.locator(".popover").last
    pop.locator("select").nth(0).select_option(label="Due")
    pop.locator(".date-filter select").select_option("today")
    expect(rows(pg)).to_have_count(1)
    expect(rows(pg).first).to_contain_text("Now")
    pop.locator(".date-filter select").select_option("days ago")
    pop.locator(".date-filter input[aria-label='Days']").fill("20")
    expect(rows(pg)).to_have_count(1)
    expect(rows(pg).first).to_contain_text("Old")
    pop.locator(".date-filter select").select_option("custom range")
    pop.locator(".date-filter input[aria-label='From']").fill(today.isoformat())
    pop.locator(".date-filter input[aria-label='To']").fill((today + timedelta(days=1)).isoformat())
    expect(rows(pg)).to_have_count(2)
    pg.keyboard.press("Escape")
    # grouped by Due, relative to today
    view = u.j("GET", f"/tables/{t['id']}")["views"][0]
    pg.wait_for_timeout(800)
    u.j("PATCH", f"/views/{view['id']}", json={"config": {"groups": [{"field": due["id"], "dir": "asc", "dateBy": "relative"}]}})
    pg.reload()
    heads = pg.locator(".grid .group-row")
    expect(heads).to_have_count(4)
    expect(heads.nth(0)).to_contain_text("Earlier")
    expect(heads.nth(1)).to_contain_text("Today")
    expect(heads.nth(2)).to_contain_text("Tomorrow")
    expect(heads.nth(3)).to_contain_text("Later")
    # a Completed date, from the field types: its formula guessed from the Done checkbox
    pg.click(".page-head button[title='Add field']")
    pg.click(".modal .type-button")
    pg.click(".popover .type-item:has-text('Completed date')")
    modal = pg.locator(".modal")
    expect(modal.locator(".type-button")).to_contain_text("Completed date")
    expect(modal.locator("textarea").first).to_have_value("{Done}")
    modal.locator("button:has-text('Save'), button:has-text('Create')").last.click()
    expect(pg.locator(".modal")).to_have_count(0)
    fid = next(f["id"] for f in u.j("GET", f"/tables/{t['id']}")["fields"] if f["name"] == "Completed date")
    rec = next(r for r in u.records(t["id"]) if r["data"][F["Name"]] == "Now")
    u.j("PATCH", f"/records/{rec['id']}", json={"data": {done["id"]: True}})
    assert u.record(t["id"], rec["id"])["data"][fid]
    assert not pg.errors, pg.errors


def test_a_list_row_with_more_than_fits_and_long_text(browser):
    """List rows keep every property (running off the edge, clipped) and can show long text on one line."""
    u = Client()
    people, agencies = u.table(name="People"), u.table(name="Agencies")
    A = u.fields(agencies["id"])
    ids = [u.j("POST", f"/tables/{agencies['id']}/records", json={"data": {A["Name"]: f"{n} Housing Authority"}})["id"]
           for n in ("East Bridgewater", "Randolph", "Walpole", "Mashpee", "Mansfield", "Worcester", "Upton")]
    P = u.fields(people["id"])
    link = u.j("POST", f"/tables/{people['id']}/fields", json={"name": "Agencies", "type": "link", "options": {"foreignTableId": agencies["id"]}})
    u.j("POST", f"/tables/{people['id']}/records", json={"data": {P["Name"]: "Danielle Reid", P["Notes"]: "Line one\nline two of the notes",
                                                                 P["Status"]: "Todo", link["id"]: ids}})
    lst = u.j("POST", f"/tables/{people['id']}/views", json={"name": "List", "type": "list"})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{people['id']}/{lst['id']}")
    row = pg.locator(".list-row", has_text="Danielle Reid")
    expect(row.locator(".list-prop", has_text="East Bridgewater")).to_have_count(1)
    expect(row.locator(".list-prop", has_text="Todo")).to_have_count(1)       # still there, past the long one
    expect(row.locator(".list-prop.long")).to_have_count(0)                   # long text: only when added
    pg.click(".tool-btn:has-text('Properties')")
    pg.click(".popover .list-opt-extra button:has-text('Notes')")
    expect(row.locator(".list-prop.long")).to_contain_text("Line one")
    assert not pg.errors, pg.errors


def test_board_columns_move_and_cards_show_every_field(browser):
    u = Client()
    t = u.table(name="Pipeline")
    F = u.fields(t["id"])
    extra = [u.j("POST", f"/tables/{t['id']}/fields", json={"name": f"Field {i}", "type": "text"})["id"] for i in range(7)]
    u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Deal", F["Status"]: "Todo", F["Notes"]: "A long note " * 12,
                                                            **{fid: f"value {i}" for i, fid in enumerate(extra)}}})
    board = u.j("POST", f"/tables/{t['id']}/views", json={"name": "Board", "type": "kanban"})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}/{board['id']}")
    heads = lambda: [x.strip() for x in pg.locator(".kanban-col .kanban-head .chip").all_inner_texts()]
    expect(pg.locator(".kanban-col")).to_have_count(3)
    assert heads() == ["Todo", "Doing", "Done"]
    # every visible field on the card (not just the first few), the card growing downwards
    card = pg.locator(".kanban .card", has_text="Deal")
    expect(card.locator(".card-field")).to_have_count(8)
    box = card.bounding_box()
    assert box["width"] <= pg.locator(".kanban-col").first.bounding_box()["width"]
    # a column moved from its menu, then dragged by its header
    pg.locator(".kanban-col[data-col='Done'] .kanban-col-menu").click()
    pg.click(".popover .menu-item:has-text('Move to the start')")
    expect(pg.locator(".kanban-col").first).to_have_attribute("data-col", "Done")
    pg.locator(".kanban-col[data-col='Todo'] .kanban-head").drag_to(pg.locator(".kanban-col[data-col='Done'] .kanban-head"))
    expect(pg.locator(".kanban-col").first).to_have_attribute("data-col", "Todo")
    pg.wait_for_timeout(800)
    pg.reload()
    expect(pg.locator(".kanban-col").first).to_have_attribute("data-col", "Todo")   # kept with the view
    order = u.j("GET", f"/tables/{t['id']}")["views"]
    assert next(v for v in order if v["id"] == board["id"])["config"]["columnOrder"][:2] == ["Todo", "Done"]
    assert not pg.errors, pg.errors


def test_a_whole_base_shared_by_link_and_added_as_a_base(browser):
    owner, guest = Client(), Client()
    jobs = owner.table(name="Jobs")
    J = owner.fields(jobs["id"])
    owner.j("POST", f"/tables/{jobs['id']}/records", json={"data": {J["Name"]: "Mill St"}})
    owner.table(name="Clients")
    pg = new_page(browser)
    sign_in(pg, owner)
    pg.click(".side-user .side-item")
    pg.click(".settings-tabs button:has-text('Public links')")
    pg.click("button:has-text('New public link')")
    pg.fill(".modal input[placeholder='Team calendar']", "Everything")
    pg.locator(".modal .whole-base input").check()
    expect(pg.locator(".modal .share-items")).to_have_count(0)            # no tables to pick: all of them
    pg.click(".modal-foot button:has-text('Create link')")
    pg.wait_for_timeout(500)
    s = next(x for x in owner.j("GET", "/shares") if x["name"] == "Everything")
    assert s["whole_base"]
    pg.close()
    # someone else adds it to their Probyte as a base of its own
    pg = new_page(browser)
    sign_in(pg, guest)
    pg.click(".side-add >> nth=0")
    pg.click(".kind:has-text('From a link')")
    pg.fill(".modal input[placeholder*='/s/']", f"{BASE}/s/{s['token']}")
    pg.click(".modal-foot button:has-text('Check link')")
    expect(pg.locator(".modal .link-as input").first).to_be_checked()   # a whole base: added as a base, by default
    pg.click(".modal-foot button:has-text('Create')")
    expect(pg.locator(".page-head h1")).to_have_text("Jobs", timeout=15000)
    expect(pg.locator(".grid tbody")).to_contain_text("Mill St")
    b = next(x for x in guest.j("GET", "/workspace")["bases"] if x.get("linked"))
    expect(pg.locator(".sidebar")).to_contain_text("Clients")
    # its settings, from the base's menu
    row = pg.locator(".sidebar .base-row", has_text=b["name"]).first
    row.hover()
    row.locator(".base-more").click()
    pg.click(".popover .menu-item:has-text('Linked base')")
    modal = pg.locator(".modal", has_text="Linked base")
    expect(modal).to_contain_text("2 tables")
    owner.table(name="Invoices")
    modal.locator("button:has-text('Check now')").click()
    expect(modal).to_contain_text("Added Invoices")
    expect(pg.locator(".sidebar")).to_contain_text("Invoices")
    assert not pg.errors, pg.errors
