"""Blueprint takeoff for AI: its MCP tools, used one after the other as an assistant would, and its REST API with an
API key."""
import json
import os
import uuid

import pytest
import requests

from conftest import API, BASE, Client, S3_URL
from drawing_set import make

S3 = {"kind": "s3", "endpoint": S3_URL, "bucket": "test-bucket", "username": "testkey", "secret": "testsecret12345"}


def rpc(h, method, params=None):
    r = requests.post(BASE + "/mcp", headers={**h, "Accept": "application/json, text/event-stream"},
                      json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def call(h, tool, **args):
    res = rpc(h, "tools/call", {"name": tool, "arguments": args})
    if res.get("isError"):
        return {"error": res["content"][0]["text"]}
    return json.loads(res["content"][0]["text"])


@pytest.fixture(scope="module")
def admin():
    name, pw = os.environ["PROBYTE_ADMIN"].split(":")
    a = Client(name, pw)
    a.j("PUT", "/extensions/takeoff", json={"enabled": True})
    yield a
    a.j("PUT", "/extensions/takeoff", json={"enabled": False})


def test_an_assistant_does_a_takeoff(admin, tmp_path):
    u = Client()
    W = {"Authorization": "Bearer " + u.j("POST", "/keys", json={"name": "ai", "scope": "write"})["key"]}
    R = {"Authorization": "Bearer " + u.j("POST", "/keys", json={"name": "ai read", "scope": "read"})["key"]}
    drive = u.table("files", "Plans", storage=S3, storage_prefix=f"ai{uuid.uuid4().hex[:6]}")
    pdf = make(str(tmp_path / "Job 12.pdf"))
    rec = u.post(API + f"/tables/{drive['id']}/files", files=[("files", ("Job 12.pdf", open(pdf, "rb"), "application/pdf"))]).json()["records"][0]

    names = [t["name"] for t in rpc(W, "tools/list")["tools"]]
    assert {"takeoff_create_project", "takeoff_find", "takeoff_review", "takeoff_summary"} <= set(names)
    ro = [t["name"] for t in rpc(R, "tools/list")["tools"]]
    assert "takeoff_get_project" in ro and "takeoff_find" not in ro

    p = call(W, "takeoff_create_project", record_id=rec["id"], name="Job 12")
    assert [s["sheet"] for s in p["sheets"]] == ["T1.0", "E1.0", "E2.0", "A1.0", "E3.0"]
    s = call(W, "takeoff_update_sheets", project="Job 12", disciplines=["Electrical"], included=True)
    assert sorted(x["sheet"] for x in s["sheets"]) == ["E1.0", "E2.0", "E3.0"] and all(x["in_takeoff"] for x in s["sheets"])
    leg = call(W, "takeoff_read_legends", project="Job 12", sheet="E1.0")["legends"]
    assert leg[0]["heading"] == "LIGHT FIXTURE SCHEDULE" and [e["label"] for e in leg[0]["entries"]] == ["A", "B", "L"]
    added = call(W, "takeoff_add_items", project="Job 12", from_legend={"sheet": "E1.0", "entries": ["A", "LED DOWNLIGHT"]})["added"]
    assert [a["name"] for a in added] == ["2X4 LED TROFFER", "LED DOWNLIGHT"] and all(a["has_symbol"] for a in added)
    found = {r["item"]: r for r in call(W, "takeoff_find", project="Job 12", sheets=["E1.0"])["results"]}
    assert found["2X4 LED TROFFER"]["found"] == 7 and found["2X4 LED TROFFER"]["method"] == "label"
    assert found["LED DOWNLIGHT"]["by_sheet"] == {"E1.0": 4}
    marks = call(W, "takeoff_list_marks", project="Job 12", item="A", status="suggested")
    assert marks["total"] == 7 and marks["marks"][0]["sheet"] == "E1.0"
    assert call(W, "takeoff_review", project="Job 12", item="2X4 LED TROFFER", status="confirmed")["changed"] == 7
    assert call(W, "takeoff_review", project="Job 12", marks=[m["id"] for m in call(W, "takeoff_list_marks", project="Job 12",
                item="LED DOWNLIGHT", status="suggested")["marks"][:1]], status="rejected")["changed"] == 1
    # a run, measured by the assistant with the sheet's scale
    call(W, "takeoff_add_items", project="Job 12", items=[{"name": "Branch circuit", "kind": "length", "waste_pct": 10}])
    m = call(W, "takeoff_add_marks", project="Job 12", item="Branch circuit", sheet="E1.0", lines=[[[300, 1300], [660, 1300]]])
    assert abs(m["values"][0] - 20) < 0.01
    call(W, "takeoff_update_item", project="Job 12", item="A", unit_cost=120)
    t = {r["name"]: r for r in call(R, "takeoff_summary", project="Job 12")["takeoff"]}
    assert t["2X4 LED TROFFER"]["qty"] == 7 and t["2X4 LED TROFFER"]["extended"] == 840
    assert t["LED DOWNLIGHT"]["pending"] == 3 and t["Branch circuit"]["total"] == 22
    # read-only: may look, may not change
    assert "read-only" in call(R, "takeoff_find", project="Job 12")["error"]
    assert call(W, "takeoff_search_text", project="Job 12", query="LED")["hits"][0] == {"sheet": "E1.0", "count": 2, "places": call(W, "takeoff_search_text", project="Job 12", query="LED")["hits"][0]["places"]}
    out = call(W, "takeoff_export_table", project="Job 12")
    F = u.fields(out["table_id"])
    assert {r["data"][F["Item"]] for r in u.records(out["table_id"])} == {"2X4 LED TROFFER", "LED DOWNLIGHT", "Branch circuit"}
    # mistakes are explained
    assert "No sheet Z9" in call(W, "takeoff_read_legends", project="Job 12", sheet="Z9")["error"]
    assert "No takeoff called" in call(W, "takeoff_get_project", project="nope")["error"]
    # the REST API, with the same keys
    pid = p["project"]
    assert requests.get(API + "/x/takeoff/projects", headers=R).json()[0]["id"] == pid
    assert requests.post(API + f"/x/takeoff/projects/{pid}/items", headers=R, json={"items": [{"name": "x"}]}).status_code == 403
    lib = requests.get(API + f"/x/takeoff/projects/{pid}/symbols", headers=R).json()
    assert {"DUPLEX RECEPTACLE", "QUADRUPLEX RECEPTACLE", "2X4 LED TROFFER"} <= {x["name"] for x in lib}
    # switched off: the tools are gone
    admin.j("PUT", "/extensions/takeoff", json={"enabled": False})
    try:
        assert not any(t["name"].startswith("takeoff_") for t in rpc(W, "tools/list")["tools"])
    finally:
        admin.j("PUT", "/extensions/takeoff", json={"enabled": True})
