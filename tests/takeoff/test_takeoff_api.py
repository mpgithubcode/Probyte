"""Blueprint takeoff (extension): reading a drawing set, legends, finding items, review, measuring, exports."""
import csv
import io
import os
import uuid

import fitz
import pytest
import requests

from conftest import API, Client
from drawing_set import A_SPOTS, B_SPOTS, REC_SPOTS, make

X = API + "/x/takeoff"


@pytest.fixture(scope="module")
def admin():
    name, pw = os.environ["PROBYTE_ADMIN"].split(":")
    a = Client(name, pw)
    yield a
    a.j("PUT", "/extensions/takeoff", json={"enabled": False})


@pytest.fixture(scope="module")
def setup(admin, tmp_path_factory):
    admin.j("PUT", "/extensions/takeoff", json={"enabled": True})
    u = Client()
    pdf = make(str(tmp_path_factory.mktemp("set") / "set.pdf"))
    r = u.post(X + "/projects", files={"file": ("Test set.pdf", open(pdf, "rb"), "application/pdf")}, data={"name": "Test job"})
    assert r.status_code == 200, r.text
    return u, r.json()


def test_switched_on_by_the_admin_only(admin, setup):
    other = Client()
    assert other.put(API + "/extensions/takeoff", json={"enabled": False}).status_code == 403
    ext = next(e for e in other.j("GET", "/extensions") if e["id"] == "takeoff")
    assert ext["enabled"] and ext["name"] == "Blueprint takeoff"
    admin.j("PUT", "/extensions/takeoff", json={"enabled": False})
    try:
        assert other.get(X + "/projects").status_code == 404           # off: its API isn't there
    finally:
        admin.j("PUT", "/extensions/takeoff", json={"enabled": True})
    assert other.get(X + "/projects").json() == []


def test_sheets_are_read(setup):
    u, p = setup
    pages = {g["sheet_no"]: g for g in p["pages"]}
    assert list(pages) == ["T1.0", "E1.0", "E2.0", "A1.0"]
    assert pages["E1.0"]["title"] == "ELECTRICAL LIGHTING PLAN" and pages["E1.0"]["discipline"] == "Electrical"
    assert pages["A1.0"]["discipline"] == "Architectural" and pages["T1.0"]["discipline"] == "Title / general"
    # scales: the plan's (next to "LIGHTING PLAN"), the detail's as another choice
    e1 = pages["E1.0"]
    assert e1["scale"]["label"] == "1/4\" = 1'-0\"" and abs(e1["scale"]["ft_per_pt"] - 1 / 18) < 1e-9
    assert any(s["label"].startswith("1 1/2\"") for s in e1["scales"])
    assert abs(pages["A1.0"]["scale"]["ft_per_pt"] - 1 / 9) < 1e-9
    assert not any(g["included"] for g in p["pages"])                  # a person chooses the sheets
    # someone else can't see it
    assert Client().get(X + f"/projects/{p['id']}").status_code == 404
    # the sheet picture
    img = u.get(X + f"/projects/{p['id']}/pages/1/image?dpi=50")
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"


def legend_items(u, p):
    """The E1.0 schedule's entries added as items (as the screen does: the legend left out of finds)."""
    leg = u.j("GET", f"/x/takeoff/projects/{p['id']}/pages/1/legends")
    sched = next(t for t in leg if "FIXTURE SCHEDULE" in (t["heading"] or ""))
    rows = {r["tag"]: r for r in sched["rows"]}
    assert set(rows) == {"A", "B"}
    assert rows["A"]["name"] == "2X4 LED TROFFER" and rows["A"]["details"] == {"MANUFACTURER": "LITHONIA"}
    assert rows["A"]["symbol"] and rows["B"]["symbol"]
    items = u.j("POST", f"/x/takeoff/projects/{p['id']}/items", json={"items": [
        {"name": r["name"], "tag": r["tag"], "category": sched["heading"], "details": r["details"],
         "symbol": {"page": 1, "rect": r["symbol"], "legend": sched["rect"]}} for r in sched["rows"]]})
    return {i["tag"]: i for i in items}


def test_legend_find_review_and_takeoff(setup):
    u, p = setup
    pid = p["id"]
    u.j("PATCH", f"/x/takeoff/projects/{pid}/pages", json={"pages": [1, 2], "included": True})
    items = legend_items(u, p)
    # A by its label: the 7 fixtures, not the legend, nor the "A" in the notes or the plan title
    r = u.j("POST", f"/x/takeoff/items/{items['A']['id']}/find", json={"method": "tag"})
    assert r["method"] == "tag" and r["found"] == 7, r
    marks = u.j("GET", f"/x/takeoff/projects/{pid}/marks?item={items['A']['id']}")
    assert all(m["status"] == "suggested" for m in marks)
    for x, y in A_SPOTS:  # each one next to a fixture
        assert any(abs(m["geom"]["points"][0][0] - (x + 18)) < 12 and abs(m["geom"]["points"][0][1] - (y - 13)) < 12 for m in marks), (x, y)
    # B by its symbol: 4, the turned one too, not the legend's
    r = u.j("POST", f"/x/takeoff/items/{items['B']['id']}/find", json={"method": "symbol", "pages": [1]})
    assert r["found"] == 4, r
    bm = u.j("GET", f"/x/takeoff/projects/{pid}/marks?item={items['B']['id']}")
    for x, y in B_SPOTS:
        assert any(abs(m["geom"]["points"][0][0] - x) < 4 and abs(m["geom"]["points"][0][1] - y) < 4 for m in bm), (x, y)
    # nothing confirmed yet: nothing in the takeoff, 7 to review
    s = {x["tag"]: x for x in u.j("GET", f"/x/takeoff/projects/{pid}/summary")}
    assert s["A"]["qty"] == 0 and s["A"]["pending"] == 7
    # reject one, accept the rest; finding again keeps those decisions
    u.j("PATCH", f"/x/takeoff/marks/{marks[0]['id']}", json={"status": "rejected"})
    assert u.j("POST", f"/x/takeoff/projects/{pid}/marks/review", json={"item_id": items["A"]["id"], "status": "confirmed"})["changed"] == 6
    r = u.j("POST", f"/x/takeoff/items/{items['A']['id']}/find", json={"method": "tag"})
    assert r["found"] == 0
    # one added by hand; 10% waste
    u.j("POST", f"/x/takeoff/projects/{pid}/marks", json={"item_id": items["A"]["id"], "page_no": 1, "geom": {"type": "point", "points": [[50, 50]]}})
    u.j("PATCH", f"/x/takeoff/items/{items['A']['id']}", json={"waste_pct": 10, "unit_cost": 85.5})
    s = {x["tag"]: x for x in u.j("GET", f"/x/takeoff/projects/{pid}/summary")}
    assert s["A"]["qty"] == 7 and s["A"]["total"] == 8 and s["A"]["extended"] == 684.0      # 7 × 1.1 = 7.7 → 8
    assert s["A"]["pages"] == [{"page_no": 1, "sheet_no": "E1.0", "qty": 7, "marks": 7}]


def test_legend_boxed_by_hand_and_receptacles(setup):
    u, p = setup
    pid = p["id"]
    rows = u.j("POST", f"/x/takeoff/projects/{pid}/pages/2/legend-in", json={"rect": [1690, 170, 2040, 270]})
    rec = next(r for r in rows if r["name"] == "DUPLEX RECEPTACLE")
    assert rec["symbol"] and rec["category"] == "ELECTRICAL LEGEND"
    it = u.j("POST", f"/x/takeoff/projects/{pid}/items", json={"items": [{"name": rec["name"], "symbol": {"page": 2, "rect": rec["symbol"]}}]})[0]
    u.j("PATCH", f"/x/takeoff/projects/{pid}/pages/2", json={"excluded": [[1690, 170, 2040, 270]]})
    r = u.j("POST", f"/x/takeoff/items/{it['id']}/find", json={"pages": [2]})
    assert r["method"] == "symbol" and r["found"] == len(REC_SPOTS), r


def test_measuring_with_the_scale(setup):
    u, p = setup
    pid = p["id"]
    wire = u.j("POST", f"/x/takeoff/projects/{pid}/items", json={"items": [{"name": "Branch circuit", "kind": "length", "per_run": 5}]})[0]
    assert wire["unit"] == "LF"
    m = u.j("POST", f"/x/takeoff/projects/{pid}/marks", json={"item_id": wire["id"], "page_no": 1,
                                                                "geom": {"type": "line", "points": [[300, 1300], [480, 1300], [660, 1300]]}})
    assert abs(m["value"] - 20) < 0.01                                  # 360 pt at 1/4" = 1'-0"
    room = u.j("POST", f"/x/takeoff/projects/{pid}/items", json={"items": [{"name": "Ceiling", "kind": "area"}]})[0]
    a = u.j("POST", f"/x/takeoff/projects/{pid}/marks", json={"item_id": room["id"], "page_no": 1,
                                                                "geom": {"type": "area", "points": [[0, 0], [180, 0], [180, 180], [0, 180]]}})
    assert abs(a["value"] - 100) < 0.01                                 # 10' × 10'
    s = {x["name"]: x for x in u.j("GET", f"/x/takeoff/projects/{pid}/summary")}
    assert s["Branch circuit"]["qty"] == 25                             # + 5' per run
    # calibrating: the line is 40', not 20' (the sheet was printed at half size)
    u.j("POST", f"/x/takeoff/projects/{pid}/pages/1/calibrate", json={"points": [[300, 1300], [660, 1300]], "feet": 40})
    s = {x["name"]: x for x in u.j("GET", f"/x/takeoff/projects/{pid}/summary")}
    assert s["Branch circuit"]["qty"] == 45 and s["Ceiling"]["qty"] == 400
    # a sheet without a scale: its lengths wait for one
    u.j("PATCH", f"/x/takeoff/projects/{pid}/pages/0", json={"scale": {}})
    u.j("POST", f"/x/takeoff/projects/{pid}/marks", json={"item_id": wire["id"], "page_no": 0, "geom": {"type": "line", "points": [[0, 0], [72, 0]]}})
    s = {x["name"]: x for x in u.j("GET", f"/x/takeoff/projects/{pid}/summary")}
    assert s["Branch circuit"]["unscaled"] == ["T1.0"]


def test_search_and_exports(setup):
    u, p = setup
    pid = p["id"]
    hits = u.j("GET", f"/x/takeoff/projects/{pid}/search?q=LED&pages=all")
    assert [h["page_no"] for h in hits] == [1] and len(hits[0]["rects"]) == 2
    rows = list(csv.reader(io.StringIO(u.get(X + f"/projects/{pid}/export.csv").content.decode("utf-8-sig"))))
    assert rows[0][:4] == ["Item", "Category", "Label", "Quantity"]
    a = next(r for r in rows if r[2] == "A")
    assert a[3] == "7" and a[6] == "8" and a[9] == "E1.0: 7" and "LITHONIA" in a[10]
    pdf = u.get(X + f"/projects/{pid}/export.pdf")
    doc = fitz.open(stream=pdf.content, filetype="pdf")
    assert doc.page_count == 3 and "TAKEOFF: Test job" in doc[1].get_text()   # the sheets in the takeoff, and the one marked
    # into a Probyte table
    r = u.j("POST", f"/x/takeoff/projects/{pid}/export/table", json={"base_id": u.base_id})
    F = u.fields(r["table_id"])
    recs = u.records(r["table_id"])
    a = next(x for x in recs if x["data"].get(F["Label"]) == "A")
    assert a["data"][F["Item"]] == "2X4 LED TROFFER" and a["data"][F["Total"]] == 8 and a["data"][F["Extended"]] == 684.0
    # a schedule from a sheet into a table
    tables = u.j("GET", f"/x/takeoff/projects/{pid}/pages/1/tables")
    sched = next(t for t in tables if t["heading"] and "SCHEDULE" in t["heading"])
    r = u.j("POST", f"/x/takeoff/projects/{pid}/export/schedule", json={"base_id": u.base_id, "cells": sched["cells"], "name": "Fixtures"})
    F = u.fields(r["table_id"])
    assert r["rows"] == 2 and sorted(x["data"][F["DESCRIPTION"]] for x in u.records(r["table_id"])) == ["2X4 LED TROFFER", "LED DOWNLIGHT"]
    # deleting the project
    u.j("DELETE", f"/x/takeoff/projects/{pid}")
    assert u.get(X + f"/projects/{pid}").status_code == 404
