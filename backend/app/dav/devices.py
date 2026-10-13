"""Device logins for Probyte's own CalDAV/CardDAV server (dav/server.py).

Phones and desktop calendar apps can't do Probyte's sign-in, so each user has a generated "device password" for
them, with their Probyte username. Its bcrypt hash is users.dav_hash; the password itself is kept (encrypted) on the
user's built-in account (caldav_accounts.is_builtin) so Settings can show it again. Checking a bcrypt hash takes
tens of milliseconds and a phone sends dozens of requests per sync, so a login that worked is remembered for a few
minutes (keyed by a hash of the password and the stored hash: a new device password ends it at once).
"""
import base64
import hashlib
import re
import secrets
import string
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin

import bcrypt

from app.core.db import connection
from app.core.secretbox import seal, unseal

ACCOUNT_NAME = "Probyte"
BUILTIN_URL = "/dav/"      # the built-in account's address (Probyte's own server; tables on it need no sync)
REALM = "Probyte calendars and contacts"
CACHE_SECONDS = 300

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def generate_password() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "-".join("".join(secrets.choice(alphabet) for _ in range(5)) for _ in range(4))


def provision(conn, user_id) -> str:
    """Create or replace a user's device password. Returns the new password."""
    user = conn.execute("SELECT id, username FROM users WHERE id = %s", (user_id,)).fetchone()
    password = generate_password()
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode()
    conn.execute("UPDATE users SET dav_hash = %s WHERE id = %s", (hashed, user_id))
    existing = conn.execute("SELECT id FROM caldav_accounts WHERE user_id = %s AND is_builtin", (user_id,)).fetchone()
    if existing:
        conn.execute("UPDATE caldav_accounts SET url = %s, username = %s, password = %s WHERE id = %s",
                     (BUILTIN_URL, user["username"], seal(password), existing["id"]))
    else:
        conn.execute(
            "INSERT INTO caldav_accounts (user_id, name, url, username, password, is_builtin) VALUES (%s,%s,%s,%s,%s,true)",
            (user_id, ACCOUNT_NAME, BUILTIN_URL, user["username"], seal(password)),
        )
    return password


def ensure(conn, user_id) -> None:
    """Every user has a device login (accounts made before there was one get it when they first need it)."""
    if not conn.execute("SELECT 1 FROM caldav_accounts WHERE user_id = %s AND is_builtin", (user_id,)).fetchone():
        provision(conn, user_id)


def builtin_account_id(conn, user_id):
    ensure(conn, user_id)
    return conn.execute("SELECT id FROM caldav_accounts WHERE user_id = %s AND is_builtin", (user_id,)).fetchone()["id"]


def is_builtin_account(conn, account_id) -> bool:
    return bool(account_id and conn.execute("SELECT 1 FROM caldav_accounts WHERE id = %s AND is_builtin", (account_id,)).fetchone())


def authenticate(username: str, password: str) -> dict | None:
    """The user with this device login, or None."""
    username = (username or "").strip().lower()
    if not username or not password:
        return None
    with connection() as conn:
        u = conn.execute("SELECT id, username, is_admin, dav_hash FROM users WHERE username = %s", (username,)).fetchone()
    if not u or not u["dav_hash"]:
        return None
    key = hashlib.sha256(f"{u['dav_hash']}\0{password}".encode()).hexdigest()
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    if not bcrypt.checkpw(password.encode(), u["dav_hash"].encode()):
        return None
    user = {"id": u["id"], "username": u["username"], "is_admin": u["is_admin"]}
    with _lock:
        if len(_cache) > 10000:
            _cache.clear()
        _cache[key] = (now + CACHE_SECONDS, user)
    return user


def info(conn, user_id, own_url: str) -> dict:
    """What a user enters on a device. own_url: Probyte's public address (the server answers at /dav/ and, for
    devices set up with the bare address, at /.well-known/caldav and the root)."""
    ensure(conn, user_id)
    acc = conn.execute("SELECT username, password FROM caldav_accounts WHERE user_id = %s AND is_builtin", (user_id,)).fetchone()
    base = own_url.rstrip("/") + "/"
    return {
        "enabled": True,
        "provisioned": True,
        "public_url": base,
        "dav_url": base + "dav/",
        "username": acc["username"],
        "password": unseal(acc["password"]),
    }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # follow redirects ourselves so PROPFIND stays PROPFIND


def check_public(username: str, password: str, public: str) -> dict:
    """Do what a phone does: PROPFIND <public address>/.well-known/caldav with the device login.

    Returns {"ok": bool, "problem": str|None, "detail": str} with a human explanation.
    """
    url = urljoin(public.rstrip("/") + "/", ".well-known/caldav")
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
                    "detail": f"The server couldn't reach {public} ({e}). Check the address and your "
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
    if status == 401 and REALM.lower() in challenge.lower():
        return {"ok": False, "problem": "bad_credentials",
                "detail": "The calendar server rejected this username/password. Generate a new device password and try again."}
    if status in (401, 403, 302):
        return {"ok": False, "problem": "proxy_auth",
                "detail": f"Something in front of Probyte answered {status} before it got the request, most "
                          "likely single sign-on on your reverse proxy (Pangolin, Authelia, Authentik, Cloudflare Access…). "
                          "Phones can only send a username and password, so turn off proxy authentication for "
                          "/.well-known/ and /dav/ (Probyte does its own login there)."}
    return {"ok": False, "problem": "unexpected",
            "detail": f"{url} answered HTTP {status}. Make sure the address reaches Probyte."}


# ---------------------------------------------------------------- connected devices

SEEN_EVERY = 30  # seconds: how often a device's "last synced" is written while it keeps talking (a sync is dozens of requests)
_seen: dict[tuple, list] = {}  # (user id, agent) -> [last written (monotonic), requests not yet counted]


def label(agent: str) -> str:
    """What a device is, from its app's User-Agent: "iPhone / iPad · Calendar (iOS 18.0)", "Mac · Contacts"…"""
    a = agent or ""
    low = a.lower()
    app = ("Reminders" if "remind" in low else "Contacts" if ("addressbook" in low or "contacts" in low or "accountsd" in low)
           else "Calendar" if ("dataaccessd" in low or "calendar" in low or "ical" in low) else None)
    m = re.search(r"\b(iOS|iPadOS|macOS|Mac OS X)/([\d.]+)", a)
    if m:
        device = "Mac" if m.group(1) in ("macOS", "Mac OS X") else "iPhone / iPad"
        os_name = "macOS" if device == "Mac" else m.group(1)
        return f"{device}{' · ' + app if app else ''} ({os_name} {m.group(2)})"
    if "davx5" in low or "davdroid" in low:
        av = re.search(r"Android/([\d.]+)", a)
        return "Android · DAVx⁵" + (f" (Android {av.group(1)})" if av else "")
    for needle, name in (("thunderbird", "Thunderbird"), ("evolution", "Evolution"), ("tasks.org", "Tasks.org"),
                         ("jtx", "jtx Board"), ("caldav", "CalDAV app"), ("probyte", "Probyte")):
        if needle in low:
            return name
    return (a.split("/")[0].split(" ")[0] or "Unknown app")[:60]


def seen(user_id, agent: str, address: str, wrote: bool) -> None:
    """A device made a request: noted (at most every SEEN_EVERY seconds, at once when it changed something)."""
    agent = (agent or "unknown")[:400]
    key = (str(user_id), agent)
    now = time.monotonic()
    with _lock:
        entry = _seen.setdefault(key, [0.0, 0])
        entry[1] += 1
        if not wrote and now - entry[0] < SEEN_EVERY:
            return
        count, entry[0], entry[1] = entry[1], now, 0
        if len(_seen) > 10000:
            _seen.clear()
    try:
        with connection() as conn:
            conn.execute(
                "INSERT INTO dav_devices (user_id, agent, label, last_address, requests, last_change) VALUES (%s,%s,%s,%s,%s,"
                "CASE WHEN %s THEN now() END) ON CONFLICT (user_id, agent) DO UPDATE SET last_seen = now(), "
                "last_address = EXCLUDED.last_address, requests = dav_devices.requests + EXCLUDED.requests, "
                "last_change = COALESCE(EXCLUDED.last_change, dav_devices.last_change)",
                (user_id, agent, label(agent), address or None, count, wrote))
    except Exception:  # noting a device never breaks its request
        pass


def list_devices(conn, user_id) -> list[dict]:
    return conn.execute("SELECT id, label, agent, first_seen, last_seen, last_change, last_address, requests FROM dav_devices "
                        "WHERE user_id = %s ORDER BY last_seen DESC", (user_id,)).fetchall()


def forget(conn, user_id, device_id) -> None:
    conn.execute("DELETE FROM dav_devices WHERE id = %s AND user_id = %s", (device_id, user_id))
    with _lock:
        for k in [k for k in _seen if k[0] == str(user_id)]:
            _seen.pop(k, None)
