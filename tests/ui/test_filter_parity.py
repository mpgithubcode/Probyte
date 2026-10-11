"""The same filter and sort cases (tests/filter_cases.json) through the server's filtering (API, public links)
and the browser's (instant filtering in the app): both must give the same records in the same order."""
import json
import os

import pytest
from playwright.sync_api import sync_playwright

from conftest import BASE, Client

CASES = json.load(open(os.path.join(os.path.dirname(__file__), "..", "filter_cases.json")))


@pytest.fixture(scope="module")
def setup():
    u = Client()
    t = u.table(name="Parity")
    for f in CASES["fields"]:
        u.j("POST", f"/tables/{t['id']}/fields", json={"name": f["name"], "type": f["type"], "options": f.get("options", {})})
    F = u.fields(t["id"])
    for rec in CASES["records"]:
        u.j("POST", f"/tables/{t['id']}/records", json={"data": {F[k]: v for k, v in rec.items() if v is not None}})
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(BASE)
        pg.fill("input[autocomplete=username]", u.username)
        pg.fill("input[type=password]", u.password)
        pg.click("button:has-text('Sign in')")
        pg.wait_for_selector(".page, .welcome")
        pg.wait_for_function("() => !!window.__probyteViews")
        yield u, t, F, pg
        b.close()


def ids(node, F):
    """Field names -> field ids, in a filter (group or flat list) or a sort list."""
    if isinstance(node, list):
        return [ids(x, F) for x in node]
    if isinstance(node, dict):
        return {k: (F[v] if k == "field" else ids(v, F)) for k, v in node.items()}
    return node


@pytest.mark.parametrize("case", CASES["cases"], ids=[c["name"] for c in CASES["cases"]])
def test_server_and_browser_agree(setup, case):
    u, t, F, pg = setup
    flt = case.get("filter")
    config = {}
    if flt is not None:
        config["filter"] = ids(flt, F) if isinstance(flt, dict) else {"conjunction": "and", "items": ids(flt, F)}
    if case.get("sorts"):
        config["sorts"] = ids(case["sorts"], F)
    view = u.j("POST", f"/tables/{t['id']}/views", json={"name": case["name"][:40], "type": "grid", "config": config})
    server = [r["data"][F["Name"]] for r in u.j("GET", f"/tables/{t['id']}/records", params={"view": view["id"]})["records"]]
    data = u.j("GET", f"/tables/{t['id']}")
    browser = pg.evaluate("""([records, fields, config, refs, nameId]) =>
        window.__probyteViews.applyView(records, fields, config, "", refs).map(r => r.data[nameId])""",
                          [data["records"], data["fields"], config, data["refs"], F["Name"]])
    assert server == case["expect"], ("server", server)
    assert browser == case["expect"], ("browser", browser)
