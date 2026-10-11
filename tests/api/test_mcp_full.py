"""MCP server in depth: every tool, errors, scopes, both transports, protocol details."""
import json
import threading
import queue

import requests

from conftest import BASE


def key(user, scope="write"):
    return {"Authorization": "Bearer " + user.j("POST", "/keys", json={"name": "mcp", "scope": scope})["key"]}


def rpc(h, method, params=None, id=1):
    r = requests.post(BASE + "/mcp", headers={**h, "Accept": "application/json, text/event-stream"},
                      json={"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}})
    assert r.status_code == 200, (r.status_code, r.text)
    return r.json()


def call(h, tool, **args):
    res = rpc(h, "tools/call", {"name": tool, "arguments": args})["result"]
    text = res["content"][0]["text"]
    try:
        body = json.loads(text)
    except ValueError:
        body = text
    return res["isError"], body


def test_every_tool(user):
    W = key(user)
    err, t = call(W, "create_table", name="Contacts", fields=[{"name": "Name"}, {"name": "Email", "type": "email"},
                  {"name": "Level", "type": "single_select", "options": {"choices": ["A", "B"]}}, {"name": "Score", "type": "number"}])
    assert not err, t
    err, _ = call(W, "add_field", table="Contacts", name="Double", type="formula", options={"expression": "{Score} * 2"})
    assert not err
    err, bases = call(W, "list_bases")
    assert not err and "Contacts" in json.dumps(bases)
    err, desc = call(W, "describe_table", table="Contacts")
    assert not err and {"Name", "Email", "Level", "Score", "Double"} <= {f["name"] for f in desc["fields"]}
    err, created = call(W, "create_records", table="Contacts", records=[{"Name": "Ada", "Email": "ada@x.org", "Level": "A", "Score": 3},
                                                                      {"Name": "Bob", "Email": "bob@x.org", "Level": "B", "Score": 5}])
    assert not err, created
    ids = [r["id"] for r in created["created"]]
    err, lst = call(W, "list_records", table="Contacts", filters=[{"field": "Score", "op": "gt", "value": 4}])
    assert not err and [r["fields"]["Name"] for r in lst["records"]] == ["Bob"] and str(lst["records"][0]["fields"]["Double"]) == "10"
    err, lst = call(W, "list_records", table="Contacts", sort=[{"field": "Score", "direction": "desc"}], limit=1)
    assert lst["records"][0]["fields"]["Name"] == "Bob" and lst["next_offset"] == 1
    err, rec = call(W, "get_record", record_id=ids[0])
    assert not err and rec["fields"]["Name"] == "Ada"
    err, found = call(W, "search", query="bob@")
    assert not err and "Bob" in json.dumps(found)
    err, up = call(W, "update_records", table="Contacts", records=[{"id": ids[0], "fields": {"Score": 9}}])
    assert not err and str(up["updated"][0]["fields"]["Score"]) == "9"
    err, ups = call(W, "upsert_records", table="Contacts", match_on=["Email"], records=[{"Email": "ADA@x.org", "Level": "B"}, {"Name": "Cy", "Email": "cy@x.org"}])
    assert not err and len(ups["updated"]) == 1 and len(ups["created"]) == 1
    err, d = call(W, "delete_records", table="Contacts", ids=[ids[1]])
    assert not err
    err, lst = call(W, "list_records", table="Contacts")
    assert lst["total"] == 2


def test_tool_errors_are_reported_not_crashes(user):
    W = key(user)
    for name, args in (("list_records", {"table": "Nope"}), ("get_record", {"record_id": "not-a-uuid"}),
                       ("create_records", {"table": "Nope", "records": [{"x": 1}]}), ("nonexistent_tool", {}),
                       ("create_records", {"table": "Nope"}), ("sync_table", {"table": "Nope"})):
        r = rpc(W, "tools/call", {"name": name, "arguments": args})
        assert ("error" in r) or r["result"]["isError"], (name, r)
    user.table(name="Typed")
    err, msg = call(W, "create_records", table="Typed", records=[{"Nmae": "typo"}])
    assert err and "Nmae" in str(msg)


def test_read_scope_and_isolation(user, other_user):
    R = key(user, "read")
    names = {t["name"] for t in rpc(R, "tools/list")["result"]["tools"]}
    assert "list_records" in names and "create_records" not in names and "delete_records" not in names
    err, _ = call(R, "create_table", name="Nope")
    assert err
    user.table(name="Mine only")
    err, bases = call(key(other_user), "list_bases")
    assert "Mine only" not in json.dumps(bases)


def test_protocol_details(user):
    W = key(user)
    init = rpc(W, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    assert init["result"]["serverInfo"]["name"] == "probyte" and "tools" in init["result"]["capabilities"]
    assert rpc(W, "ping")["result"] == {}
    assert rpc(W, "no/such/method")["error"]["code"] == -32601
    # notifications get no body back
    r = requests.post(BASE + "/mcp", headers=W, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code in (200, 202, 204)
    # batches and malformed requests
    bad = requests.post(BASE + "/mcp", headers={**W, "Content-Type": "application/json"}, data="{broken")
    assert bad.status_code in (200, 400) and "error" in bad.json()
    tools = rpc(W, "tools/list")["result"]["tools"]
    assert all("inputSchema" in t and "annotations" in t for t in tools)
    # unauthenticated: 401 with discovery info
    r = requests.post(BASE + "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401 and "resource_metadata" in r.headers.get("WWW-Authenticate", "")
    assert requests.options(BASE + "/mcp").headers.get("Access-Control-Allow-Origin") == "*"


def test_legacy_sse_transport(user):
    W = key(user)
    events = queue.Queue()

    def listen():
        with requests.get(BASE + "/mcp/sse", headers=W, stream=True, timeout=20) as r:
            ev = None
            for line in r.iter_lines(decode_unicode=True):
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    events.put((ev, line[5:].strip()))

    threading.Thread(target=listen, daemon=True).start()
    ev, endpoint = events.get(timeout=10)
    assert ev == "endpoint" and "/mcp/messages" in endpoint
    url = endpoint if endpoint.startswith("http") else BASE + endpoint
    r = requests.post(url, headers=W, json={"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
    assert r.status_code in (200, 202)
    ev, data = events.get(timeout=10)
    assert ev == "message" and json.loads(data)["id"] == 7
