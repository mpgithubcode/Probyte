"""Public REST API (/api/v1), MCP server and OAuth."""
import base64
import hashlib
import json
import re
import secrets
import threading

import requests
from conftest import API, BASE


def key(user, scope):
    return user.j("POST", "/keys", json={"name": f"test {scope}", "scope": scope})["key"]


def test_rest_v1(user):
    W = {"Authorization": "Bearer " + key(user, "write")}
    R = {"X-API-Key": key(user, "read")}
    t = requests.post(BASE + "/api/v1/bases/My Base/tables", headers=W, json={"name": "Leads", "fields": [
        {"name": "Email", "type": "email"}, {"name": "Stage", "type": "single_select", "options": {"choices": ["New", "Won"]}},
        {"name": "Value", "type": "number"}, {"name": "Next call", "type": "date", "options": {"include_time": True}}]}).json()
    assert [f["type"] for f in t["fields"]][:2] == ["email", "single_select"]
    r = requests.post(BASE + "/api/v1/tables/Leads/records", headers=W, json={"records": [
        {"fields": {"Email": "ada@x.org", "Stage": "New", "Value": "1,200", "Next call": "2026-10-20T15:00:00Z"}},
        {"fields": {"Email": "bob@x.org", "Stage": "Lost", "Value": 50}}]})
    assert r.status_code == 201 and r.json()["records"][0]["fields"]["Value"] == 1200
    assert requests.post(BASE + "/api/v1/tables/Leads/records", headers=W, json={"fields": {"Nmae": "x"}}).status_code == 400
    assert requests.post(BASE + "/api/v1/tables/Leads/records", headers=R, json={"fields": {"Email": "x"}}).status_code == 403
    q = requests.get(BASE + "/api/v1/tables/Leads/records", headers=R, params={"filter": json.dumps([{"field": "Value", "op": "gt", "value": 100}])}).json()
    assert q["total"] == 1
    up = requests.post(BASE + "/api/v1/tables/Leads/records/upsert", headers=W, json={"match_on": ["Email"], "records": [
        {"fields": {"Email": "ADA@x.org", "Stage": "Won"}}, {"fields": {"Email": "cy@x.org"}}]}).json()
    assert len(up["updated"]) == 1 and len(up["created"]) == 1
    spec = requests.get(BASE + "/api/v1/openapi.json").json()
    assert spec["servers"][0]["url"].endswith("/api/v1")


def rpc(headers, method, params=None, url=BASE + "/mcp"):
    return requests.post(url, headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})


def test_mcp_tools_and_scopes(user):
    t = user.table("generic", "Notes")
    W = {"Authorization": "Bearer " + key(user, "write")}
    R = {"Authorization": "Bearer " + key(user, "read")}
    assert rpc({}, "tools/list").status_code == 401
    init = rpc(W, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    assert init.json()["result"]["protocolVersion"] == "2025-06-18"
    assert len(rpc(W, "tools/list").json()["result"]["tools"]) > len(rpc(R, "tools/list").json()["result"]["tools"])
    res = rpc(W, "tools/call", {"name": "create_records", "arguments": {"table": "Notes", "records": [{"Name": "From MCP"}]}}).json()["result"]
    assert not res["isError"]
    res = rpc(R, "tools/call", {"name": "list_records", "arguments": {"table": t["id"]}}).json()["result"]
    assert json.loads(res["content"][0]["text"])["records"][0]["fields"]["Name"] == "From MCP"
    assert rpc(R, "tools/call", {"name": "delete_records", "arguments": {"table": "Notes", "ids": [t["id"]]}}).json()["result"]["isError"]


def test_oauth_flow(user):
    reg = requests.post(BASE + "/oauth/register", json={"client_name": "Test", "redirect_uris": ["https://example.com/cb"]}).json()
    ver = secrets.token_urlsafe(40)
    ch = base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest()).rstrip(b"=").decode()
    p = {"response_type": "code", "client_id": reg["client_id"], "redirect_uri": "https://example.com/cb", "state": "s1",
         "code_challenge": ch, "code_challenge_method": "S256", "scope": "read"}
    ok = requests.post(BASE + "/oauth/authorize", allow_redirects=False,
                       data={**p, "decision": "allow", "username": user.username, "password": user.password, "access": "read", "bases_all": "all"})
    code = re.search(r"code=([^&]+)", ok.headers["location"]).group(1)
    bad = requests.post(BASE + "/oauth/token", data={"grant_type": "authorization_code", "code": code, "code_verifier": "x", "client_id": reg["client_id"]})
    assert bad.json()["error"] == "invalid_grant"
    tok = requests.post(BASE + "/oauth/token", data={"grant_type": "authorization_code", "code": code, "code_verifier": ver, "client_id": reg["client_id"]}).json()
    assert tok["token_type"] == "Bearer"
    assert len(rpc({"Authorization": "Bearer " + tok["access_token"]}, "tools/list").json()["result"]["tools"]) > 0


def test_webhooks_signed_delivery(user):
    import socket
    from http.server import BaseHTTPRequestHandler, HTTPServer
    got = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            got.append((self.headers["X-Probyte-Event"], self.headers["X-Probyte-Signature"], body))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("0.0.0.0", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host = socket.gethostbyname(socket.gethostname())
    t = user.table("generic", "Hooked")
    h = user.j("POST", "/v1/webhooks", json={"url": f"http://{host}:{srv.server_port}/hook", "tables": [t["id"]]})
    F = user.fields(t["id"])
    import time
    time.sleep(1.2)
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Hello"}})
    for _ in range(40):
        if got:
            break
        time.sleep(0.25)
    assert got, "no webhook delivery"
    ev, sig, body = got[0]
    import hmac
    assert ev == "record.created"
    assert sig == "sha256=" + hmac.new(h["secret"].encode(), body, hashlib.sha256).hexdigest()
    assert json.loads(body)["record"]["fields"]["Name"] == "Hello"


def test_public_link(user):
    t = user.table("generic", "Shared")
    F = user.fields(t["id"])
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Visible"}})
    view = user.j("GET", f"/tables/{t['id']}")["views"][0]
    s = user.j("POST", "/shares", json={"name": "Link", "base_id": user.base_id, "items": [{"kind": "table", "id": t["id"], "views": [view["id"]]}],
                                       "permissions": {"add": True}})
    anon = requests.Session()
    meta = anon.get(API + f"/public/{s['token']}").json()
    assert meta["permissions"]["add"] is True
    payload = anon.get(API + f"/public/{s['token']}/views/{view['id']}").json()
    assert [r["data"].get(F["Name"]) for r in payload["records"]] == ["Visible"]
    assert all(f["type"] != "record_id" for f in payload["fields"])   # hidden by default
    assert anon.post(API + f"/public/{s['token']}/views/{view['id']}/records", json={"data": {F["Name"]: "From link"}}).status_code == 200
