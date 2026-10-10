"""Accounts on the Radicale server bundled with Probyte.

Probyte owns Radicale's htpasswd file (on a volume shared by both containers) and rewrites it
from the users table. Each user gets a Radicale login with their Probyte username and a generated
"device password" to use on phones etc.; Probyte also adds that login as a CalDAV account for them.
"""
import base64
import logging
import os
import secrets
import string
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

import bcrypt

from .db import connection

log = logging.getLogger("probyte.dav")

RADICALE_URL = os.environ.get("RADICALE_URL", "")              # how Probyte reaches Radicale, e.g. http://radicale:5232/
RADICALE_HTPASSWD = os.environ.get("RADICALE_HTPASSWD", "")    # htpasswd file shared with Radicale
RADICALE_PUBLIC_URL = os.environ.get("RADICALE_PUBLIC_URL", "")  # what users enter on their devices

ACCOUNT_NAME = "Probyte CalDAV"


def enabled() -> bool:
    return bool(RADICALE_URL and RADICALE_HTPASSWD)


def generate_password() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(4))


def write_htpasswd(conn) -> None:
    if not enabled():
        return
    # Serialise writers until commit, so concurrent sign-ups can't drop each other's lines.
    conn.execute("SELECT pg_advisory_xact_lock(4242)")
    rows = conn.execute("SELECT username, radicale_hash FROM users WHERE radicale_hash IS NOT NULL ORDER BY username").fetchall()
    path = Path(RADICALE_HTPASSWD)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(f"{r['username']}:{r['radicale_hash']}\n" for r in rows))
    tmp.chmod(0o644)
    tmp.replace(path)  # atomic: Radicale re-reads the file on each login


def provision(conn, user_id) -> str:
    """Create or reset a user's Radicale login. Returns the new device password."""
    user = conn.execute("SELECT id, username FROM users WHERE id = %s", (user_id,)).fetchone()
    password = generate_password()
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode()
    conn.execute("UPDATE users SET radicale_hash = %s WHERE id = %s", (hashed, user_id))
    existing = conn.execute("SELECT id FROM caldav_accounts WHERE user_id = %s AND is_builtin", (user_id,)).fetchone()
    if existing:
        conn.execute("UPDATE caldav_accounts SET url = %s, username = %s, password = %s WHERE id = %s",
                     (RADICALE_URL, user["username"], password, existing["id"]))
    else:
        conn.execute(
            "INSERT INTO caldav_accounts (user_id, name, url, username, password, is_builtin) VALUES (%s,%s,%s,%s,%s,true)",
            (user_id, ACCOUNT_NAME, RADICALE_URL, user["username"], password),
        )
    write_htpasswd(conn)
    return password


def info(conn, user_id) -> dict:
    acc = conn.execute("SELECT username, password FROM caldav_accounts WHERE user_id = %s AND is_builtin", (user_id,)).fetchone()
    return {
        "enabled": enabled(),
        "provisioned": bool(acc),
        "public_url": RADICALE_PUBLIC_URL or None,
        "username": acc["username"] if acc else None,
        "password": acc["password"] if acc else None,
    }


# ---------------------------------------------------------------- pass-through for devices
# Probyte forwards CalDAV traffic to Radicale, so phones can use the same address as the web app.

DAV_METHODS = ["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS", "PROPFIND", "PROPPATCH", "MKCOL",
               "MKCALENDAR", "COPY", "MOVE", "REPORT", "LOCK", "UNLOCK", "ACL"]
_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
        "transfer-encoding", "upgrade", "host", "content-length"}


def forward(method: str, path: str, query: str, headers: dict, body: bytes, client: str, proto: str):
    """Send one request to Radicale and return (status, [(header, value)], body)."""
    url = RADICALE_URL.rstrip("/") + "/" + path + (f"?{query}" if query else "")
    out = {k: v for k, v in headers.items() if k.lower() not in _HOP}
    out["X-Forwarded-For"] = client
    out["X-Forwarded-Proto"] = proto
    req = urllib.request.Request(url, data=body if body or method not in ("GET", "HEAD") else None, method=method, headers=out)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        r = opener.open(req, timeout=60)
    except urllib.error.HTTPError as e:
        r = e
    data = r.read()
    resp_headers = [(k, v) for k, v in r.headers.items() if k.lower() not in _HOP]
    return r.status if hasattr(r, "status") else r.code, resp_headers, data


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # follow redirects ourselves so PROPFIND stays PROPFIND


def check_public(username: str, password: str) -> dict:
    """Do what a phone does: PROPFIND <public url>/.well-known/caldav with the user's device login.

    Returns {"ok": bool, "problem": str|None, "detail": str} with a human explanation.
    """
    if not RADICALE_PUBLIC_URL:
        return {"ok": False, "problem": "no_public_url",
                "detail": "RADICALE_PUBLIC_URL isn't set, so Probyte doesn't know the address phones should use."}
    url = urljoin(RADICALE_PUBLIC_URL.rstrip("/") + "/", ".well-known/caldav")
    auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(5):
        req = urllib.request.Request(url, method="PROPFIND", data=b"",
                                     headers={"Depth": "0", "Authorization": auth, "User-Agent": "Probyte-check"})
        try:
            with opener.open(req, timeout=10) as r:
                status, headers = r.status, r.headers
        except urllib.error.HTTPError as e:
            status, headers = e.code, e.headers
        except Exception as e:
            return {"ok": False, "problem": "unreachable",
                    "detail": f"The server couldn't reach {RADICALE_PUBLIC_URL} ({e}). Check the address and your "
                              "reverse proxy. (Some setups can't reach their own public address from inside; "
                              "if your phone can open it in a browser, try adding the account anyway.)"}
        if status in (301, 302, 303, 307, 308) and headers.get("Location"):
            url = urljoin(url, headers["Location"])
            continue
        break
    challenge = headers.get("WWW-Authenticate", "")
    if status == 207:
        return {"ok": True, "problem": None,
                "detail": "Calendar server reachable and the login works. Your phone should be able to connect."}
    if status == 401 and "radicale" in challenge.lower():
        return {"ok": False, "problem": "bad_credentials",
                "detail": "The calendar server rejected this username/password. Generate a new device password and try again."}
    if status == 405 and "uvicorn" in headers.get("Server", "").lower():
        return {"ok": False, "problem": "wrong_target",
                "detail": "That address reaches an old Probyte version that can't pass calendar requests on. Restart Probyte."}
    if status in (401, 403, 302):
        return {"ok": False, "problem": "proxy_auth",
                "detail": f"Something in front of the calendar server answered {status} before it got the request, most "
                          "likely single sign-on on your reverse proxy (Pangolin, Authelia, Authentik, Cloudflare Access…). "
                          "Phones can only send a username and password, so turn off proxy authentication for this "
                          "address. The calendar server does its own login."}
    return {"ok": False, "problem": "unexpected",
            "detail": f"{url} answered HTTP {status}. Make sure the address points at the bundled Radicale (port 5232)."}


def sync_on_startup() -> None:
    """Keep the htpasswd file and the built-in accounts' URL in line with the database."""
    if not enabled():
        return
    try:
        with connection() as conn:
            conn.execute("UPDATE caldav_accounts SET url = %s WHERE is_builtin AND url <> %s", (RADICALE_URL, RADICALE_URL))
            write_htpasswd(conn)
    except Exception:
        log.exception("could not write Radicale htpasswd file")
