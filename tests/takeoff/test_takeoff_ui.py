"""Blueprint takeoff in the browser: switch it on, upload a set, choose sheets, legend → items, find and review,
count and measure by hand, the takeoff and its export."""
import os
import re

import pytest
from playwright.sync_api import expect, sync_playwright

from conftest import API, BASE, Client
from drawing_set import make

SHOTS = "tests/.artifacts"


def sign_in(pg, u):
    pg.goto(BASE)
    pg.fill("input[autocomplete=username]", u.username)
    pg.fill("input[type=password]", u.password)
    pg.click("button:has-text('Sign in')")
    pg.wait_for_selector(".page, .welcome")


def sheet_xy(pg, x, y):
    """Where a point of the sheet (PDF points) is on the screen."""
    box = pg.locator(".tk-sheet").bounding_box()
    w = pg.evaluate("parseFloat(document.querySelector('.tk-sheet').style.width)")
    s = box["width"] / w
    return box["x"] + x * s, box["y"] + y * s


@pytest.fixture
def admin():
    name, pw = os.environ["PROBYTE_ADMIN"].split(":")
    a = Client(name, pw)
    yield a
    a.j("PUT", "/extensions/takeoff", json={"enabled": False})


def test_takeoff_in_the_browser(admin, tmp_path):
    os.makedirs(SHOTS, exist_ok=True)
    pdf = make(str(tmp_path / "Lighting set.pdf"))
    u = Client()
    with sync_playwright() as p:
        b = p.chromium.launch()
        # the admin switches it on
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        sign_in(pg, admin)
        pg.click(".side-user .side-item")
        pg.click(".settings-tabs button:has-text('Extensions')")
        card = pg.locator(".ext-card", has_text="Blueprint takeoff")
        expect(card).to_contain_text("Count symbols")
        card.locator(".switch").click()
        expect(card.locator("input")).to_be_checked()
        pg.close()

        pg = b.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("dialog", lambda d: d.accept(answers.pop(0) if answers else ""))
        answers = []
        sign_in(pg, u)
        pg.click(".side-extensions .side-item:has-text('Blueprint takeoff')")
        expect(pg.locator(".tk-empty")).to_contain_text("Start a takeoff")
        pg.set_input_files(".tk-projects input[type=file]", pdf)
        expect(pg.locator(".tk-name")).to_have_value("Lighting set", timeout=30000)
        # sheets: read from the title blocks; include the electrical ones
        rows = pg.locator(".tk-sheet-row")
        expect(rows).to_have_count(4)
        expect(rows.nth(1).locator(".tk-sheet-no")).to_have_value("E1.0")
        expect(rows.nth(1).locator("input[aria-label='Sheet title']")).to_have_value("ELECTRICAL LIGHTING PLAN")
        pg.click(".tk-chips .chip:has-text('Electrical')")
        pg.click(".tk-bulk button:has-text('Include electrical')")
        expect(pg.locator(".tk-tab.active")).to_contain_text("Sheets · 2/4")
        rows = pg.locator(".tk-sheet-row")
        rows.first.click()                                        # E1.0
        expect(pg.locator(".tk-scale")).to_contain_text("1/4\" = 1'-0\"")
        rows.first.locator(".tk-ok").click()
        expect(rows.first.locator(".tk-ok")).to_have_class(re.compile("on"))
        pg.wait_for_timeout(500)
        pg.screenshot(path=f"{SHOTS}/takeoff-sheets.png")
        # legend → items
        pg.click(".tk-tab:has-text('Items')")
        pg.click("button:has-text('Read legends on this sheet')")
        modal = pg.locator(".modal", has_text="Legend entries on E1.0")
        expect(modal.locator("tbody tr")).to_have_count(2)
        expect(modal.locator(".tk-crop")).to_have_count(2)
        pg.screenshot(path=f"{SHOTS}/takeoff-legend.png")
        modal.locator("button:has-text('Add 2 items')").click()
        expect(pg.locator(".tk-detail")).to_be_visible()          # the first one opens
        expect(pg.locator(".tk-detail input[aria-label=Name]")).to_have_value("2X4 LED TROFFER")
        # find A by its label, accept them
        pg.click(".tk-find button:has-text('Find')")
        expect(pg.locator(".tk-result")).to_contain_text("Found 7: E1.0 7")
        expect(pg.locator(".tk-mark.suggested")).to_have_count(7)
        pg.click("button:has-text('Accept all on sheet')")
        expect(pg.locator(".tk-mark.confirmed")).to_have_count(7)
        expect(pg.locator(".tk-detail-qty")).to_contain_text("7 EA")
        # a mark: reject it from its menu, then accept it again
        pg.locator(".tk-mark.confirmed").first.click()
        menu = pg.locator(".tk-mark-menu")
        expect(menu).to_contain_text("In the takeoff")
        menu.locator("button:has-text('Reject')").click()
        expect(pg.locator(".tk-detail-qty")).to_contain_text("6 EA")
        # B by its symbol
        pg.click("button:has-text('All items')")
        pg.click(".tk-item-row:has-text('LED DOWNLIGHT')")
        pg.click(".tk-find label:has-text('By its symbol')")
        pg.click(".tk-find button:has-text('Find')")
        expect(pg.locator(".tk-result")).to_contain_text("Found 4", timeout=30000)
        pg.screenshot(path=f"{SHOTS}/takeoff-review.png")
        pg.click("button:has-text('Accept all on sheet')")
        expect(pg.locator(".tk-detail-qty")).to_contain_text("4 EA")
        # count one more by clicking
        pg.click("button:has-text('Count by clicking')")
        expect(pg.locator(".tk-tool.on")).to_have_attribute("aria-label", re.compile("^Count"))
        x, y = sheet_xy(pg, 1500, 1100)
        pg.mouse.click(x, y)
        expect(pg.locator(".tk-detail-qty")).to_contain_text("5 EA")
        # a run, measured: the 20' wall
        pg.click("button:has-text('All items')")
        answers.append("Branch circuit")
        pg.click(".tk-actions button:has-text('Run')")
        expect(pg.locator(".tk-detail input[aria-label=Name]")).to_have_value("Branch circuit")
        pg.click("button:has-text('Measure runs')")
        x1, y1 = sheet_xy(pg, 300, 1300)
        x2, y2 = sheet_xy(pg, 660, 1300)
        pg.mouse.click(x1, y1)
        pg.mouse.move(x2, y2)
        expect(pg.locator(".tk-readout")).to_contain_text(re.compile(r"^(19|20)'-"))
        pg.mouse.dblclick(x2, y2)
        expect(pg.locator(".tk-note")).to_contain_text(re.compile(r"Branch circuit: (19|20)'-"))
        pg.keyboard.press("Escape")
        # the takeoff
        pg.click(".tk-tab:has-text('Takeoff')")
        table = pg.locator(".tk-summary")
        expect(table.locator(".tk-sum-row", has_text="2X4 LED TROFFER")).to_contain_text("6 EA")
        expect(table.locator(".tk-sum-row", has_text="LED DOWNLIGHT")).to_contain_text("5 EA")
        expect(table.locator(".tk-sum-row", has_text="Branch circuit")).to_contain_text(re.compile(r"(19|20)(\.\d)? LF"))
        pg.screenshot(path=f"{SHOTS}/takeoff-summary.png")
        expect(pg.locator(".tk-pane a:has-text('CSV')")).to_have_attribute("href", re.compile("export.csv"))
        pg.click("button:has-text('To a Probyte table')")
        expect(pg.locator(".page-head h1, .title-row h1")).to_contain_text("Takeoff – Lighting set", timeout=15000)
        expect(pg.locator(".grid tbody")).to_contain_text("2X4 LED TROFFER")
        assert not errors, errors
        b.close()
