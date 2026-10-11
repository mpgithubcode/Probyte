"""The FastAPI app: startup, middleware, the API routers (routes/, shares, OAuth, MCP, /api/v1), the built
frontend, and the pass-through to the bundled Radicale."""
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

from . import builtin_dav, mcp_server, oauth, rest_v1, shares, store
from .routes import auth as auth_routes, records, tables, views, workspace
from .db import connection, init_db

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with connection() as conn:
        store.ensure_system_fields(conn)
    builtin_dav.sync_on_startup()
    stop = threading.Event()
    if os.environ.get("RUN_WORKER", "").lower() == "embedded":  # single-container setups: run the worker in here too
        from . import worker
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



for r in (auth_routes, workspace, tables, records, views):
    app.include_router(r.router)

app.include_router(shares.router)
app.include_router(oauth.router)
app.include_router(mcp_server.router)
app.mount("/api/v1", rest_v1.v1)


# ======================================================================== frontend + CalDAV pass-through
#
# The web app only uses "/", "/assets/*" and "/api/*" (navigation is in the #fragment). Everything
# else, and every non-GET method such as PROPFIND / REPORT, goes to the bundled Radicale, so a
# phone can be pointed at the same address as the web app.

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@app.api_route("/{path:path}", methods=builtin_dav.DAV_METHODS, include_in_schema=False)
async def frontend_or_dav(path: str, request: Request):
    if path.startswith("api/"):
        raise HTTPException(404)
    if request.method in ("GET", "HEAD") and STATIC_DIR.exists():
        f = (STATIC_DIR / path).resolve()
        if path in ("", "index.html"):
            return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
        if f.is_file() and f.is_relative_to(STATIC_DIR):  # sw.js and other files from frontend/public
            return FileResponse(f, headers={"Cache-Control": "no-cache"} if path == "sw.js" else None)
    if not builtin_dav.enabled():
        if not STATIC_DIR.exists() or request.method not in ("GET", "HEAD"):
            raise HTTPException(404)
        return FileResponse(STATIC_DIR / "index.html")
    body = await request.body()
    status, headers, data = await run_in_threadpool(
        builtin_dav.forward, request.method, path, request.url.query, dict(request.headers), body,
        request.client.host if request.client else "", request.url.scheme,
    )
    resp = RawResponse(content=data, status_code=status)
    for k, v in headers:
        resp.headers.append(k, v)
    return resp
