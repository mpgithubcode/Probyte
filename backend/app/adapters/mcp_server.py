"""MCP (Model Context Protocol) server, so AI assistants can work with Probyte data.

Transports:
  * Streamable HTTP  POST /mcp            (Claude, ChatGPT, Cursor, VS Code, Claude Code, n8n, LibreChat, …)
  * HTTP + SSE       GET /mcp/sse + POST /mcp/messages   (older clients)
Authentication: an API key (Authorization: Bearer / X-API-Key / ?key=), or OAuth 2.1 (see oauth.py), whose
tokens are API keys too. A read-only key only gets the read tools.
"""
import asyncio
import hmac
import json
import logging
import os
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.adapters import apiv1
from app.data import audit
from app.identity import auth
from app.core.db import connection

log = logging.getLogger("probyte.mcp")
router = APIRouter()

PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
SERVER_INFO = {"name": "probyte", "title": "Probyte", "version": "1.0.0"}
INSTRUCTIONS = (
    "Probyte is a database app: bases contain tables (some synced with CalDAV calendars/tasks or CardDAV contacts) "
    "and combined databases (read-only views over several tables). Start with list_bases, then describe_table to "
    "learn a table's fields before reading or writing. Tables can be named by id, by name, or as 'Base / Table'. "
    "Records use field names as keys; write values as plain text (dates like 2026-10-12 or 2026-10-12T15:30:00Z, "
    "select option names, linked record titles, usernames). Calculated fields can't be written. "
    "With the Blueprint takeoff extension on, takeoff_* tools count and measure drawing sets (PDFs in files tables), "
    "in this order: takeoff_create_project, takeoff_update_sheets (choose the sheets), takeoff_read_legends, "
    "takeoff_add_items, takeoff_find, takeoff_list_marks + takeoff_review (finds are suggestions until accepted), "
    "takeoff_summary, takeoff_export_table."
)

TABLE = {"type": "string", "description": "Table id, table name, or 'Base / Table'"}
BASE = {"type": "string", "description": "Base id or name, when table names repeat across bases"}
FIELD_VALUES = {"type": "object", "description": "Field name → value, e.g. {\"Name\": \"Ada\", \"Status\": \"Done\", \"Due\": \"2026-10-12\"}",
                "additionalProperties": True}
FILTERS = {"type": "array", "description": "Conditions that must all hold",
           "items": {"type": "object", "properties": {
               "field": {"type": "string"},
               "op": {"type": "string", "enum": ["is", "is_not", "contains", "not_contains", "gt", "gte", "lt", "lte", "empty", "not_empty"]},
               "value": {"description": "Not needed for empty / not_empty. Dates as 2026-10-12."}},
               "required": ["field", "op"]}}


def _tool(name, title, description, props, required=(), read_only=False, destructive=False, idempotent=False):
    return {"name": name, "title": title, "description": description,
            "inputSchema": {"type": "object", "properties": props, "required": list(required), "additionalProperties": False},
            "annotations": {"title": title, "readOnlyHint": read_only, "destructiveHint": destructive,
                            "idempotentHint": idempotent or read_only, "openWorldHint": False}}


FIELD_KEY = {"type": "string", "enum": ["name", "id"], "default": "name",
             "description": "Key fields in results by name (default) or by field id; field names and ids are both accepted in input"}

TOOLS = [
    _tool("list_bases", "List bases and tables", "All bases you can access, with their tables and combined databases.", {}, read_only=True),
    _tool("describe_table", "Describe a table", "A table's fields (name, type, select options, link targets, which are read-only) and views.",
          {"table": TABLE, "base": BASE}, ["table"], read_only=True),
    _tool("get_json_schema", "Get a table's JSON Schema",
          "A JSON Schema (draft 2020-12) of a table's records: each field's JSON type, format, options, and which are required or read-only.",
          {"table": TABLE, "base": BASE, "field_key": FIELD_KEY}, ["table"], read_only=True),
    _tool("list_records", "List records",
          "Records of a table or combined database, as field name → value (values as shown in Probyte). "
          "Supports filters, text search, sorting and paging; returns total and next_offset.",
          {"field_key": FIELD_KEY, "table": TABLE, "base": BASE, "view": {"type": "string", "description": "Apply a view's filters and sort (id or name)"},
           "filters": FILTERS, "search": {"type": "string", "description": "Text to find in any field"},
           "sort": {"type": "array", "items": {"type": "object", "properties": {"field": {"type": "string"},
                    "direction": {"type": "string", "enum": ["asc", "desc"]}}, "required": ["field"]}},
           "fields": {"type": "array", "items": {"type": "string"}, "description": "Only return these fields"},
           "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 25},
           "offset": {"type": "integer", "minimum": 0, "default": 0}}, ["table"], read_only=True),
    _tool("get_record", "Get a record", "One record by id, with all its fields.", {"field_key": FIELD_KEY, "record_id": {"type": "string"}}, ["record_id"], read_only=True),
    _tool("search", "Search everything", "Find records containing some text in any field of any table.",
          {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}}, ["query"], read_only=True),
    _tool("create_records", "Create records", "Add records to a table (up to 200 per call; all-or-nothing).",
          {"field_key": FIELD_KEY, "table": TABLE, "base": BASE, "records": {"type": "array", "items": FIELD_VALUES, "minItems": 1, "maxItems": 200}},
          ["table", "records"]),
    _tool("update_records", "Update records", "Change some fields of existing records; fields you leave out stay as they are.",
          {"field_key": FIELD_KEY, "table": TABLE, "base": BASE, "records": {"type": "array", "minItems": 1, "maxItems": 200, "items": {
              "type": "object", "properties": {"id": {"type": "string"}, "fields": FIELD_VALUES}, "required": ["id", "fields"]}}},
          ["table", "records"], idempotent=True),
    _tool("upsert_records", "Create or update records",
          "For each record: update the one whose match_on fields have the same values, or create it.",
          {"field_key": FIELD_KEY, "table": TABLE, "base": BASE, "records": {"type": "array", "items": FIELD_VALUES, "minItems": 1, "maxItems": 200},
           "match_on": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "Field names that identify a record, e.g. [\"Email\"]"}},
          ["table", "records", "match_on"], idempotent=True),
    _tool("delete_records", "Delete records", "Delete records by id (synced items are also removed from the calendar / address book).",
          {"table": TABLE, "base": BASE, "ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 200}},
          ["table", "ids"], destructive=True, idempotent=True),
    _tool("create_table", "Create a table", "Create a table. The first field is the title field.",
          {"name": {"type": "string"}, "base": BASE, "fields": {"type": "array", "items": {"type": "object", "properties": {
              "name": {"type": "string"},
              "type": {"type": "string", "description": "text, long_text, number, checkbox, single_select, multi_select, date, url, email, rating, link, user, recurrence, formula"},
              "options": {"type": "object", "description": "e.g. {\"choices\": [\"Todo\", \"Done\"]}, {\"include_time\": true}, {\"linked_table\": \"Projects\"}, {\"expression\": \"{Price} * 2\"}"}},
              "required": ["name"]}}}, ["name"]),
    _tool("add_field", "Add a field", "Add a field to a table.",
          {"table": TABLE, "base": BASE, "name": {"type": "string"},
           "type": {"type": "string", "description": "text, long_text, number, checkbox, single_select, multi_select, date, url, email, rating, link, user, recurrence, formula"},
           "options": {"type": "object"}}, ["table", "name", "type"]),
    _tool("sync_table", "Sync with calendar", "Sync a CalDAV / CardDAV table with its server now.", {"table": TABLE, "base": BASE}, ["table"], idempotent=True),
]


def _call(user: dict, name: str, a: dict):
    """Run a tool (in a worker thread). Returns a JSON-able result."""
    def run(fn, *args, **kw):
        with connection() as conn:
            out = fn(conn, user, *args, **kw)
        return out[0] if isinstance(out, tuple) else out

    if name == "list_bases":
        return {"bases": run(apiv1.list_bases)}
    if name == "describe_table":
        return run(apiv1.table_schema, a["table"], a.get("base"))
    fk = "id" if a.get("field_key") == "id" else "name"  # key fields by name (default) or by id, as in Teable
    if name == "get_json_schema":
        return run(apiv1.json_schema, a["table"], a.get("base"), fk)
    if name == "list_records":
        return run(apiv1.query_records, a["table"], base=a.get("base"), view=a.get("view"), filters=a.get("filters"),
                   search=a.get("search"), sort=a.get("sort"), fields=a.get("fields"), limit=min(int(a.get("limit") or 25), 200),
                   offset=a.get("offset") or 0, cell_format="text", field_key=fk)
    if name == "get_record":
        return run(apiv1.get_record, a["record_id"], "text", fk)
    if name == "search":
        return {"results": run(apiv1.search_all, a["query"], min(int(a.get("limit") or 20), 100))}
    if name == "create_records":
        return {"created": run(apiv1.create_records, a["table"], a["records"], a.get("base"), "text", fk)}
    if name == "update_records":
        return {"updated": run(apiv1.update_records, a["table"], a["records"], a.get("base"), "text", fk)}
    if name == "upsert_records":
        return run(apiv1.upsert_records, a["table"], a["records"], a["match_on"], a.get("base"), "text", fk)
    if name == "delete_records":
        return {"deleted": run(apiv1.delete_records, a["table"], a["ids"], a.get("base"))}
    if name == "create_table":
        return run(apiv1.create_table, a["name"], a.get("base"), a.get("fields"))
    if name == "add_field":
        return run(apiv1.add_field, a["table"], a["name"], a["type"], a.get("options"), a.get("base"))
    if name == "sync_table":
        return apiv1.sync_table(user, a["table"], a.get("base"))
    raise HTTPException(400, f"Unknown tool {name}")


def _all_tools() -> tuple[list, dict]:
    """Probyte's tools and those of the extensions that are on: (tools, {name: extension})."""
    from app.extensions import tools_on
    ext = tools_on()
    return TOOLS + [t for _, t in ext], {t["name"]: e for e, t in ext}


def _read_only(tool) -> bool:
    return tool["annotations"]["readOnlyHint"]


def _tools_for(user):
    tools, _ = _all_tools()
    return [t for t in tools if user.get("api_scope") != "read" or _read_only(t)]


async def handle(msg, user: dict, bases) -> dict | None:
    """One JSON-RPC message -> response (None for notifications)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if method is None:
        return None  # a response from the client (we never send requests)
    if mid is None:
        return None  # notification (notifications/initialized, cancelled, …)

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    if method == "initialize":
        asked = params.get("protocolVersion")
        return ok({"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                   "capabilities": {"tools": {"listChanged": False}, "resources": {}, "prompts": {}},
                   "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS})
    if method == "ping" or method == "logging/setLevel":
        return ok({})
    if method == "tools/list":
        return ok({"tools": await run_in_threadpool(_tools_for, user)})
    if method in ("resources/list",):
        return ok({"resources": []})
    if method == "resources/templates/list":
        return ok({"resourceTemplates": []})
    if method == "prompts/list":
        return ok({"prompts": []})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        tools, owners = await run_in_threadpool(_all_tools)
        tool = next((t for t in tools if t["name"] == name), None)
        if not tool:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool: {name}"}}
        if user.get("api_scope") == "read" and not _read_only(tool):
            return ok(_error_result("This connection is read-only. Give it read & write access in Probyte (Settings → API & MCP) to make changes."))
        missing = [k for k in tool["inputSchema"]["required"] if k not in args]
        if missing:
            return ok(_error_result(f"Missing argument(s): {', '.join(missing)}"))

        def work():
            auth.api_bases.set(bases)
            if name in owners:  # an extension's tool
                return owners[name].call(user, name, args)
            return _call(user, name, args)
        try:
            result = await run_in_threadpool(work)
        except HTTPException as e:
            return ok(_error_result(str(e.detail)))
        except Exception as e:
            log.exception("MCP tool %s failed", name)
            return ok(_error_result(f"Something went wrong: {e}"))
        text = json.dumps(result, default=str, ensure_ascii=False)
        return ok({"content": [{"type": "text", "text": text}], "structuredContent": json.loads(text), "isError": False})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def _error_result(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


# ---------------------------------------------------------------- authentication

def public_base(request: Request) -> str:
    from app.identity.oauth import public_base as pb
    return pb(request)


def _unauthorized(request: Request, message: str) -> JSONResponse:
    meta = f"{public_base(request)}/.well-known/oauth-protected-resource"
    return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": message}}, status_code=401,
                        headers={"WWW-Authenticate": f'Bearer realm="probyte", resource_metadata="{meta}"'})


# A fixed bearer token from the server's environment, for MCP clients configured with "Bearer token" auth.
STATIC_TOKEN = os.environ.get("MCP_BEARER_TOKEN", "").strip()
STATIC_USER = os.environ.get("MCP_BEARER_USER", "").strip().lower()      # default: the first admin
STATIC_SCOPE = "read" if os.environ.get("MCP_BEARER_SCOPE", "write").strip().lower() == "read" else "write"
if STATIC_TOKEN and len(STATIC_TOKEN) < 16:
    log.warning("MCP_BEARER_TOKEN is shorter than 16 characters; ignoring it")
    STATIC_TOKEN = ""


def _static_user():
    with connection() as conn:
        u = (conn.execute("SELECT id, username, is_admin, radicale_hash FROM users WHERE username = %s", (STATIC_USER,)).fetchone()
             if STATIC_USER else
             conn.execute("SELECT id, username, is_admin, radicale_hash FROM users ORDER BY is_admin DESC, created_at LIMIT 1").fetchone())
    if not u:
        raise HTTPException(401, f"MCP_BEARER_USER '{STATIC_USER}' doesn't exist")
    return dict(u) | {"api_key": True, "api_scope": STATIC_SCOPE, "api_key_id": "env", "actor": f"{u['username']} via the MCP token"}


async def _authenticate(request: Request):
    key = auth.request_key(request) or request.query_params.get("key") or request.query_params.get("api_key")
    if not key:
        return None, None, "Sign in: connect with OAuth, or send a bearer token (Authorization: Bearer …)"
    if STATIC_TOKEN and hmac.compare_digest(key.encode(), STATIC_TOKEN.encode()):
        try:
            user = await run_in_threadpool(_static_user)
        except HTTPException as e:
            return None, None, str(e.detail)
        audit.set_actor(user["actor"], user["id"])
        return user, None, None
    try:
        user, bases = await run_in_threadpool(auth.key_user, key, "/mcp")
    except HTTPException as e:
        return None, None, str(e.detail)
    audit.set_actor(user["actor"], user["id"])
    return user, bases, None


# ---------------------------------------------------------------- Streamable HTTP

@router.post("/mcp")
@router.post("/mcp/")
async def mcp_post(request: Request):
    user, bases, err = await _authenticate(request)
    if err:
        return _unauthorized(request, err)
    try:
        body = json.loads(await request.body() or b"null")
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400)
    batch = isinstance(body, list)
    replies = [r for r in [await handle(m, user, bases) for m in (body if batch else [body])] if r is not None]
    headers = {}
    if any(isinstance(m, dict) and m.get("method") == "initialize" for m in (body if batch else [body])):
        headers["Mcp-Session-Id"] = uuid.uuid4().hex
    if not replies:
        return Response(status_code=202, headers=headers)
    return JSONResponse(replies if batch else replies[0], headers=headers)


@router.get("/mcp")
@router.get("/mcp/")
async def mcp_get(request: Request):
    # No server-initiated stream on this endpoint (allowed by the spec); old SSE clients use /mcp/sse.
    if "text/event-stream" in request.headers.get("accept", "") and not request.headers.get("mcp-protocol-version"):
        user, _, err = await _authenticate(request)
        if err:
            return _unauthorized(request, err)
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("/mcp")
@router.delete("/mcp/")
async def mcp_delete():
    return Response(status_code=204)  # sessions hold no state


# ---------------------------------------------------------------- HTTP + SSE (protocol 2024-11-05)

_sessions: dict[str, dict] = {}


@router.get("/mcp/sse")
async def sse_open(request: Request):
    user, bases, err = await _authenticate(request)
    if err:
        return _unauthorized(request, err)
    sid = uuid.uuid4().hex
    q: asyncio.Queue = asyncio.Queue()
    _sessions[sid] = {"queue": q, "user": user, "bases": bases}

    async def stream():
        try:
            yield f"event: endpoint\ndata: /mcp/messages?session_id={sid}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: message\ndata: {json.dumps(msg, default=str, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            _sessions.pop(sid, None)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@router.post("/mcp/messages")
async def sse_message(request: Request, session_id: str):
    s = _sessions.get(session_id)
    if not s:
        return JSONResponse({"error": "Unknown or expired session; reconnect to /mcp/sse"}, status_code=404)
    try:
        body = json.loads(await request.body() or b"null")
    except ValueError:
        return JSONResponse({"error": "Parse error"}, status_code=400)
    for m in body if isinstance(body, list) else [body]:
        reply = await handle(m, s["user"], s["bases"])
        if reply is not None:
            await s["queue"].put(reply)
    return Response(status_code=202, content="Accepted")
