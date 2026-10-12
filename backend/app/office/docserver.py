"""Probyte as a client of the Document Server: its command and conversion services (signed requests), and
downloading the versions it saves."""
from urllib.parse import urlparse

import requests
from fastapi import HTTPException

from app.office.config import PREFIX
from app.office.jwt import sign


def post(cfg: dict, paths: tuple, payload: dict) -> dict:
    """A signed request to one of the Document Server's services (its older address first, then the newer one)."""
    base = (cfg["server_url"] or cfg["url"]).rstrip("/")
    body = dict(payload)
    headers = {"Accept": "application/json"}
    if cfg["secret"]:
        body["token"] = sign(payload, cfg["secret"])
        headers["Authorization"] = "Bearer " + sign({"payload": payload}, cfg["secret"])
    last = None
    for path in paths:
        try:
            r = requests.post(base + path, json=body, headers=headers, timeout=120, verify=cfg["verify_tls"])
        except requests.RequestException as e:
            raise HTTPException(502, f"The document server isn't reachable from Probyte ({e.__class__.__name__})")
        if r.status_code == 404:
            last = r
            continue
        try:
            return r.json()
        except ValueError:
            raise HTTPException(502, f"The document server answered HTTP {r.status_code}")
    raise HTTPException(502, f"The document server has no {paths[0].strip('/')} service (HTTP {last.status_code if last else '?'})")


def command(cfg: dict, payload: dict) -> dict:
    """A command to the Document Server's command service (forcesave, version…)."""
    return post(cfg, ("/coauthoring/CommandService.ashx", "/command"), payload)


def convert(cfg: dict, payload: dict) -> dict:
    return post(cfg, ("/ConvertService.ashx", "/converter"), payload)


def fetch(cfg: dict, url: str) -> bytes:
    """A saved (or converted) version from the Document Server (only from its own addresses)."""
    allowed = {urlparse(u).netloc for u in (cfg["url"], cfg["server_url"]) if u}
    u = urlparse(url)
    if u.scheme not in ("http", "https") or u.netloc not in allowed:
        # the Document Server may report its own address as it knows it: fetch the same path from the configured one
        base = (cfg["server_url"] or cfg["url"]).rstrip("/")
        path = u.path[len(PREFIX):] if u.path.startswith(PREFIX + "/") else u.path  # (reported through Probyte's relay)
        url = base + path + (f"?{u.query}" if u.query else "")
    r = requests.get(url, timeout=(10, 600), verify=cfg["verify_tls"])
    if r.status_code != 200:
        raise HTTPException(502, f"Couldn't get the saved document (HTTP {r.status_code})")
    return r.content


def healthcheck(cfg: dict) -> None:
    """Raises HTTPException(400) with what's wrong when Probyte can't use the Document Server."""
    base = (cfg["server_url"] or cfg["url"]).rstrip("/")
    try:
        h = requests.get(base + "/healthcheck", timeout=10, verify=cfg["verify_tls"])
    except requests.exceptions.SSLError:
        raise HTTPException(400, f"{base}'s certificate isn't trusted (self-signed?): turn off “Check the document server's certificate” "
                                 "in the network settings, or give it a trusted certificate")
    except requests.RequestException as e:
        raise HTTPException(400, f"Probyte can't reach {base} ({e.__class__.__name__})")
    if h.status_code != 200 or "true" not in h.text:
        raise HTTPException(400, f"{base} doesn't look like an ONLYOFFICE Document Server (healthcheck: HTTP {h.status_code})")
