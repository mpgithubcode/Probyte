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

    # no page switch on the record panel: pages are set up in Table settings
    expect(pg.locator(".record-modal button:has-text('Page')")).to_have_count(0)
    pg.keyboard.press("Escape")
    pg.click(".page-head [aria-label=More]")
    pg.click(".settings-nav button:has-text('Page')")
    pg.click(".designer-head input[type=checkbox]")                    # open this table's records as pages
    eventually(lambda: (u.j("GET", f"/tables/{projects['id']}")["table"]["page"] or {}).get("enabled") or None)
    # every field is a block to begin with: hide one, add a heading, show Jobs as cards in a row, half wide
    expect(pg.locator(".designer-field.on", has_text="Brief")).to_have_count(1)
    pg.click(".designer-field:has-text('Brief')")
    expect(pg.locator(".designer-field.on", has_text="Brief")).to_have_count(0)
    pg.click(".designer-add button:has-text('Heading')")
    pg.locator(".block-heading").fill("Work")
    pg.locator(".block-heading").press("Tab")
    jobs = pg.locator(".page-block", has=pg.locator(".block-name", has_text="Jobs"))
    jobs.locator("[aria-label='Jobs settings']").click()                  # each block's choices are behind its ⚙
    pg.locator(".popover select[aria-label='Show Jobs as']").select_option("hcards")
    pg.locator(".popover .block-span button:has-text('½')").click()
    pg.click(".designer-sample .muted")                                    # (a click elsewhere closes the menu)
    expect(pg.locator(".popover")).to_have_count(0)
    def layout():
        blocks = (u.j("GET", f"/tables/{projects['id']}")["table"]["page"] or {}).get("blocks") or []
        j = next((b for b in blocks if b.get("field") == link["id"]), {})
        return blocks if j.get("view") == "hcards" and j.get("span") == 2 and any(b.get("text") == "Work" for b in blocks) else None
    blocks = eventually(layout)
    assert not any(b.get("field") == brief["id"] for b in blocks)
    # sized by dragging its right edge: a quarter narrower
    grid = pg.locator(".designer .page-blocks").bounding_box()
    h = jobs.locator(".resize-x").bounding_box()
    pg.mouse.move(h["x"] + h["width"] / 2, h["y"] + h["height"] / 2)
    pg.mouse.down()
    pg.mouse.move(h["x"] + h["width"] / 2 - grid["width"] / 4, h["y"] + h["height"] / 2, steps=5)
    pg.mouse.up()
    eventually(lambda: next(b for b in u.j("GET", f"/tables/{projects['id']}")["table"]["page"]["blocks"] if b.get("field") == link["id"]).get("span") == 1 or None)
    pg.click(".modal-foot button:has-text('Done')")

    # the record opens as a page: no Page / Record switch, no Edit
    pg.goto(f"{BASE}/#/table/{projects['id']}?record={p['id']}")
    expect(pg.locator(".page-view .page-title-input")).to_have_value("Mill St")
    expect(pg.locator(".page-view .segmented, .page-view button:has-text('Edit page')")).to_have_count(0)
    expect(pg.locator(".page-view h2.page-heading")).to_have_text("Work")
    expect(pg.locator(".page-view .linked-row .linked-card")).to_have_count(1)
    expect(pg.locator(".page-view .page-label", has_text="Brief")).to_have_count(0)
    # the title is the primary field and wraps (never wider than the page)
    title = pg.locator(".page-title-input")
    long_name = "Mill St service upgrade and a very long description of the whole job " * 2
    title.fill(long_name)
    title.press("Enter")
    eventually(lambda: u.record(projects["id"], p["id"])["data"][u.fields(projects["id"])["Name"]] == long_name.strip() or None)
    assert title.evaluate("t => t.scrollWidth <= t.clientWidth + 1 && t.clientHeight > 70")
    # write in the page's body (the first long text), and place a field in the text
    pg.locator(".page-doc-body .ProseMirror").click()
    pg.keyboard.type("Scope of work")
    pg.click(".page-doc .rt-btn:has-text('Field')")
    pg.click(".popover .menu-item:has-text('Jobs')")
    expect(pg.locator(".page-embed")).to_contain_text("Jobs")
    eventually(lambda: "Scope of work" in (u.record(projects["id"], p["id"])["data"].get(notes) or "")
               and "{{field:" + link["id"] + "}}" in u.record(projects["id"], p["id"])["data"][notes] or None)
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


def test_choosing_the_primary_field_and_how_fields_are_shown(browser):
    u = Client()
    tasks, projects, link, brief, a, b, p = setup(u)
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{tasks['id']}")
    pg.click(".page-head [aria-label=More]")
    pg.click(".settings-nav button:has-text('Fields')")
    pg.locator("label:has-text('Primary field') select").select_option(label="Hours")
    expect(pg.locator(".grid thead th").nth(1)).to_contain_text("Hours", timeout=8000)   # the first column
    eventually(lambda: [f["name"] for f in u.j("GET", f"/tables/{tasks['id']}")["fields"] if f.get("is_primary")] == ["Hours"] or None)
    pg.click(".modal-foot button:has-text('Done')")
    # in the projects table, the linked tasks are called by their hours now; a number shown as a bar in the table
    hours = next(f for f in u.j("GET", f"/tables/{tasks['id']}")["fields"] if f["name"] == "Hours")
    u.j("PATCH", f"/fields/{hours['id']}", json={"options": {"views": {"cell": "bar", "record": "stat", "max": 10}}})
    pg.goto(f"{BASE}/#/table/{tasks['id']}")
    expect(pg.locator(".grid .value-bar").first).to_be_visible()
    pg.goto(f"{BASE}/#/table/{tasks['id']}?record={b['id']}")
    expect(pg.locator(".record-modal .stat-value")).to_have_text("5")
    pg.locator(".record-modal .display-edit").click()                    # another look: a click edits it
    pg.locator(".record-modal .form-row", has_text="Hours").locator("input").fill("7")
    pg.keyboard.press("Enter")
    eventually(lambda: u.record(tasks["id"], b["id"])["data"][hours["id"]] == 7 or None)
    assert not pg.errors, pg.errors


PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR4nGP4z8AARAwQCgAf7gP9i18U1AAAAABJRU5ErkJggg=="


def rich_page(u):
    """A Jobs table whose records open as pages, with sections, tabs, related tasks, files, numbers, conditions and a
    layout of its own for finished jobs."""
    import base64  # noqa: F401
    jobs = u.table(name="Jobs")
    J = u.fields(jobs["id"])
    add = lambda name, typ, **o: u.j("POST", f"/tables/{jobs['id']}/fields", json={"name": name, "type": typ, "options": o})
    photos = add("Photos", "attachment")
    budget = add("Budget", "number", numberFormat="currency", currency="USD")
    tasks = u.table(name="Crew tasks")
    T = u.fields(tasks["id"])
    job_link = u.j("POST", f"/tables/{tasks['id']}/fields", json={"name": "Job", "type": "link", "options": {"foreignTableId": jobs["id"], "symmetric": False}})
    job = u.j("POST", f"/tables/{jobs['id']}/records", json={"data": {J["Name"]: "Mill St service upgrade", J["Status"]: "Todo", budget["id"]: 12500}})
    for n in ("Pull permit", "Order panel"):
        u.j("POST", f"/tables/{tasks['id']}/records", json={"data": {T["Name"]: n, job_link["id"]: [job["id"]]}})
    done_only = {"conjunction": "and", "items": [{"field": J["Status"], "op": "is", "value": "Done"}]}
    u.j("PATCH", f"/tables/{jobs['id']}", json={"page": {
        "enabled": True, "header": [J["Status"]], "outline": True, "template": "## Scope\n\n## Materials",
        "variants": {"field": J["Status"], "layouts": {"Done": [{"id": "d1", "type": "heading", "text": "Closed out"}, {"id": "d2", "type": "field", "field": budget["id"]}]}},
        "blocks": [
            {"id": "st", "type": "stats", "items": [{"field": budget["id"], "view": "stat"}], "span": 2},
            {"id": "hd", "type": "heading", "text": "Finished", "when": done_only},
            {"id": "sec", "type": "section", "text": "Crew", "blocks": [
                {"id": "rel", "type": "related", "table": tasks["id"], "field": job_link["id"], "text": "Crew tasks", "display": "list"}]},
            {"id": "tb", "type": "tabs", "tabs": [{"id": "tab1", "text": "Photos", "blocks": [{"id": "ph", "type": "files", "field": photos["id"]}]},
                                                  {"id": "tab2", "text": "Notes", "blocks": [{"id": "nb", "type": "doc", "field": J["Notes"]}]}]},
            {"id": "tall", "type": "properties", "height": 1400},                          # (room to scroll the title away)
        ]}})
    return jobs, J, tasks, T, job_link, job, photos, budget


def test_rich_pages(browser, tmp_path):
    import base64
    u = Client()
    jobs, J, tasks, T, job_link, job, photos, budget = rich_page(u)
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{jobs['id']}?record={job['id']}")
    view = pg.locator(".page-view")
    expect(view.locator(".page-stat")).to_contain_text("12,500")                       # numbers side by side
    expect(view.locator(".page-header-field")).to_contain_text("Status")                # under the title
    expect(view.locator("h2.page-heading", has_text="Finished")).to_have_count(0)       # its condition: Done only
    # a related list: the tasks linking to this job; New adds one already linked
    rel = view.locator(".page-related")
    expect(rel.locator(".linked-list li")).to_have_count(2)
    rel.locator("button:has-text('New')").click()
    expect(pg.locator(".modal .record-title")).to_be_visible()
    eventually(lambda: sum(1 for r in u.records(tasks["id"]) if r["data"].get(job_link["id"]) == [job["id"]]) == 3 or None)
    pg.keyboard.press("Escape")
    expect(rel.locator(".linked-list li")).to_have_count(3)
    # a section folds; tabs; the outline opens a tab
    view.locator(".page-section-head:has-text('Crew')").click()
    expect(rel).to_be_hidden()
    view.locator(".page-section-head:has-text('Crew')").click()
    expect(view.locator(".page-tab-panel.active")).to_contain_text("Photos")
    view.locator(".page-outline button:has-text('Notes')").click()
    expect(view.locator(".page-tab-panel.active")).to_contain_text("Notes")
    # files: added from the page
    view.locator(".page-tab-strip button:has-text('Photos')").click()
    png = tmp_path / "site.png"
    png.write_bytes(base64.b64decode(PNG_B64))
    view.locator(".page-files input[type=file]").set_input_files(str(png))
    eventually(lambda: [a["name"] for a in u.record(jobs["id"], job["id"])["data"].get(photos["id"]) or []] == ["site.png"] or None)
    expect(view.locator(".page-files .thumb")).to_have_count(1)
    # the title and the fields under it stay in view when scrolled away
    view.locator(".page-scroll").evaluate("e => e.scrollTo(0, e.scrollHeight)")
    expect(view.locator(".page-sticky")).to_contain_text("Mill St service upgrade")
    # printing: just the page
    pg.evaluate("() => { window.print = () => { window.__printed = document.body.classList.contains('print-page'); }; }")
    view.locator(".page-bar [aria-label='Record actions']").click()
    pg.click(".popover .menu-item:has-text('Print or save as PDF')")
    pg.wait_for_function("() => window.__printed === true")
    # a finished job: its condition shows the heading, and finished jobs have a layout of their own
    u.j("PATCH", f"/records/{job['id']}", json={"data": {J["Status"]: "Done"}})
    expect(view.locator("h2.page-heading", has_text="Closed out")).to_be_visible(timeout=8000)
    expect(view.locator(".page-related")).to_have_count(0)
    assert not pg.errors, pg.errors


def test_a_template_for_new_records_and_sharing_one_page(browser):
    u = Client()
    jobs, J, tasks, T, job_link, job, photos, budget = rich_page(u)
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{jobs['id']}")
    pg.click(".new-record-btn")
    expect(pg.locator(".page-view .page-tab-strip")).to_be_visible()
    pg.locator(".page-view .page-tab-strip button:has-text('Notes')").click()
    expect(pg.locator(".page-view .page-doc-body h2").first).to_have_text("Scope")       # the template
    pg.keyboard.press("Escape")
    # share one page: a link to just this record
    pg.goto(f"{BASE}/#/table/{jobs['id']}?record={job['id']}")
    pg.locator(".page-bar [aria-label='Record actions']").click()
    pg.click(".popover .menu-item:has-text('Share this page')")
    pg.click(".modal-foot button:has-text('Make the link')")
    url = pg.locator(".modal input[aria-label='Link to the page']").input_value()
    assert "/s/" in url
    guest = new_page(browser)
    guest.goto(url.replace("http://localhost:8000", BASE) if "localhost" in url else url)
    expect(guest.locator(".page-view .page-title-text")).to_have_text("Mill St service upgrade")
    expect(guest.locator(".page-view .page-title-text")).to_be_visible()
    expect(guest.locator(".page-view .page-stat")).to_be_visible()
    expect(guest.locator(".page-view .page-stat")).to_contain_text("12,500")
    expect(guest.locator(".page-view .page-related")).to_have_count(0)                  # other tables stay private
    expect(guest.locator("body")).not_to_contain_text("Pull permit")
    assert not guest.errors, guest.errors
    assert not pg.errors, pg.errors


def test_the_designer_places_blocks_in_sections_and_tabs(browser):
    u = Client()
    jobs, J, tasks, T, job_link, job, photos, budget = rich_page(u)
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{jobs['id']}")
    pg.click(".page-head [aria-label=More]")
    pg.click(".settings-nav button:has-text('Page')")
    # Status shown, then put into the Crew section
    pg.click(".designer-field:has-text('Status')")
    status = pg.locator(".page-block", has=pg.locator(".block-name", has_text="Status")).last
    status.locator("[aria-label='Status settings']").click()
    pg.locator(".popover select[aria-label='Put in']").select_option(label="Section: Crew")
    def placed():
        page = u.j("GET", f"/tables/{jobs['id']}")["table"]["page"]
        sec = next(b for b in page["blocks"] if b["id"] == "sec")
        return any(b.get("field") == J["Status"] for b in sec["blocks"]) or None
    eventually(placed)
    # a related list added from the list of tables linking here
    pg.click(".designer-add button:has-text('Related list')")
    pg.click(".popover .menu-item:has-text('Crew tasks')")
    eventually(lambda: sum(1 for b in u.j("GET", f"/tables/{jobs['id']}")["table"]["page"]["blocks"] if b["type"] == "related") == 1 or None)
    # the layout for finished jobs is edited on its own
    pg.locator("select[aria-label='Layout being edited']").select_option(label="Done · its own layout")
    expect(pg.locator(".designer .page-heading", has_text="Closed out")).to_be_visible()
    assert not pg.errors, pg.errors


def test_combined_databases_have_pages_and_the_record_panel_shows_page_bodies(browser):
    u = Client()
    a, b = u.table(name="Jobs A"), u.table(name="Jobs B")
    B = u.fields(b["id"])
    budget = u.j("POST", f"/tables/{b['id']}/fields", json={"name": "Budget", "type": "number", "options": {}})
    rec = u.j("POST", f"/tables/{b['id']}/records", json={"data": {B["Name"]: "Combo job", budget["id"]: 900,
                                                                    B["Notes"]: "## Scope\n\n{{field:" + budget["id"] + "}}\n\nSome words"}})
    # the record panel: a page body (here: a long text with a field placed in it) is formatted, its field live
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{b['id']}?record={rec['id']}")
    doc = pg.locator(".record-modal .record-doc")
    expect(doc.locator("h2")).to_have_text("Scope")
    expect(doc.locator(".page-embed")).to_contain_text("Budget")
    expect(pg.locator(".record-modal")).not_to_contain_text("{{field:")
    pg.keyboard.press("Escape")
    # a combined database: pages laid out in its settings, its records open as them
    c = u.j("POST", "/combos", json={"name": "All jobs", "base_id": u.base_id, "sources": [{"table_id": a["id"]}, {"table_id": b["id"]}]})
    pg.goto(f"{BASE}/#/combo/{c['id']}")
    pg.click(".page-head [aria-label=More]")
    pg.click(".settings-nav button:has-text('Page')")
    pg.click(".designer-head input[type=checkbox]")
    eventually(lambda: (u.j("GET", f"/combos/{c['id']}")["combo"]["page"] or {}).get("enabled") or None)
    pg.click(".modal-foot button:has-text('Done')")
    pg.goto(f"{BASE}/#/combo/{c['id']}?record={rec['id']}")
    expect(pg.locator(".page-view .page-title-input")).to_have_value("Combo job")
    expect(pg.locator(".page-view .page-embed")).to_contain_text("Budget")
    assert not pg.errors, pg.errors


def test_a_page_switched_on_elsewhere_is_used_at_once(browser):
    """The table is open in the browser; its page is switched on elsewhere (another window, the API): the next record
    opened is a page (the table's copy notices the change, as it does for fields)."""
    u = Client()
    t = u.table(name="Visits")
    T = u.fields(t["id"])
    rec = u.j("POST", f"/tables/{t['id']}/records", json={"data": {T["Name"]: "Site visit"}})
    pg = new_page(browser)
    sign_in(pg, u)
    pg.goto(f"{BASE}/#/table/{t['id']}")
    expect(pg.locator(".grid tbody")).to_contain_text("Site visit")
    u.j("PATCH", f"/tables/{t['id']}", json={"page": {"enabled": True, "blocks": [{"id": "s", "type": "field", "field": T["Status"]}]}})
    pg.goto(f"{BASE}/#/table/{t['id']}?record={rec['id']}")
    expect(pg.locator(".page-view .page-title-input")).to_have_value("Site visit")
    assert not pg.errors, pg.errors
