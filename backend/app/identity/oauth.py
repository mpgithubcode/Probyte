"""OAuth 2.1 authorization server for MCP clients (Claude, ChatGPT, Cursor, VS Code, …).

Discovery (RFC 9728 / RFC 8414), dynamic client registration (RFC 7591), authorization code + PKCE (S256).
Approving a client creates an API key (source 'oauth') with the access and bases chosen on the consent page;
that key is the access token, so the connection shows up in Settings → API & MCP and can be revoked there.
"""
import base64
import hashlib
import hmac
import html
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.identity import auth
from app.core import ratelimit
from app.core.db import Jsonb, app_secret, connection

router = APIRouter()
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")
CODE_MINUTES = 10


def public_base(request: Request) -> str:
    """This server's public address (PUBLIC_URL, else what the reverse proxy forwarded)."""
    if PUBLIC_URL:
        return PUBLIC_URL
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    return f"{proto}://{host.split(',')[0].strip()}"


# ---------------------------------------------------------------- discovery

def _resource_meta(request: Request):
    base = public_base(request)
    return {"resource": f"{base}/mcp", "authorization_servers": [base], "bearer_methods_supported": ["header"],
            "scopes_supported": ["read", "write"], "resource_name": "Probyte",
            "resource_documentation": f"{base}/api/v1/docs"}


def _server_meta(request: Request):
    base = public_base(request)
    return {"issuer": base, "authorization_endpoint": f"{base}/oauth/authorize", "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register", "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"], "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
            "scopes_supported": ["read", "write"], "service_documentation": f"{base}/api/v1/docs"}


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
@router.get("/.well-known/oauth-protected-resource/mcp/sse")
def protected_resource(request: Request):
    return _resource_meta(request)


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/mcp")
@router.get("/.well-known/openid-configuration")
def authorization_server(request: Request):
    return _server_meta(request)


# ---------------------------------------------------------------- registration

def _redirect_ok(uri: str) -> bool:
    u = urlparse(uri)
    if not u.scheme or u.scheme.lower() in ("javascript", "data", "file", "vbscript"):
        return False
    if u.scheme == "http":
        return u.hostname in ("localhost", "127.0.0.1", "::1")  # loopback only (RFC 8252)
    return u.scheme == "https" and bool(u.netloc) or u.scheme not in ("http", "https")  # https or an app's own scheme


@router.post("/oauth/register")
async def register(request: Request):
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid_client_metadata", "error_description": "Send JSON"}, status_code=400)
    uris = body.get("redirect_uris") or []
    if not isinstance(uris, list) or not uris or not all(isinstance(u, str) and _redirect_ok(u) for u in uris):
        return JSONResponse({"error": "invalid_redirect_uri",
                             "error_description": "redirect_uris must be https, a loopback http address, or an app scheme"}, status_code=400)
    client_id = "pbc_" + secrets.token_urlsafe(18)
    name = str(body.get("client_name") or "AI assistant")[:100]
    with connection() as conn:
        conn.execute("INSERT INTO oauth_clients (client_id, client_name, redirect_uris) VALUES (%s,%s,%s)", (client_id, name, Jsonb(uris)))
    return JSONResponse({"client_id": client_id, "client_name": name, "redirect_uris": uris,
                         "grant_types": ["authorization_code"], "response_types": ["code"],
                         "token_endpoint_auth_method": "none", "client_id_issued_at": int(datetime.now().timestamp())}, status_code=201)


# ---------------------------------------------------------------- authorize (sign in + consent page)

def _client(conn, client_id):
    return conn.execute("SELECT * FROM oauth_clients WHERE client_id = %s", (client_id or "",)).fetchone()


def _csrf(session_token: str, client_id: str) -> str:
    return hmac.new(app_secret(), f"oauth|{session_token}|{client_id}".encode(), hashlib.sha256).hexdigest()


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect to Probyte</title><style>
:root{--bg:#f6f6f8;--card:#fff;--text:#18181b;--muted:#71717a;--border:#e4e4e7;--accent:#6366f1;--soft:#eef2ff;--danger:#dc2626}
@media (prefers-color-scheme:dark){:root{--bg:#0f0f12;--card:#18181c;--text:#f4f4f5;--muted:#a1a1aa;--border:#2e2e35;--soft:#25254a}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--text);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px}
.card{width:100%;max-width:440px;background:var(--card);border:1px solid var(--border);border-radius:14px;padding:24px;box-shadow:0 10px 30px rgba(0,0,0,.08)}
.brand{display:flex;align-items:center;gap:10px;font-weight:600;margin-bottom:14px}.logo{width:30px;height:30px;border-radius:8px;
background:linear-gradient(135deg,#6366f1,#a855f7);color:#fff;display:grid;place-items:center}
h1{font-size:18px;margin:0 0 6px}p{margin:0 0 14px;color:var(--muted)}b{color:var(--text)}
label{display:block;font-size:13px;font-weight:500;margin:12px 0 4px}input[type=text],input[type=password]{width:100%;padding:9px 11px;
border:1px solid var(--border);border-radius:8px;background:transparent;color:inherit;font:inherit}
.opt{display:flex;gap:8px;align-items:flex-start;padding:9px 11px;border:1px solid var(--border);border-radius:8px;margin:6px 0;font-weight:400;cursor:pointer}
.opt input{margin-top:3px;accent-color:var(--accent)}.opt small{display:block;color:var(--muted)}
.bases{padding-left:26px}.row{display:flex;gap:8px;margin-top:18px}button{flex:1;padding:10px;border-radius:8px;border:1px solid var(--border);
background:transparent;color:inherit;font:inherit;font-weight:500;cursor:pointer}button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.err{background:#fee2e2;color:var(--danger);padding:8px 11px;border-radius:8px;margin-bottom:10px;font-size:14px}
.note{font-size:12px;margin-top:14px}</style></head><body><main class="card">
<div class="brand"><span class="logo">P</span>Probyte</div>{body}</main>
<script>document.querySelectorAll('input[name=bases_all]').forEach(function(r){r.addEventListener('change',function(){
var b=document.querySelector('.bases');if(b)b.style.display=document.querySelector('input[name=bases_all][value=some]').checked?'block':'none';});});</script>
</body></html>"""


def _page(body: str, status=200):
    return HTMLResponse(PAGE.replace("{body}", body), status_code=status,
                        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'"})


def _error_page(msg: str):
    return _page(f"<h1>Can't connect</h1><p>{html.escape(msg)}</p>", 400)


def _form(params: dict, client, user, bases, csrf: str, error: str = "") -> str:
    esc = html.escape
    host = urlparse(params.get("redirect_uri", "")).netloc or params.get("redirect_uri", "")
    hidden = "".join(f'<input type="hidden" name="{k}" value="{esc(str(v))}">' for k, v in params.items()
                     if k in ("response_type", "client_id", "redirect_uri", "state", "code_challenge", "code_challenge_method", "scope", "resource"))
    want_write = "write" in (params.get("scope") or "write")
    login = "" if user else (
        '<label for="u">Username</label><input type="text" id="u" name="username" autocomplete="username" autofocus required>'
        '<label for="p">Password</label><input type="password" id="p" name="password" autocomplete="current-password" required>')
    base_list = "".join(f'<label class="opt"><input type="checkbox" name="base" value="{b["id"]}">{esc(b["name"])}</label>' for b in bases)
    who = f"Signed in as <b>{esc(user['username'])}</b>." if user else "Sign in to your Probyte account to continue."
    return f"""{f'<div class="err">{esc(error)}</div>' if error else ''}
<h1>Connect {esc(client['client_name'] or 'an app')}?</h1>
<p><b>{esc(client['client_name'] or 'This app')}</b> ({esc(host)}) wants to use your Probyte data. {who}</p>
<form method="post" action="/oauth/authorize">{hidden}<input type="hidden" name="csrf" value="{csrf}">{login}
<label>Access</label>
<label class="opt"><input type="radio" name="access" value="write" {'checked' if want_write else ''}><span>Read &amp; write<small>See, add, change and delete records and tables</small></span></label>
<label class="opt"><input type="radio" name="access" value="read" {'' if want_write else 'checked'}><span>Read only<small>See your data, change nothing</small></span></label>
<label>Bases</label>
<label class="opt"><input type="radio" name="bases_all" value="all" checked><span>All bases<small>Including ones you create later</small></span></label>
{f'<label class="opt"><input type="radio" name="bases_all" value="some"><span>Only some bases</span></label><div class="bases" style="display:none">{base_list}</div>' if base_list else ''}
<div class="row"><button type="submit" name="decision" value="deny">Cancel</button><button class="primary" type="submit" name="decision" value="allow">Allow</button></div>
<p class="note">You can disconnect it any time in Probyte under Settings → API &amp; MCP.</p></form>"""


def _session_user(request: Request):
    token = request.cookies.get(auth.SESSION_COOKIE)
    return (auth._session_user(token), token) if token else (None, None)


def _bases(user_id):
    with connection() as conn:
        return conn.execute("SELECT id, name FROM bases WHERE user_id = %s ORDER BY position, created_at", (user_id,)).fetchall()


def _check_params(conn, p: dict):
    client = _client(conn, p.get("client_id"))
    if not client:
        return None, "This app isn't registered with this Probyte server. Try connecting again from the app."
    if p.get("redirect_uri") not in client["redirect_uris"]:
        return None, "The app's return address doesn't match the one it registered."
    return client, None


def _back(p: dict, **extra) -> RedirectResponse:
    q = {k: v for k, v in extra.items() if v is not None}
    if p.get("state"):
        q["state"] = p["state"]
    sep = "&" if "?" in p["redirect_uri"] else "?"
    return RedirectResponse(p["redirect_uri"] + sep + urlencode(q), status_code=302)


@router.get("/oauth/authorize")
async def authorize_page(request: Request):
    p = dict(request.query_params)
    with connection() as conn:
        client, err = _check_params(conn, p)
    if err:
        return _error_page(err)
    if p.get("response_type") != "code":
        return _back(p, error="unsupported_response_type")
    if not p.get("code_challenge") or p.get("code_challenge_method", "plain") != "S256":
        return _back(p, error="invalid_request", error_description="PKCE with S256 is required")
    user, token = await run_in_threadpool(_session_user, request)
    bases = await run_in_threadpool(_bases, user["id"]) if user else []
    return _page(_form(p, client, user, bases, _csrf(token, p["client_id"]) if user else ""))


@router.post("/oauth/authorize")
async def authorize_submit(request: Request):
    p = dict(parse_qsl((await request.body()).decode(), keep_blank_values=True))
    multi = parse_qsl((await request.body()).decode())
    with connection() as conn:
        client, err = _check_params(conn, p)
    if err:
        return _error_page(err)
    if p.get("decision") != "allow":
        return _back(p, error="access_denied")
    user, token = await run_in_threadpool(_session_user, request)
    if user:
        if not hmac.compare_digest(p.get("csrf", ""), _csrf(token, p["client_id"])):
            return _error_page("This page expired. Go back to the app and connect again.")
    else:
        def login():
            with connection() as c:
                return c.execute("SELECT id, username, password_hash FROM users WHERE username = %s",
                                 ((p.get("username") or "").strip().lower(),)).fetchone()
        account = f"user:{(p.get('username') or '').strip().lower()}"
        try:
            ratelimit.check(account, request)
        except HTTPException as e:
            return _page(_form(p, client, None, [], "", e.detail), 429)
        row = await run_in_threadpool(login)
        if not row or not auth.verify_password(p.get("password") or "", row["password_hash"]):
            ratelimit.failed(account, request)
            return _page(_form(p, client, None, [], "", "Wrong username or password."), 401)
        ratelimit.succeeded(account)
        user = {"id": row["id"], "username": row["username"]}
    access = "write" if p.get("access") == "write" else "read"
    owned = {str(b["id"]) for b in await run_in_threadpool(_bases, user["id"])}
    chosen = [v for k, v in multi if k == "base" and v in owned]
    base_ids = chosen if p.get("bases_all") == "some" and chosen else None
    code = secrets.token_urlsafe(32)
    with connection() as conn:
        conn.execute("DELETE FROM oauth_codes WHERE expires_at < now()")
        conn.execute(
            "INSERT INTO oauth_codes (code_hash, client_id, user_id, redirect_uri, code_challenge, scope, base_ids, expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (hashlib.sha256(code.encode()).hexdigest(), client["client_id"], user["id"], p["redirect_uri"], p["code_challenge"],
             access, Jsonb(base_ids) if base_ids is not None else None, datetime.now(timezone.utc) + timedelta(minutes=CODE_MINUTES)))
    return _back(p, code=code, iss=public_base(request))


# ---------------------------------------------------------------- token

def _token_error(error: str, desc: str, status=400):
    return JSONResponse({"error": error, "error_description": desc}, status_code=status, headers={"Cache-Control": "no-store"})


@router.post("/oauth/token")
async def token(request: Request):
    raw = (await request.body()).decode()
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            p = {k: str(v) for k, v in json.loads(raw or "{}").items()}
        except ValueError:
            return _token_error("invalid_request", "Bad JSON")
    else:
        p = dict(parse_qsl(raw, keep_blank_values=True))
    basic = request.headers.get("authorization", "")
    if basic.lower().startswith("basic ") and not p.get("client_id"):
        try:
            p["client_id"] = base64.b64decode(basic[6:]).decode().split(":", 1)[0]
        except ValueError:
            pass
    if p.get("grant_type") != "authorization_code":
        return _token_error("unsupported_grant_type", "Only authorization_code is supported; access tokens don't expire")
    code_hash = hashlib.sha256((p.get("code") or "").encode()).hexdigest()
    with connection() as conn:
        row = conn.execute("SELECT * FROM oauth_codes WHERE code_hash = %s FOR UPDATE", (code_hash,)).fetchone()
        if not row or row["used"] or row["expires_at"] < datetime.now(timezone.utc):
            return _token_error("invalid_grant", "The authorization code is invalid or expired")
        if p.get("client_id") and p["client_id"] != row["client_id"]:
            return _token_error("invalid_grant", "The code was issued to another client")
        if p.get("redirect_uri") and p["redirect_uri"] != row["redirect_uri"]:
            return _token_error("invalid_grant", "redirect_uri doesn't match")
        verifier = p.get("code_verifier") or ""
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if not verifier or not hmac.compare_digest(challenge, row["code_challenge"]):
            return _token_error("invalid_grant", "PKCE verification failed")
        conn.execute("UPDATE oauth_codes SET used = true WHERE code_hash = %s", (code_hash,))
        client = _client(conn, row["client_id"])
        # One key per app and user: connecting again replaces the old one.
        conn.execute("DELETE FROM api_keys WHERE user_id = %s AND client_id = %s AND source = 'oauth'", (row["user_id"], row["client_id"]))
        key, key_hash = auth.new_api_key()
        conn.execute(
            "INSERT INTO api_keys (user_id, name, prefix, key_hash, scope, base_ids, source, client_id) VALUES (%s,%s,%s,%s,%s,%s,'oauth',%s)",
            (row["user_id"], f"{(client['client_name'] if client else None) or 'AI assistant'} (connected app)", key[:12], key_hash,
             row["scope"], Jsonb(row["base_ids"]) if row["base_ids"] is not None else None, row["client_id"]))
    return JSONResponse({"access_token": key, "token_type": "Bearer", "scope": "read write" if row["scope"] == "write" else "read"},
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
