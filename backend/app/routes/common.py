"""Request models and helpers shared by the route modules."""
import json
import uuid
from datetime import date, datetime
from typing import Any, Literal

from fastapi import Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import remote, store
from ..auth import current_user
from ..compute import Engine


def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, uuid.UUID):
        return str(o)
    raise TypeError(f"Not JSON serialisable: {type(o).__name__}")


class FastJSON(JSONResponse):
    """json.dumps with UUID/datetime support: much quicker than FastAPI's generic encoder for big tables."""

    def render(self, content) -> bytes:
        return json.dumps(content, default=_json_default, ensure_ascii=False, separators=(",", ":")).encode()

COMPONENTS = {"tasks": ["VTODO"], "events": ["VEVENT"], "mixed": ["VEVENT", "VTODO"]}

User = Depends(current_user)


# ======================================================================== models

class Credentials(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class AccountIn(BaseModel):
    name: str
    url: str
    carddav_url: str | None = None  # contacts address, when the provider uses a different one (iCloud)
    username: str | None = None
    password: str | None = None


class AccountTest(AccountIn):
    id: str | None = None  # reuse the stored password of an existing account when none is given


class BaseIn(BaseModel):
    name: str
    color: str | None = None


class BasePatch(BaseModel):
    name: str | None = None
    color: str | None = None
    position: int | None = None


class RemoteIn(BaseModel):
    url: str
    password: str | None = None
    item_id: str | None = None
    view_id: str | None = None


class RelinkIn(BaseModel):
    url: str | None = None
    password: str | None = None


class TableIn(BaseModel):
    base_id: str | None = None
    name: str
    kind: Literal["generic", "tasks", "events", "mixed", "contacts", "remote"] = "generic"
    account_id: str | None = None
    calendar_url: str | None = None
    new_calendar_name: str | None = None
    remote: RemoteIn | None = None


class TablePatch(BaseModel):
    name: str | None = None
    base_id: str | None = None


class FieldIn(BaseModel):
    name: str
    type: str
    options: dict[str, Any] = {}


class FieldPatch(BaseModel):
    name: str | None = None
    type: str | None = None
    options: dict[str, Any] | None = None


class FormulaCheck(BaseModel):
    expression: str
    field_id: str | None = None


class RecordIn(BaseModel):
    data: dict[str, Any] = {}
    table_id: str | None = None  # combined databases: the record's source table (needed for linked tables)


class ComboRecordIn(BaseModel):
    table_id: str
    link_table_id: str | None = None  # source is a linked combined database: which of its tables
    data: dict[str, Any] = {}


class ViewIn(BaseModel):
    name: str
    type: Literal["grid", "kanban", "timeline", "calendar", "list", "gallery", "chart"]
    config: dict[str, Any] = {}


class ViewPatch(BaseModel):
    name: str | None = None
    config: dict[str, Any] | None = None


class ComboSource(BaseModel):
    table_id: str
    color: str | None = None


class ComboField(BaseModel):
    id: str
    name: str
    mapping: dict[str, str] = {}


class ComboIn(BaseModel):
    base_id: str | None = None
    name: str
    sources: list[ComboSource]


class ComboPatch(BaseModel):
    name: str | None = None
    base_id: str | None = None
    sources: list[ComboSource] | None = None
    fields: list[ComboField] | None = None
    remap: bool = False  # rebuild the field mapping automatically from the sources


# ======================================================================== helpers

def _table_out(t):
    """A table row for the browser: a linked table's access cookie and token never leave the server."""
    if t is None:
        return t
    out = {k: v for k, v in dict(t).items() if k != "remote"}
    out["remote"] = remote.public_info(t)
    return out


def _account_public(a):
    return {k: a[k] for k in ("id", "name", "url", "carddav_url", "username", "is_builtin", "created_at")} | {"has_password": bool(a["password"])}


def _own_account(conn, account_id, user):
    a = conn.execute("SELECT * FROM caldav_accounts WHERE id = %s AND user_id = %s", (account_id, user["id"])).fetchone()
    return a or store.not_found("Account not found")


def _own_field(conn, field_id, user):
    f = conn.execute(
        "SELECT f.* FROM fields f JOIN tables t ON t.id = f.table_id WHERE f.id = %s AND t.user_id = %s", (field_id, user["id"])
    ).fetchone()
    return f or store.not_found("Field not found")


def _own_view(conn, view_id, user):
    v = conn.execute(
        "SELECT v.* FROM views v LEFT JOIN tables t ON t.id = v.table_id LEFT JOIN combos c ON c.id = v.combo_id "
        "WHERE v.id = %s AND (t.user_id = %s OR c.user_id = %s)", (view_id, user["id"], user["id"])
    ).fetchone()
    return v or store.not_found("View not found")


def _record_out(conn, table_id, record_id):
    """The record as it now looks (computed fields filled in; fresh from the source for linked tables)."""
    for r in Engine(conn).records(table_id):
        if str(r["id"]) == str(record_id):
            return store.clean_record(r)
    return {"id": str(record_id), "table_id": str(table_id), "data": {}}
