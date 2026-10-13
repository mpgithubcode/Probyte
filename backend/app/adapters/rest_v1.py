"""Probyte REST API v1 (/api/v1): a stable, documented API for scripts and automation tools.

OpenAPI: /api/v1/openapi.json · interactive docs: /api/v1/docs
"""
import json
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Security, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader, HTTPBearer
from pydantic import BaseModel, Field

from app.adapters import apiv1, webhooks
from app.core.db import connection, Jsonb
from app.core.secretbox import seal, unseal
from app.identity import auth

DESCRIPTION = """
Read and write your Probyte bases, tables and records from scripts and automation tools
(n8n, Make, Zapier, Home Assistant, GPT Actions, curl…).

**Authentication.** Create an API key in *Settings → API & MCP* and send it as `Authorization: Bearer pbk_…`
(or `X-API-Key: pbk_…`). Read-only keys can use `GET` endpoints only; keys limited to some bases see only those.

**Tables** can be given by id or by name (add `?base=` when two bases have a table with the same name).
**Fields** are keyed by name. Values are written the way you'd type them: `"2026-10-12"` or
`"2026-10-12T15:30:00Z"` for dates, an option name for selects, a list (or comma-separated text) for
multiple selects, record titles or ids for links, usernames for user fields. Empty values are left out of
responses.

**Webhooks** send `record.created`, `record.updated` and `record.deleted` events to your URL, signed with
`X-Probyte-Signature: sha256=<HMAC-SHA256 of the body using the webhook secret>`.
"""

v1 = FastAPI(title="Probyte API", version="1.0.0", description=DESCRIPTION, docs_url=None, redoc_url=None, openapi_url=None)


@v1.get("/openapi.json", include_in_schema=False)
def openapi_spec(request: Request):
    """The spec with this server's absolute address, so tools that import it (GPT Actions, n8n…) know where to call."""
    from fastapi.openapi.utils import get_openapi
    from app.identity.oauth import public_base
    spec = get_openapi(title=v1.title, version=v1.version, description=v1.description, routes=v1.routes)
    spec["servers"] = [{"url": f"{public_base(request)}/api/v1"}]
    return spec


@v1.get("/docs", include_in_schema=False)
def docs():
    from fastapi.openapi.docs import get_swagger_ui_html
    return get_swagger_ui_html(openapi_url="/api/v1/openapi.json", title="Probyte API",
                               swagger_ui_parameters={"persistAuthorization": True})

_bearer = HTTPBearer(auto_error=False, description="Your API key, e.g. pbk_…")
_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def api_user(request: Request, _b=Security(_bearer), _h=Security(_header)) -> dict:
    """The key's owner (or the logged-in user, for trying the API from the browser)."""
    return await auth.current_user(request)


User = Depends(api_user)
Fmt = Query("json", description="`json`: raw values (link and user fields as ids). `text`: values as shown in Probyte.")
def _field_key(field_key: Literal["name", "id"] | None = Query(None, description="Key fields by `name` (default) or by field `id`."),
               fieldKeyType: Literal["name", "id"] | None = Query(None, description="Same as field_key (Teable's name for it).")) -> str:
    return fieldKeyType or field_key or "name"


Key = Depends(_field_key)


def _run(fn, *args, **kw):
    """Run a core function with a database connection."""
    with connection() as conn:
        out = fn(conn, *args, **kw)
    return out[0] if isinstance(out, tuple) else out


# ---------------------------------------------------------------- models (for the docs)

class FieldsIn(BaseModel):
    fields: dict[str, Any] = Field(..., examples=[{"Name": "Call the plumber", "Status": "Todo", "Due": "2026-10-12"}])


class RecordsIn(BaseModel):
    records: list[FieldsIn] = Field(..., max_length=1000)


class RecordUpdate(BaseModel):
    id: str
    fields: dict[str, Any]


class UpdatesIn(BaseModel):
    records: list[RecordUpdate] = Field(..., max_length=1000)


class UpsertIn(BaseModel):
    records: list[FieldsIn] = Field(..., max_length=1000)
    match_on: list[str] = Field(..., examples=[["Email"]], description="Fields that identify a record. Matching records are updated, others created.")


class DeleteIn(BaseModel):
    ids: list[str] = Field(..., max_length=1000)


class FieldIn(BaseModel):
    name: str
    type: str = Field("text", description="text, long_text, number, checkbox, single_select, multi_select, date, url, email, rating, link, user, recurrence, formula…")
    options: dict[str, Any] = Field({}, examples=[{"choices": ["Todo", "Doing", "Done"]}])


class TableIn(BaseModel):
    name: str
    fields: list[FieldIn] | None = Field(None, description="The first field becomes the primary (title) field.")


class WebhookIn(BaseModel):
    name: str = "Webhook"
    url: str
    events: list[Literal["record.created", "record.updated", "record.deleted"]] = list(webhooks.EVENTS)
    tables: list[str] | None = Field(None, description="Table ids or names; leave out for every table.")
    enabled: bool = True


class WebhookPatch(BaseModel):
    name: str | None = None
    url: str | None = None
    events: list[Literal["record.created", "record.updated", "record.deleted"]] | None = None
    tables: list[str] | None = None
    all_tables: bool | None = Field(None, description="true: send events for every table (clears `tables`).")
    enabled: bool | None = None


# ---------------------------------------------------------------- account & schema

@v1.get("/me", tags=["Account"], summary="Who the key belongs to and what it may do")
def me(user=User):
    from app.core.context import api_bases
    return {"username": user["username"], "access": user.get("api_scope", "write"),
            "bases": "all" if api_bases.get() is None else sorted(api_bases.get())}


@v1.get("/bases", tags=["Schema"], summary="Bases with their tables and combined databases")
def bases(user=User):
    return {"bases": _run(apiv1.list_bases, user)}


@v1.post("/bases/{base}/tables", tags=["Schema"], status_code=201, summary="Create a table")
def create_table(base: str, body: TableIn, user=User):
    return _run(apiv1.create_table, user, body.name, base, [f.model_dump() for f in body.fields or []])


@v1.get("/tables/{table}", tags=["Schema"], summary="A table's fields and views")
def table(table: str, base: str | None = None, user=User):
    return _run(apiv1.table_schema, user, table, base)


@v1.get("/tables/{table}/schema", tags=["Schema"], summary="A table's records as a JSON Schema",
        description="A JSON Schema (draft 2020-12) describing a record's `fields` object: one property per field with its JSON "
                    "type, format, select options, read-only fields, required fields and defaults. Keyed by field name, or by "
                    "id with `fieldKeyType=id`. Works for tables and combined databases.")
def json_schema(table: str, base: str | None = None, field_key: str = Key, user=User):
    return _run(apiv1.json_schema, user, table, base, field_key)


@v1.post("/tables/{table}/fields", tags=["Schema"], status_code=201, summary="Add a field")
def add_field(table: str, body: FieldIn, base: str | None = None, user=User):
    return _run(apiv1.add_field, user, table, body.name, body.type, body.options, base)


@v1.post("/tables/{table}/sync", tags=["Schema"], summary="Sync a CalDAV / CardDAV / files table now")
def sync_now(table: str, base: str | None = None, user=User):
    return apiv1.sync_table(user, table, base)


# ---------------------------------------------------------------- records

@v1.get("/tables/{table}/records", tags=["Records"], summary="List records",
        description="Filter with `filter` (JSON): `[{\"field\": \"Status\", \"op\": \"is\", \"value\": \"Done\"}]` or "
                    "`{\"Status\": \"Done\"}`. Ops: is, is_not, contains, not_contains, gt, gte, lt, lte, empty, not_empty. "
                    "Sort with `sort=-Due,Name` (minus = descending). Page with `limit` (max 1000) and `offset`; "
                    "`next_offset` is null on the last page.")
def list_records(table: str, base: str | None = None, view: str | None = Query(None, description="Apply a view's filters and sort (id or name)"),
                 filter: str | None = None, search: str | None = Query(None, description="Text to find in any field"),
                 sort: str | None = None, fields: str | None = Query(None, description="Comma-separated field names to return"),
                 limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), cell_format: Literal["json", "text"] = Fmt,
                 field_key: str = Key, user=User):
    return _run(apiv1.query_records, user, table, base=base, view=view, filters=filter, search=search, sort=sort,
                fields=fields, limit=limit, offset=offset, cell_format=cell_format, field_key=field_key)


@v1.get("/tables/{table}/records/{record_id}", tags=["Records"], summary="Get a record")
def get_record(table: str, record_id: str, cell_format: Literal["json", "text"] = Fmt, field_key: str = Key, user=User):
    return _run(apiv1.get_record, user, record_id, cell_format, field_key, table)


@v1.post("/tables/{table}/records", tags=["Records"], status_code=201, summary="Create records",
         description="Send `{\"fields\": {...}}` for one record or `{\"records\": [{\"fields\": {...}}, ...]}` for up to 1000. "
                     "A batch is all-or-nothing.")
def create_records(table: str, body: dict[str, Any], base: str | None = None, cell_format: Literal["json", "text"] = Fmt,
                   field_key: str = Key, user=User):
    single = "records" not in body
    recs = [body] if single else body["records"]
    if not isinstance(recs, list):
        raise HTTPException(400, "records must be a list")
    out = _run(apiv1.create_records, user, table, recs, base, cell_format, field_key)
    return out[0] if single else {"records": out}


@v1.patch("/tables/{table}/records", tags=["Records"], summary="Update records",
          description="Only the fields you send change. Up to 1000 records; all-or-nothing.")
def update_records(table: str, body: UpdatesIn, base: str | None = None, cell_format: Literal["json", "text"] = Fmt,
                   field_key: str = Key, user=User):
    return {"records": _run(apiv1.update_records, user, table, [r.model_dump() for r in body.records], base, cell_format, field_key)}


@v1.patch("/tables/{table}/records/{record_id}", tags=["Records"], summary="Update a record")
def update_record(table: str, record_id: str, body: FieldsIn, cell_format: Literal["json", "text"] = Fmt,
                  field_key: str = Key, user=User):
    return _run(apiv1.update_records, user, table, [{"id": record_id, "fields": body.fields}], None, cell_format, field_key)[0]


@v1.post("/tables/{table}/records/upsert", tags=["Records"], summary="Create or update records by matching fields")
def upsert_records(table: str, body: UpsertIn, base: str | None = None, cell_format: Literal["json", "text"] = Fmt,
                   field_key: str = Key, user=User):
    return _run(apiv1.upsert_records, user, table, [r.model_dump() for r in body.records], body.match_on, base, cell_format, field_key)


@v1.post("/tables/{table}/records/{record_id}/fields/{field}/files", tags=["Records"], summary="Upload files into a field",
         description="Send the files as `multipart/form-data`, each as a form field named `files`. The field (name or id) decides "
                     "what happens: a **link to a files table** (S3 / WebDAV) uploads them into that table (into `folder`, if "
                     "given) and links the new files from the record; an **attachment** field attaches them; a files "
                     "table's own **File** field replaces the file's contents (one file). They're added to what's already "
                     "there unless `replace` is true.\n\n"
                     "`curl -H 'Authorization: Bearer pbk_…' -F files=@plan.pdf -F folder=Jobs/7 "
                     "https://probyte.example.com/api/v1/tables/Jobs/records/<id>/fields/Drawings/files`")
def upload_to_field(table: str, record_id: str, field: str, files: list[UploadFile] = File(...),
                    folder: str = Form("", description="Files tables: the folder the files go into"),
                    replace: bool = Form(False, description="Replace the field's files / links instead of adding to them"),
                    base: str | None = None, cell_format: Literal["json", "text"] = Fmt, field_key: str = Key, user=User):
    _write_scope(user)
    return _run(apiv1.upload_to_field, user, table, record_id, field, files, folder=folder, replace=replace, base=base,
                cell_format=cell_format, field_key=field_key)


@v1.delete("/tables/{table}/records/{record_id}", tags=["Records"], summary="Delete a record")
def delete_record(table: str, record_id: str, user=User):
    return {"deleted": _run(apiv1.delete_records, user, table, [record_id])}


@v1.post("/tables/{table}/records/delete", tags=["Records"], summary="Delete records")
def delete_records(table: str, body: DeleteIn, base: str | None = None, user=User):
    return {"deleted": _run(apiv1.delete_records, user, table, body.ids, base)}


@v1.get("/search", tags=["Records"], summary="Find records in every table")
def search(q: str, limit: int = Query(20, ge=1, le=200), user=User):
    """Linked tables are searched too; `skipped` lists any whose source didn't answer in time."""
    with connection() as conn:
        results, skipped = apiv1.search_everything(conn, user, q, limit)
    return {"results": results, "skipped": skipped}


# ---------------------------------------------------------------- webhooks

def _hook_out(h, secret=False):
    out = {k: h[k] for k in ("id", "name", "url", "events", "enabled", "last_status", "last_error", "last_delivery_at", "created_at")}
    out["id"] = str(out["id"])
    out["tables"] = h["table_ids"]
    if secret:
        out["secret"] = unseal(h["secret"])
    return out


def _table_ids(conn, user, refs):
    if refs is None:
        return None
    return [str(apiv1._writable_table(conn, user, r)["id"]) for r in refs]


def _write_scope(user):
    if user.get("api_scope") == "read":
        raise HTTPException(403, "This API key is read-only")


@v1.get("/webhooks", tags=["Webhooks"], summary="List webhooks")
def list_webhooks(user=User):
    with connection() as conn:
        # The secret is shown to you in the app, not to API keys.
        return {"webhooks": [_hook_out(h, secret=not user.get("api_key")) for h in conn.execute(
            "SELECT * FROM webhooks WHERE user_id = %s ORDER BY created_at", (user["id"],)).fetchall()]}


@v1.post("/webhooks", tags=["Webhooks"], status_code=201, summary="Create a webhook",
         description="The response includes the signing `secret` (it's also shown in Settings).")
def create_webhook(body: WebhookIn, user=User):
    _write_scope(user)
    url = webhooks.check_url(body.url)
    with connection() as conn:
        h = conn.execute(
            "INSERT INTO webhooks (user_id, name, url, secret, events, table_ids, enabled) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (user["id"], body.name.strip() or "Webhook", url, seal(webhooks.new_secret()), Jsonb(body.events or list(webhooks.EVENTS)),
             Jsonb(_table_ids(conn, user, body.tables)) if body.tables is not None else None, body.enabled)).fetchone()
    return _hook_out(h, secret=True)


@v1.patch("/webhooks/{hook_id}", tags=["Webhooks"], summary="Change a webhook")
def update_webhook(hook_id: str, body: WebhookPatch, user=User):
    _write_scope(user)
    with connection() as conn:
        h = conn.execute("SELECT * FROM webhooks WHERE id::text = %s AND user_id = %s", (hook_id, user["id"])).fetchone()
        if not h:
            raise HTTPException(404, "Webhook not found")
        tables = None if body.all_tables else (_table_ids(conn, user, body.tables) if body.tables is not None else h["table_ids"])
        h = conn.execute(
            "UPDATE webhooks SET name = %s, url = %s, events = %s, table_ids = %s, enabled = %s WHERE id = %s RETURNING *",
            (body.name or h["name"], webhooks.check_url(body.url) if body.url else h["url"], Jsonb(body.events or h["events"]),
             Jsonb(tables) if tables is not None else None, h["enabled"] if body.enabled is None else body.enabled, h["id"])).fetchone()
    return _hook_out(h, secret=True)


@v1.delete("/webhooks/{hook_id}", tags=["Webhooks"], summary="Delete a webhook")
def delete_webhook(hook_id: str, user=User):
    _write_scope(user)
    with connection() as conn:
        conn.execute("DELETE FROM webhooks WHERE id::text = %s AND user_id = %s", (hook_id, user["id"]))
    return {"ok": True}


@v1.post("/webhooks/{hook_id}/test", tags=["Webhooks"], summary="Send a test event")
async def test_webhook(hook_id: str, user=User):
    with connection() as conn:
        h = conn.execute("SELECT * FROM webhooks WHERE id::text = %s AND user_id = %s", (hook_id, user["id"])).fetchone()
    if not h:
        raise HTTPException(404, "Webhook not found")
    status, err = await run_in_threadpool(webhooks.deliver, dict(h), "ping",
                                          {"message": "Test event from Probyte", "webhook": {"id": str(h["id"]), "name": h["name"]}})
    return {"ok": status is not None and 200 <= status < 300, "status": status, "error": err}


@v1.exception_handler(json.JSONDecodeError)
async def _bad_json(request, exc):
    return JSONResponse({"detail": "The request body isn't valid JSON"}, status_code=400)
