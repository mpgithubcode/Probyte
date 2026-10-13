"""The Document Server through Probyte, at /office-ds/ (pages, files and the editor's WebSocket), so browsers reach it
wherever they reach Probyte, over HTTPS, with the Document Server only on the local or Docker network.

It's a reverse proxy, and does only what one in front of the Document Server would: say where browsers see it
(X-Forwarded-Host with the prefix, X-Forwarded-Proto as http/https), keep Probyte's cookies and the services Probyte
calls itself away from it, and point its redirects back through the prefix."""
import asyncio
import logging
import re
import ssl
from urllib.parse import urlparse

import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.responses import Response
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from app.core.db import connection
from app.identity.oauth import PUBLIC_URL, public_base
from app.office.config import PREFIX, settings

log = logging.getLogger("probyte.office.relay")
router = APIRouter()

_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding",
        "upgrade", "host", "content-length"}
# what browsers never need (Probyte talks to these directly), and the Document Server's own pages
_NOT_RELAYED = re.compile(r"^(coauthoring/commandservice\.ashx|command|convertservice\.ashx|converter|info|example|welcome)(/|$)", re.I)
_clients: dict[bool, httpx.AsyncClient] = {}   # one per "check the certificate" setting


def target(path: str) -> tuple[str, dict]:
    with connection() as conn:
        cfg = settings(conn)
    if not cfg["url"] or not cfg["via_probyte"] or _NOT_RELAYED.match(path.lstrip("/")) or ".." in path.split("/"):
        raise HTTPException(404, "Not found")
    return (cfg["server_url"] or cfg["url"]).rstrip("/") + "/" + path.lstrip("/"), cfg


def browser_base(conn) -> str:
    """Where the browser sees Probyte: PUBLIC_URL when it's set; else the page's own origin, which the browser states
    itself (Origin, sent with every WebSocket and the editor's requests, or Referer); else what the reverse proxy
    forwarded. A proxy can call the editor's WebSocket plain http behind HTTPS: the Document Server then gives the
    browser http:// addresses for the document, which an https page refuses to load ("Download failed")."""
    if PUBLIC_URL:
        return PUBLIC_URL
    for value in (conn.headers.get("origin"), conn.headers.get("referer")):
        u = urlparse(value or "")
        if u.scheme in ("http", "https") and u.netloc:
            return f"{u.scheme}://{u.netloc}"
    return public_base(conn)


def forwarded_headers(headers, request_base: str, scheme: str = "") -> dict:
    """The browser's request headers, plus where it sees the Document Server (so the addresses it makes point back
    through Probyte, as an nginx in front of it would say)."""
    u = urlparse(request_base)
    out = {k: v for k, v in headers.items() if k.lower() not in _HOP and k.lower() not in ("cookie", "authorization")}
    out["X-Forwarded-Host"] = u.netloc + PREFIX
    # the web scheme, also for the editor's WebSocket (ws / wss): the Document Server writes the document's download
    # address with it, and a wss:// one can't be downloaded ("Download failed")
    # (the admin can set it: Settings → Document editing → network settings, when the proxy in front reports it wrongly)
    out["X-Forwarded-Proto"] = scheme or {"ws": "http", "wss": "https"}.get(u.scheme, u.scheme)
    return out


# Document Server 9 installs a service worker over its files; behind the relay a stale one (its cache from an earlier
# version or a failed load) keeps the editor from ever loading: "a glimpse, then nothing". This one takes its place,
# empties its caches and removes itself. It reloads the editor only when there was such a cache: a reload of an editor
# that loaded fine reconnects to its document as a second visit, which the Document Server can't serve ("Download
# failed").
_SW_RESET = """
self.addEventListener('install', function () { self.skipWaiting(); });
self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.map(function (k) { return caches.delete(k); }))
      .then(function () { return self.registration.unregister(); })
      .then(function () { return keys.length ? self.clients.matchAll({ type: 'window' }) : []; })
      .then(function (clients) { clients.forEach(function (c) { c.navigate(c.url); }); });
  }));
});
"""


@router.api_route(PREFIX + "/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS"], include_in_schema=False)
async def relay(path: str, request: Request):
    if path.endswith("document_editor_service_worker.js"):
        return Response(_SW_RESET, media_type="application/javascript", headers={"Cache-Control": "no-store"})
    url, cfg = await run_in_threadpool(target, path)
    if request.url.query:
        url += "?" + request.url.query
    client = _clients.get(cfg["verify_tls"]) or _clients.setdefault(
        cfg["verify_tls"], httpx.AsyncClient(timeout=httpx.Timeout(30, read=600), follow_redirects=False, verify=cfg["verify_tls"]))
    body = await request.body() if request.method in ("POST", "PUT") else None  # (with its length: not every server takes chunks)
    try:
        up = await client.send(client.build_request(request.method, url, content=body,
                                                    headers=forwarded_headers(request.headers, browser_base(request), cfg["browser_scheme"])),
                               stream=True)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Probyte can't reach the document server ({e.__class__.__name__})")
    headers = {k: v for k, v in up.headers.items() if k.lower() not in _HOP and k.lower() != "set-cookie"}
    if "location" in headers:  # a redirect to its own address: through the relay instead
        loc = urlparse(headers["location"])
        if loc.netloc:
            headers["location"] = browser_base(request).rstrip("/") + PREFIX + loc.path + (f"?{loc.query}" if loc.query else "")
        elif loc.path.startswith("/") and not loc.path.startswith(PREFIX):
            headers["location"] = PREFIX + headers["location"]
    return StreamingResponse(up.aiter_raw(), status_code=up.status_code, headers=headers, background=BackgroundTask(up.aclose))


@router.websocket(PREFIX + "/{path:path}")
async def relay_ws(ws: WebSocket, path: str):
    """The editor's live connection (co-editing, saving) to the Document Server."""
    try:
        url, cfg = await run_in_threadpool(target, path)
    except HTTPException:
        await ws.close(code=1008)
        return
    url = re.sub(r"^http", "ws", url) + (f"?{ws.url.query}" if ws.url.query else "")
    headers = forwarded_headers({k: v for k, v in ws.headers.items() if k.lower() not in (
        "sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions", "sec-websocket-protocol")},
        browser_base(ws), cfg["browser_scheme"])
    log.info("editor connection: the browser is at %s (the proxy said %s://%s); the Document Server is told %s://%s",
             browser_base(ws), ws.headers.get("x-forwarded-proto", "-"), ws.headers.get("x-forwarded-host") or ws.headers.get("host", "-"),
             headers.get("X-Forwarded-Proto"), headers.get("X-Forwarded-Host"))
    tls = None
    if url.startswith("wss:") and not cfg["verify_tls"]:  # an HTTPS Document Server with a self-signed certificate
        tls = ssl.create_default_context()
        tls.check_hostname = False
        tls.verify_mode = ssl.CERT_NONE
    try:
        up = await websockets.connect(url, extra_headers=headers, subprotocols=ws.scope.get("subprotocols") or None, ssl=tls,
                                      max_size=None, open_timeout=20, ping_interval=None)
    except Exception as e:  # noqa: BLE001
        log.warning("relay: can't open the document server's WebSocket: %s", e)
        await ws.close(code=1011)
        return
    await ws.accept(subprotocol=up.subprotocol)

    async def to_server():
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                break
            await up.send(m["text"] if m.get("text") is not None else m.get("bytes") or b"")

    async def to_browser():
        async for m in up:
            await (ws.send_text(m) if isinstance(m, str) else ws.send_bytes(m))

    tasks = [asyncio.create_task(to_server()), asyncio.create_task(to_browser())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await up.close()
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
