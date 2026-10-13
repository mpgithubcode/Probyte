"""Request models and helpers shared by the route modules."""
from typing import Any, Literal

from pydantic import BaseModel

from app.calc.compute import Engine
from app.core.http import FastJSON  # noqa: F401  (used by the route modules)
from app.data import access, store
from app.identity.auth import User  # noqa: F401
from app.sharing import linked as remote


COMPONENTS = {"tasks": ["VTODO"], "events": ["VEVENT"], "mixed": ["VEVENT", "VTODO"]}



# ======================================================================== models

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
    kind: Literal["generic", "tasks", "events", "mixed", "contacts", "remote", "files"] = "generic"
    account_id: str | None = None
    storage_id: str | None = None          # files: a saved storage connection…
    storage: dict[str, Any] | None = None  # …or a new one (see files.StorageIn)
    storage_prefix: str | None = None      # files: the folder inside the bucket / WebDAV folder ('' = all of it)
    calendar_url: str | None = None
    new_calendar_name: str | None = None
    remote: RemoteIn | None = None


class TablePatch(BaseModel):
    name: str | None = None
    base_id: str | None = None
    hidden: bool | None = None  # left out of the sidebar
    icon: str | None = None     # an emoji; "" = the default icon
    description: str | None = None  # what the table is for; "" removes it


class FieldIn(BaseModel):
    name: str
    type: str
    options: dict[str, Any] = {}
    description: str | None = None
    required: bool = False
    unique: bool = False
    default_value: Any = None


class FieldPatch(BaseModel):
    name: str | None = None
    type: str | None = None
    options: dict[str, Any] | None = None
    description: str | None = None
    required: bool | None = None
    unique: bool | None = None
    default_value: Any = None   # send null to remove the default


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
    type: Literal["grid", "kanban", "timeline", "calendar", "list", "gallery", "chart"] | None = None  # show it another way


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
    hidden: bool | None = None
    icon: str | None = None
    description: str | None = None
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


def access_info(conn, base_id, user_id) -> dict:
    """What the user may do in a base (the app shows only what applies): their role, whether they follow the base's
    views, and whose base it is."""
    m = access.membership(conn, base_id, user_id) or {"role": "viewer", "follow_views": True, "owner": None}
    owner = conn.execute("SELECT username FROM users WHERE id = %s", (m["owner"],)).fetchone() if m["role"] != "owner" else None
    return {"role": m["role"], "follow_views": m["follow_views"], "owner": owner["username"] if owner else None}


def _own_field(conn, field_id, user, need="admin"):
    """A field of a table the user may change the fields of (its owner, or an admin of its base)."""
    f = conn.execute("SELECT f.*, t.base_id AS _base_id FROM fields f JOIN tables t ON t.id = f.table_id WHERE f.id = %s",
                     (field_id,)).fetchone()
    if not f or not store.base_allowed(f["_base_id"]):
        store.not_found("Field not found")
    access.check(conn, f["_base_id"], user["id"], need, "Field")
    return {k: v for k, v in f.items() if k != "_base_id"}


def _own_view(conn, view_id, user):
    """A view the user can see: one of a base they have access to, or their own copy."""
    v = conn.execute(
        "SELECT v.*, COALESCE(t.base_id, c.base_id) AS _base_id FROM views v LEFT JOIN tables t ON t.id = v.table_id "
        "LEFT JOIN combos c ON c.id = v.combo_id WHERE v.id = %s", (view_id,)).fetchone()
    if not v or not store.base_allowed(v["_base_id"]) or (v["user_id"] and str(v["user_id"]) != str(user["id"])):
        store.not_found("View not found")
    access.check(conn, v["_base_id"], user["id"], "viewer", "View")
    return v


def _record_out(conn, table_id, record_id):
    """The record as it now looks (computed fields filled in; fresh from the source for linked tables)."""
    r = Engine(conn).record(table_id, record_id)
    if r:
        return store.clean_record(r)
    return {"id": str(record_id), "table_id": str(table_id), "data": {}}
