"""Screenshots and short recordings of Probyte for the README, made on the isolated test stack with demo data.

    scripts/test.sh screens        → docs/screenshots/*.png and *.gif

Run it again after UI changes to refresh the pictures.
"""
import os
import random
from datetime import date, datetime, timedelta, timezone

from playwright.sync_api import sync_playwright

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from conftest import BASE, Client  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", ".artifacts", "screens")
DESK = {"viewport": {"width": 1280, "height": 760}, "device_scale_factor": 1}
random.seed(7)


def iso(d: date, h=None, m=0):
    return d.isoformat() if h is None else datetime(d.year, d.month, d.day, h, m, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed():
    u = Client("alex")  # the stack is fresh for every capture, so the name is free
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    # ---- clients and projects
    clients = u.table(name="Clients")
    C = u.fields(clients["id"])
    client_ids = {n: u.j("POST", f"/tables/{clients['id']}/records", json={"data": {C["Name"]: n}})["id"]
                  for n in ("Northwind", "Acme Co", "Globex", "Initech")}
    p = u.table(name="Projects")
    P = u.fields(p["id"])
    add = lambda name, type, **o: u.j("POST", f"/tables/{p['id']}/fields", json={"name": name, "type": type, "options": o})["id"]
    client = add("Client", "link", foreignTableId=clients["id"], relationship="one")
    budget = add("Budget", "number")
    start = add("Start", "date")
    due = add("Due", "date")
    progress = add("Progress", "rating", max=5)
    tags = add("Tags", "multi_select", choices=[{"name": n} for n in ("Web", "Mobile", "Brand", "Data")])
    add("Client name", "lookup", linkFieldId=client, lookupFieldId=C["Name"])
    add("Cost per day", "formula", expression="ROUND({Budget} / MAX(DATETIME_DIFF({Due}, {Start}, \"day\"), 1), 0)")
    rows = [("Website relaunch", "Doing", "Northwind", 18000, -12, 20, 3, ["Web", "Brand"]),
            ("iOS app v2", "Todo", "Acme Co", 42000, 5, 60, 1, ["Mobile"]),
            ("Brand guidelines", "Done", "Globex", 6500, -40, -10, 5, ["Brand"]),
            ("Data warehouse", "Doing", "Initech", 30000, -20, 35, 2, ["Data"]),
            ("Spring campaign", "Todo", "Northwind", 9000, 15, 45, 0, ["Brand", "Web"]),
            ("Customer portal", "Doing", "Acme Co", 24000, -5, 30, 2, ["Web"]),
            ("Analytics dashboard", "Done", "Globex", 12000, -60, -25, 5, ["Data", "Web"]),
            ("Onboarding emails", "Todo", "Initech", 3500, 8, 18, 1, ["Brand"])]
    for name, status, cl, b, s, d, pr, tg in rows:
        u.j("POST", f"/tables/{p['id']}/records", json={"data": {
            P["Name"]: name, P["Status"]: status, client: [client_ids[cl]], budget: b, start: iso(today + timedelta(days=s)),
            due: iso(today + timedelta(days=d)), progress: pr, tags: tg, P["Notes"]: f"Kick-off with {cl}."}})
    views = {}
    grid = u.j("GET", f"/tables/{p['id']}")["views"][0]
    u.j("PATCH", f"/views/{grid['id']}", json={"config": {"frozen": 1, "sorts": [{"field": due, "dir": "asc"}]}})
    views["projects_grid"] = grid["id"]
    views["projects_board"] = u.j("POST", f"/tables/{p['id']}/views", json={"name": "Board", "type": "kanban", "config": {"groupBy": P["Status"]}})["id"]
    views["projects_timeline"] = u.j("POST", f"/tables/{p['id']}/views", json={"name": "Timeline", "type": "timeline", "config": {
        "dateField": start, "endField": due, "colorField": P["Status"]}})["id"]
    views["projects_chart"] = u.j("POST", f"/tables/{p['id']}/views", json={"name": "Budget", "type": "chart", "config": {
        "chart": {"type": "bar", "agg": "sum", "valueField": budget, "xField": P["Status"]}}})["id"]
    # ---- a personal calendar: this week's events and tasks
    t = u.table("mixed", "Personal")
    F = u.fields(t["id"])
    events = [(0, 9, 10, "Team stand-up"), (0, 13, 14, "Lunch with Sam"), (1, 10, 12, "Design review"), (2, 15, 16, "Dentist"),
              (3, 9, 11, "Workshop: roadmap"), (3, 18, 20, "Climbing"), (4, 12, 13, "1:1 with Alex"), (4, 16, 17, "Demo day")]
    for day, h1, h2, title in events:
        d = monday + timedelta(days=day)
        u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: title, F["component"]: "Event",
                                                                 F["dtstart"]: iso(d, h1), F["dtend"]: iso(d, h2)}})
    tasks = [("Pay rent", 0, "Completed", "High"), ("Book flights", 2, "To Do", "High"), ("Renew passport", 4, "In Progress", "Medium"),
             ("Water plants", 1, "To Do", "Low"), ("Call mom", 3, "To Do", "Medium"), ("Return library books", 5, "Completed", "Low")]
    for title, day, status, prio in tasks:
        u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: title, F["component"]: "Task", F["status"]: status,
                                                                 F["priority"]: prio, F["dtend"]: iso(monday + timedelta(days=day))}})
    u.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Gym", F["component"]: "Event", F["dtstart"]: iso(monday, 7),
                                                             F["dtend"]: iso(monday, 8), F["rrule"]: "FREQ=WEEKLY;BYDAY=MO,WE,FR"}})
    cal = next(v for v in u.j("GET", f"/tables/{t['id']}")["views"] if v["type"] == "calendar")
    u.j("PATCH", f"/views/{cal['id']}", json={"config": {**cal["config"], "calMode": "week", "colorField": F["component"]}})
    views["personal_week"] = cal["id"]
    views["personal_month"] = u.j("POST", f"/tables/{t['id']}/views", json={"name": "Month", "type": "calendar", "config": {
        "dateField": F["dtstart"], "endField": F["dtend"], "calMode": "month", "colorField": F["component"]}})["id"]
    views["personal_list"] = u.j("POST", f"/tables/{t['id']}/views", json={"name": "To do", "type": "list", "config": {
        "filter": {"conjunction": "and", "items": [{"field": F["component"], "op": "is", "value": "Task"}]},
        "groups": [{"field": F["status"], "dir": "asc"}],
        "listProps": [{"field": F["done"], "align": "start", "label": False}, {"field": F["priority"], "align": "right", "label": False},
                      {"field": F["dtend"], "align": "right", "label": True}],
        "hidden": [f for k, f in F.items() if k in ("component", "status", "dtstart", "all_day", "location", "categories", "started",
                                                   "completed", "rrule", "description", "Record ID")]}})["id"]
    combo = u.j("POST", "/combos", json={"name": "Work & life", "base_id": u.base_id,
                                         "sources": [{"table_id": t["id"], "color": "#6366f1"}, {"table_id": p["id"], "color": "#f59e0b"}]})
    share = u.j("POST", "/shares", json={"name": "Project board", "base_id": u.base_id, "permissions": {"add": True},
                                         "items": [{"kind": "table", "id": p["id"], "views": [views["projects_board"]]}]})
    return u, {"projects": p["id"], "personal": t["id"], "combo": combo["id"], "share": share["token"]}, views


def sign_in(pg, u):
    pg.goto(BASE)
    pg.fill("input[autocomplete=username]", u.username)
    pg.fill("input[type=password]", u.password)
    pg.click("button:has-text('Sign in')")
    pg.wait_for_selector(".page, .welcome")


def go(pg, kind, id, view=None):
    pg.goto(f"{BASE}/#/{kind}/{id}" + (f"/{view}" if view and view != "calendar" else ""))
    pg.wait_for_selector(".view-tab")
    if view == "calendar":  # a combined database's calendar view, by type
        pg.click(".view-tab:has-text('Calendar')")
    pg.wait_for_timeout(700)


def main():
    os.makedirs(OUT, exist_ok=True)
    u, ids, views = seed()
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(**DESK)
        pg = ctx.new_page()
        sign_in(pg, u)
        shots = [("table", "table", ids["projects"], views["projects_grid"]), ("board", "table", ids["projects"], views["projects_board"]),
                 ("timeline", "table", ids["projects"], views["projects_timeline"]), ("chart", "table", ids["projects"], views["projects_chart"]),
                 ("calendar-week", "table", ids["personal"], views["personal_week"]),
                 ("calendar-month", "table", ids["personal"], views["personal_month"]), ("list", "table", ids["personal"], views["personal_list"]),
                 ("combined", "combo", ids["combo"], "calendar")]
        for name, kind, id, view in shots:
            go(pg, kind, id, view)
            pg.screenshot(path=f"{OUT}/{name}.png")
        # the record panel
        go(pg, "table", ids["projects"], views["projects_grid"])
        pg.locator(".grid tbody tr:not(.add-row):not(.spacer)").first.locator("td.rownum").click()
        pg.wait_for_timeout(400)
        pg.screenshot(path=f"{OUT}/record.png")
        pg.keyboard.press("Escape")
        # settings: connecting assistants and the API
        pg.click(".side-user .side-item")
        pg.click(".settings-tabs button:has-text('API & MCP')")
        pg.wait_for_timeout(400)
        pg.screenshot(path=f"{OUT}/settings-api.png")
        ctx.close()
        # dark mode
        ctx = b.new_context(**DESK, color_scheme="dark")
        pg = ctx.new_page()
        sign_in(pg, u)
        go(pg, "table", ids["personal"], views["personal_week"])
        pg.screenshot(path=f"{OUT}/dark.png")
        ctx.close()
        # phone: the to-do list, the month with its agenda, and a public link
        phone = {**p.devices["iPhone 13"], "device_scale_factor": 2}
        ctx = b.new_context(**phone)
        pg = ctx.new_page()
        sign_in(pg, u)
        go(pg, "table", ids["personal"], views["personal_list"])
        pg.screenshot(path=f"{OUT}/phone-list.png")
        go(pg, "table", ids["personal"], views["personal_month"])
        pg.screenshot(path=f"{OUT}/phone-calendar.png")
        pg.goto(f"{BASE}/s/{ids['share']}")
        pg.wait_for_selector(".kanban-col")
        pg.wait_for_timeout(500)
        pg.screenshot(path=f"{OUT}/phone-share.png")
        ctx.close()
        # recordings (converted to GIFs afterwards): editing in the table, and dragging on the board
        vid = {"viewport": {"width": 1100, "height": 620}, "record_video_dir": f"{OUT}/video-table", "record_video_size": {"width": 1100, "height": 620}}
        ctx = b.new_context(**vid)
        pg = ctx.new_page()
        sign_in(pg, u)
        go(pg, "table", ids["projects"], views["projects_grid"])
        pg.wait_for_timeout(800)
        pg.click(".add-row button:has-text('New record')")
        pg.wait_for_timeout(400)
        pg.locator(".grid tbody tr:not(.add-row):not(.spacer)").last.locator("td.cell").first.click()
        pg.keyboard.type("Holiday microsite", delay=70)
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(500)
        idx = pg.locator("thead th").evaluate_all("(ths) => ths.findIndex(th => th.textContent.includes('Budget'))")
        pg.locator(".grid tbody tr:not(.add-row):not(.spacer)").last.locator("td").nth(idx).click()
        pg.keyboard.type("7500", delay=90)
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(700)
        pg.fill(".view-options input[placeholder=Search]", "")
        pg.locator(".view-options input[placeholder=Search]").type("web", delay=120)
        pg.wait_for_timeout(1200)
        pg.fill(".view-options input[placeholder=Search]", "")
        pg.wait_for_timeout(800)
        ctx.close()
        vid["record_video_dir"] = f"{OUT}/video-board"
        ctx = b.new_context(**vid)
        pg = ctx.new_page()
        sign_in(pg, u)
        go(pg, "table", ids["projects"], views["projects_board"])
        pg.wait_for_timeout(800)
        for title, target in (("iOS app v2", "Doing"), ("Website relaunch", "Done")):
            card = pg.locator(".kanban-col .card").filter(has_text=title).bounding_box()
            col = pg.locator(".kanban-col").filter(has=pg.locator(".kanban-head", has_text=target)).first.bounding_box()
            pg.mouse.move(card["x"] + card["width"] / 2, card["y"] + 18)
            pg.mouse.down()
            pg.mouse.move(col["x"] + col["width"] / 2, col["y"] + 120, steps=25)
            pg.wait_for_timeout(200)
            pg.mouse.up()
            pg.wait_for_timeout(900)
        ctx.close()
        b.close()
    print("saved to", OUT)


if __name__ == "__main__":
    main()
