"""OAuth 2.1 for MCP clients: discovery, registration rules, consent outcomes, PKCE, code reuse, base limits."""
import base64
import hashlib
import re
import secrets

import requests

from conftest import BASE

CB = "https://example.com/cb"


def register(uris=(CB,)):
    return requests.post(BASE + "/oauth/register", json={"client_name": "Test app", "redirect_uris": list(uris)})


def pkce():
    ver = secrets.token_urlsafe(40)
    return ver, base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest()).rstrip(b"=").decode()


def params(client_id, challenge, **extra):
    return {"response_type": "code", "client_id": client_id, "redirect_uri": CB, "state": "xyz", "code_challenge": challenge,
            "code_challenge_method": "S256", "scope": "read", **extra}


def authorize(user, client_id, challenge, **form):
    data = {**params(client_id, challenge), "decision": "allow", "username": user.username, "password": user.password,
            "access": "read", "bases_all": "all", **form}
    return requests.post(BASE + "/oauth/authorize", data=data, allow_redirects=False)


def code_of(resp):
    return re.search(r"code=([^&]+)", resp.headers["location"]).group(1)


def exchange(client_id, code, ver):
    return requests.post(BASE + "/oauth/token", data={"grant_type": "authorization_code", "code": code, "code_verifier": ver,
                                                      "client_id": client_id, "redirect_uri": CB})


def test_discovery_documents():
    as_meta = requests.get(BASE + "/.well-known/oauth-authorization-server").json()
    assert as_meta["authorization_endpoint"].endswith("/oauth/authorize") and as_meta["code_challenge_methods_supported"] == ["S256"]
    pr = requests.get(BASE + "/.well-known/oauth-protected-resource/mcp").json()
    assert pr["resource"].endswith("/mcp") and pr["authorization_servers"]
    assert requests.get(BASE + "/.well-known/openid-configuration").status_code in (200, 404)


def test_registration_rules():
    assert register().status_code in (200, 201)
    assert register(("http://evil.example.com/cb",)).status_code == 400          # plain http only on loopback
    assert register(("http://127.0.0.1:33418/cb",)).status_code in (200, 201)
    assert register(()).status_code == 400
    assert requests.post(BASE + "/oauth/register", data="nope", headers={"Content-Type": "application/json"}).status_code == 400


def test_consent_outcomes(user):
    cid = register().json()["client_id"]
    ver, ch = pkce()
    page = requests.get(BASE + "/oauth/authorize", params=params(cid, ch))
    assert page.status_code == 200 and "Test app" in page.text
    denied = authorize(user, cid, ch, decision="deny")
    assert "error=access_denied" in denied.headers["location"] and "state=xyz" in denied.headers["location"]
    wrong = authorize(user, cid, ch, password="not-the-password")
    assert wrong.status_code == 401 and "Wrong username or password" in wrong.text
    # an unregistered redirect address is never redirected to (no open redirect)
    bad = requests.get(BASE + "/oauth/authorize", params={**params(cid, ch), "redirect_uri": "https://attacker.example/cb"}, allow_redirects=False)
    assert bad.status_code in (200, 400) and "attacker.example" not in bad.headers.get("location", "")
    nopkce = requests.get(BASE + "/oauth/authorize", params={**params(cid, ch), "code_challenge_method": "plain"}, allow_redirects=False)
    assert "error=invalid_request" in nopkce.headers.get("location", "")
    assert requests.get(BASE + "/oauth/authorize", params={**params("unknown-client", ch)}, allow_redirects=False).status_code in (200, 400)


def test_codes_are_single_use_and_bound(user):
    cid = register().json()["client_id"]
    other = register().json()["client_id"]
    ver, ch = pkce()
    code = code_of(authorize(user, cid, ch))
    assert exchange(other, code, ver).json()["error"] == "invalid_grant"         # issued to another app
    tok = exchange(cid, code, ver).json()
    assert tok["token_type"] == "Bearer"
    assert exchange(cid, code, ver).json()["error"] == "invalid_grant"           # used already
    assert requests.post(BASE + "/oauth/token", data={"grant_type": "refresh_token", "refresh_token": "x"}).json()["error"] == "unsupported_grant_type"


def test_base_limited_token_and_reconnecting(user):
    user.base_id  # the default base first
    other_base = user.j("POST", "/bases", json={"name": "Hidden"})
    user.j("POST", "/tables", json={"name": "In hidden", "base_id": other_base["id"]})
    user.table(name="Visible one")
    cid = register().json()["client_id"]
    ver, ch = pkce()
    code = code_of(authorize(user, cid, ch, bases_all="some", base=user.base_id))
    t1 = exchange(cid, code, ver).json()["access_token"]
    H = {"Authorization": "Bearer " + t1}
    names = str(requests.get(BASE + "/api/v1/bases", headers=H).json())
    assert "Visible one" in names and "In hidden" not in names
    assert requests.post(BASE + "/api/v1/tables/Visible one/records", headers=H, json={"fields": {"Name": "x"}}).status_code == 403  # read access
    # connecting the same app again replaces its old token
    ver2, ch2 = pkce()
    t2 = exchange(cid, code_of(authorize(user, cid, ch2)), ver2).json()["access_token"]
    assert requests.get(BASE + "/api/v1/me", headers={"Authorization": "Bearer " + t2}).status_code == 200
    assert requests.get(BASE + "/api/v1/me", headers=H).status_code == 401
    keys = user.j("GET", "/keys")
    keys = keys if isinstance(keys, list) else keys.get("keys", [])
    assert sum(1 for k in keys if "connected app" in k["name"]) == 1
