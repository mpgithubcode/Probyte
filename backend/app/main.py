"""The web program: startup, middleware, and the HTTP faces of the components (the app's own API in web/, sign-in
and OAuth, public links, REST v1, MCP, feeds, Probyte's CalDAV/CardDAV server) plus the built frontend.

    uvicorn app.main:app"""
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response as RawResponse
from fastapi.staticfiles import StaticFiles

from app import wiring  # noqa: F401  (connects the components: before anything writes)
from app import extensions, office
from app.adapters import feeds, live, mcp_server, rest_v1
from app.core.db import connection, init_db
from app.data import contacts, store
from app.dav import api as dav_api, server as dav_server
from app.files import catalog as files
from app.identity import oauth, routes as auth_routes
from app.sharing import links as shares
from app.web import admin, records, tables, templates, views, workspace

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with connection() as conn:
        store.ensure_system_fields(conn)
    stop = threading.Event()
    if os.environ.get("RUN_WORKER", "").lower() == "embedded":  # single-container setups: run the worker in here too
        from app import worker
        from app.core.db import pool
        pool.resize(pool.min_size, pool.max_size + worker.THREADS * 2 + 4)  # room for the worker's syncs next to web requests
        worker.start(stop)
    yield
    stop.set()


app = FastAPI(title="Probyte", lifespan=lifespan)

# Endpoints used with bearer tokens (never cookies) can be called from any origin: browser-based MCP clients,
# OAuth flows in web apps, and API calls from web tools.
CORS_PREFIXES = ("/api/v1/", "/mcp", "/oauth/token", "/oauth/register", "/.well-known/oauth", "/.well-known/openid")
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, X-API-Key, Mcp-Session-Id, Mcp-Protocol-Version, Last-Event-ID",
    "Access-Control-Expose-Headers": "Mcp-Session-Id, WWW-Authenticate",
    "Access-Control-Max-Age": "86400",
}


class CompressAPI:
    """gzip for the JSON API (table payloads shrink ~10x); never for streams (MCP, live updates) or DAV."""

    def __init__(self, app):
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=2048, compresslevel=5)

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if path.startswith("/api/") and not path.endswith("/events"):
            return await self.gzip(scope, receive, send)
        return await self.app(scope, receive, send)


app.add_middleware(CompressAPI)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    try:
        response = await call_next(request)
    except RuntimeError as e:  # the browser went away before the answer (a closed editor, a cancelled download)
        if str(e) == "No response returned." and await request.is_disconnected():
            return RawResponse(status_code=499)
        raise
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # The app and its sign-in / consent pages can't be framed by other sites (clickjacking); public links (/s/…)
    # are meant to be embedded, so they can.
    if h.get("content-type", "").startswith("text/html") and not request.url.path.startswith("/s/"):
        h.setdefault("X-Frame-Options", "SAMEORIGIN")
        h.setdefault("Content-Security-Policy", "frame-ancestors 'self'")
    return response


@app.middleware("http")
async def cors(request: Request, call_next):
    if not request.url.path.startswith(CORS_PREFIXES):
        return await call_next(request)
    if request.method == "OPTIONS":
        return RawResponse(status_code=204, headers=CORS_HEADERS)
    response = await call_next(request)
    for k, v in CORS_HEADERS.items():
        response.headers.setdefault(k, v)
    return response



@app.exception_handler(psycopg.errors.InvalidTextRepresentation)
def _bad_id(request: Request, exc):
    # Malformed UUIDs in the URL: treat like any other missing object.
    return JSONResponse({"detail": "Not found"}, status_code=404)



for r in (auth_routes, dav_api, workspace, tables, records, views, live, admin, feeds, files, templates, contacts, office):
    app.include_router(r.router)

app.include_router(shares.router)
extensions.mount(app)
app.include_router(oauth.router)
app.include_router(mcp_server.router)
app.mount("/api/v1", rest_v1.v1)


# ======================================================================== CalDAV / CardDAV, and the frontend
#
# Phones find the calendar server at /.well-known/caldav (and carddav) and use /dav/… Devices set up when calendars
# lived in the bundled Radicale use the root (/<username>/…), so DAV methods anywhere else reach it too. The web app
# only uses "/", "/assets/*" and "/api/*" (navigation is in the #fragment).

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


async def _dav(request: Request, prefix: str, path: str):
    body = await request.body()
    status, headers, data = await run_in_threadpool(
        dav_server.handle, request.method, prefix, path, {k.lower(): v for k, v in request.headers.items()}, body,
        request.client.host if request.client else "")
    return RawResponse(content=data, status_code=status, headers=headers)


@app.api_route("/.well-known/{service}", methods=dav_server.METHODS, include_in_schema=False)
async def well_known(service: str, request: Request):
    if service not in ("caldav", "carddav"):
        raise HTTPException(404)
    return RawResponse(status_code=301, headers={"Location": "/dav/"})


@app.api_route("/dav", methods=dav_server.METHODS, include_in_schema=False)
@app.api_route("/dav/{path:path}", methods=dav_server.METHODS, include_in_schema=False)
async def dav(request: Request, path: str = ""):
    return await _dav(request, "/dav", path)


@app.api_route("/{path:path}", methods=dav_server.METHODS, include_in_schema=False)
async def frontend_or_dav(path: str, request: Request):
    if path.startswith("api/"):
        raise HTTPException(404)
    if request.method in ("GET", "HEAD") and STATIC_DIR.exists():
        f = (STATIC_DIR / path).resolve()
        if path in ("", "index.html"):
            return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
        if f.is_file() and f.is_relative_to(STATIC_DIR):  # sw.js and other files from frontend/public
            return FileResponse(f, headers={"Cache-Control": "no-cache"} if path == "sw.js" else None)
        if "authorization" not in request.headers and "text/html" in request.headers.get("accept", ""):
            return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})  # a browser, not a device
    return await _dav(request, "", path)
