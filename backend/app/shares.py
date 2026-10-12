"""Public links: share chosen tables / combined databases / views of a base as a web page,
an embeddable iframe, or an installable app (PWA).

Visitors only ever receive fields that are visible in a shared view, and only records that
pass the view's filters — filtering happens here, not in the browser.
"""
import hashlib
import hmac
import io
import secrets
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import attachments, audit, auth, ratelimit, store
from .compute import COMPUTED_TYPES, Engine
from .db import Jsonb, app_secret, connection
from .routes.common import FastJSON
from .viewfilter import apply_view

router = APIRouter()
User = Depends(auth.current_user)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
READONLY_IN_SHARES = {"link", "user", "attachment"}
HIDDEN_BY_DEFAULT = {"record_id"}  # shown only when a view turns them on (config.shown)  # editing these would expose other tables / users / uploads


# ======================================================================== owner side

class ShareItem(BaseModel):
    kind: Literal["table", "combo"]
    id: str
    views: list[str] | None = None  # None = every view of the table


class SharePerms(BaseModel):
    add: bool = False
    edit: bool = False
    delete: bool = False
    # files tables: upload new files / change a file's contents (replace it, edit its text, edit it in ONLYOFFICE).
    # None (links made before these existed): the same as add / edit.
    upload_files: bool | None = None
    edit_files: bool | None = None


def effective(perms: dict | None) -> dict:
    """What a link allows, every permission spelled out."""
    p = perms or {}
    out = {k: bool(p.get(k)) for k in ("add", "edit", "delete")}
    out["upload_files"] = out["add"] if p.get("upload_files") is None else bool(p["upload_files"])
    out["edit_files"] = out["edit"] if p.get("edit_files") is None else bool(p["edit_files"])
    return out


class ShareIn(BaseModel):
    name: str
    base_id: str
    items: list[ShareItem]
    permissions: SharePerms = SharePerms()
    password: str | None = None      # None = unchanged, "" = remove the password
    expires_at: datetime | None = None
    enabled: bool = True
    color: str | None = None


def _validate_items(conn, user_id, base_id, items: list[ShareItem]) -> list[dict]:
    out = []
    for it in items:
        row = conn.execute(f"SELECT id FROM {'tables' if it.kind == 'table' else 'combos'} WHERE id = %s AND user_id = %s AND base_id = %s",
                           (it.id, user_id, base_id)).fetchone()
        if not row:
            raise HTTPException(400, "Every shared table must belong to the chosen base")
        col = "table_id" if it.kind == "table" else "combo_id"
        all_views = [str(v["id"]) for v in conn.execute(f"SELECT id FROM views WHERE {col} = %s ORDER BY position", (it.id,)).fetchall()]
        views = all_views if it.views is None else [v for v in it.views if v in all_views]
        if not views:
            raise HTTPException(400, "Choose at least one view for every shared table")
        out.append({"kind": it.kind, "id": it.id, "views": None if it.views is None else views})
    if not out:
        raise HTTPException(400, "Choose at least one table to share")
    return out


def _share_public(s):
    return {k: s[k] for k in ("id", "base_id", "token", "name", "items", "permissions", "expires_at", "enabled", "color", "created_at", "updated_at")} \
        | {"has_password": bool(s["password_hash"])}


@router.get("/api/shares")
def list_shares(user=User):
    with connection() as conn:
        return [_share_public(s) for s in conn.execute("SELECT * FROM shares WHERE user_id = %s ORDER BY created_at DESC", (user["id"],)).fetchall()]


@router.post("/api/shares")
def create_share(body: ShareIn, user=User):
    with connection() as conn:
        store.get_base(conn, body.base_id, user["id"])
        items = _validate_items(conn, user["id"], body.base_id, body.items)
        s = conn.execute(
            "INSERT INTO shares (user_id, base_id, token, name, items, permissions, password_hash, expires_at, enabled, color) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (user["id"], body.base_id, secrets.token_urlsafe(12), body.name.strip() or "Shared view", Jsonb(items),
             Jsonb(body.permissions.model_dump()), auth.hash_password(body.password) if body.password else None,
             body.expires_at, body.enabled, body.color or "#6366f1"),
        ).fetchone()
    return _share_public(s)


def _own_share(conn, share_id, user):
    s = conn.execute("SELECT * FROM shares WHERE id = %s AND user_id = %s", (share_id, user["id"])).fetchone()
    return s or store.not_found("Public link not found")


@router.patch("/api/shares/{share_id}")
def update_share(share_id: str, body: ShareIn, user=User):
    with connection() as conn:
        s = _own_share(conn, share_id, user)
        store.get_base(conn, body.base_id, user["id"])
        items = _validate_items(conn, user["id"], body.base_id, body.items)
        pw = s["password_hash"] if body.password is None else (auth.hash_password(body.password) if body.password else None)
        s = conn.execute(
            "UPDATE shares SET base_id=%s, name=%s, items=%s, permissions=%s, password_hash=%s, expires_at=%s, enabled=%s, "
            "color=%s, updated_at=now() WHERE id=%s RETURNING *",
            (body.base_id, body.name.strip() or s["name"], Jsonb(items), Jsonb(body.permissions.model_dump()), pw,
             body.expires_at, body.enabled, body.color or s["color"], share_id),
        ).fetchone()
    return _share_public(s)


@router.post("/api/shares/{share_id}/regenerate")
def regenerate_share(share_id: str, user=User):
    """New URL for the link; the old URL stops working."""
    with connection() as conn:
        _own_share(conn, share_id, user)
        s = conn.execute("UPDATE shares SET token = %s, updated_at = now() WHERE id = %s RETURNING *",
                         (secrets.token_urlsafe(12), share_id)).fetchone()
    return _share_public(s)


@router.delete("/api/shares/{share_id}")
def delete_share(share_id: str, user=User):
    with connection() as conn:
        _own_share(conn, share_id, user)
        conn.execute("DELETE FROM shares WHERE id = %s", (share_id,))
    return {"ok": True}


# ======================================================================== visitor side

def _load(conn, token):
    s = conn.execute("SELECT * FROM shares WHERE token = %s", (token,)).fetchone()
    if not s or not s["enabled"] or (s["expires_at"] and s["expires_at"] < datetime.now(timezone.utc)):
        raise HTTPException(404, "This link doesn't exist or is no longer available")
    return s


def _cookie_name(s):
    return "pbshare_" + str(s["id"]).replace("-", "")[:12]


def _cookie_value(s):
    return hmac.new(app_secret(), f"{s['id']}:{s['password_hash']}".encode(), hashlib.sha256).hexdigest()


def _ok(s, cookie: str | None) -> bool:
    """Access check: links without a password are open; others need the cookie set by unlock()."""
    return not s["password_hash"] or hmac.compare_digest(cookie or "", _cookie_value(s))


def _require(s, cookie):
    if not _ok(s, cookie):
        raise HTTPException(401, "password_required")


def _items(conn, s):
    """Shared items with their allowed views (resolving 'all views')."""
    out = []
    for it in s["items"]:
        table = conn.execute(f"SELECT id, name{', kind' if it['kind'] == 'table' else ''} FROM {'tables' if it['kind'] == 'table' else 'combos'} "
                             f"WHERE id = %s AND user_id = %s AND base_id = %s{' AND trashed_at IS NULL' if it['kind'] == 'table' else ''}",
                             (it["id"], s["user_id"], s["base_id"])).fetchone()
        if not table:
            continue
        col = "table_id" if it["kind"] == "table" else "combo_id"
        views = conn.execute(f"SELECT * FROM views WHERE {col} = %s ORDER BY position", (it["id"],)).fetchall()
        if it.get("views") is not None:
            views = [v for v in views if str(v["id"]) in it["views"]]
        if views:
            kind = table.get("kind", "combo")
            if kind == "remote":  # a linked table shared on again: what its source is
                kind = (conn.execute("SELECT remote FROM tables WHERE id = %s", (table["id"],)).fetchone()["remote"] or {}).get("source_kind") or "remote"
            out.append({"kind": it["kind"], "id": str(table["id"]), "name": table["name"], "table_kind": kind, "views": views})
    return out


def _find_view(conn, s, view_id):
    for it in _items(conn, s):
        for v in it["views"]:
            if str(v["id"]) == str(view_id):
                return it, v
    raise HTTPException(404, "View not found in this link")


def _view_payload(conn, s, item, view):
    """Fields visible in the view, records passing its filters, and the references they need."""
    eng = Engine(conn)
    hidden = set(view["config"].get("hidden") or [])
    shown = set(view["config"].get("shown") or [])
    if item["kind"] == "table":
        all_fields = eng.describe(item["id"])
        records = eng.records(item["id"])
        sources = None
    else:
        combo = store.get_combo(conn, item["id"], s["user_id"])
        sources, all_fields = store.resolve_combo(conn, combo, eng)
        records = store.combo_records(conn, combo, eng)
    filtered = apply_view(eng, all_fields, records, view["config"])
    hidden |= {str(f["id"]) for f in all_fields if f["type"] in HIDDEN_BY_DEFAULT and str(f["id"]) not in shown}
    fields = [dict(f) for f in all_fields if f["id"] == "__source" or f.get("is_primary") or str(f["id"]) not in hidden]
    keep = {str(f["id"]) for f in fields}
    for f in fields:
        if f["type"] in READONLY_IN_SHARES or f["type"] in COMPUTED_TYPES:
            f["readonly"] = True
        f["options"] = _display_options(f["options"])
        f.pop("mapping", None)
    out_records = [{"id": str(r["id"]), "table_id": str(r["table_id"]), "created_at": r["created_at"],
                    "data": {k: v for k, v in r["data"].items() if k in keep}} for r in filtered]
    refs = _refs(eng, item, fields, out_records, all_fields)
    cfg = dict(view["config"])
    for key in ("groupBy", "dateField", "endField", "colorField", "coverField"):
        if cfg.get(key) and cfg[key] not in keep:
            cfg[key] = None
    return {"item": {"kind": item["kind"], "id": item["id"], "name": item["name"], "table_kind": item["table_kind"]},
            "view": {"id": str(view["id"]), "name": view["name"], "type": view["type"], "config": cfg},
            "views": [{"id": str(v["id"]), "name": v["name"], "type": v["type"], "config": {}} for v in item["views"]],
            "fields": fields, "records": out_records, "refs": refs,
            "permissions": effective(s["permissions"]), "link_name": s["name"],
            "sources": [{k: x[k] for k in ("table_id", "name", "kind", "color")} for x in sources] if sources else None}


# Only what's needed to display values; formulas, conditions and table/field ids stay private.
_DISPLAY_OPTIONS = {"choices", "includeTime", "max", "precision", "resultType", "label", "color", "action", "url",
                    "updates", "multiple", "relationship", "numberFormat", "currency", "separators", "dateFormat", "timeFormat",
                    "allowNew"}


def _display_options(o: dict) -> dict:
    out = {k: v for k, v in o.items() if k in _DISPLAY_OPTIONS}
    if o.get("_inner"):
        out["_inner"] = {"type": o["_inner"]["type"], "name": o["_inner"]["name"],
                         "options": {k: v for k, v in o["_inner"]["options"].items() if k in _DISPLAY_OPTIONS}}
    return out


def _refs(eng, item, fields, records, all_fields):
    """Titles / usernames referenced by the visible fields only."""
    titles, users = {}, {}
    if item["kind"] == "table":
        return eng.refs(item["id"], records)
    by_unified = {f["id"]: f for f in all_fields}
    for f in fields:
        uf = by_unified.get(f["id"])
        for tid, sfid in (uf or {}).get("mapping", {}).items():
            src = eng.field(sfid)
            if not src:
                continue
            recs = [{"data": {sfid: r["data"].get(f["id"])}} for r in records if r["table_id"] == tid]
            if recs:
                sub = eng.refs(tid, recs)
                titles.update(sub["records"])
                users.update(sub["users"])
    return {"records": titles, "users": users}


def _writable(payload, data: dict) -> dict:
    allowed = {str(f["id"]) for f in payload["fields"] if not f.get("readonly")}
    return {k: v for k, v in data.items() if k in allowed}


def _visible_record(payload, record_id):
    rec = next((r for r in payload["records"] if r["id"] == str(record_id)), None)
    if not rec:
        raise HTTPException(404, "Record not found in this view")
    return rec


# ---------------------------------------------------------------- core (used by the endpoints below and by linked tables)

def core_meta(token: str, cookie: str | None) -> dict:
    with connection() as conn:
        s = _load(conn, token)
        unlocked = _ok(s, cookie)
        items = _items(conn, s) if unlocked else []
    return {"name": s["name"], "color": s["color"], "permissions": effective(s["permissions"]), "needs_password": not unlocked,
            "items": [{"kind": i["kind"], "id": i["id"], "name": i["name"], "table_kind": i["table_kind"],
                       "views": [{"id": str(v["id"]), "name": v["name"], "type": v["type"]} for v in i["views"]]} for i in items]}


def core_unlock(token: str, password: str, request: Request | None = None) -> tuple[str, str]:
    """Returns (cookie name, cookie value) proving the password was entered."""
    with connection() as conn:
        s = _load(conn, token)
    ratelimit.check(f"share:{s['id']}", request)
    if not s["password_hash"] or not auth.verify_password(password, s["password_hash"]):
        ratelimit.failed(f"share:{s['id']}", request)
        raise HTTPException(401, "Wrong password")
    ratelimit.succeeded(f"share:{s['id']}")
    return _cookie_name(s), _cookie_value(s)


def core_view(token: str, view_id: str, cookie: str | None) -> dict:
    with connection() as conn:
        s = _load(conn, token)
        _require(s, cookie)
        item, view = _find_view(conn, s, view_id)
        return _view_payload(conn, s, item, view)


def core_create(token: str, view_id: str, data: dict, table_id: str | None, cookie: str | None) -> dict:
    from . import recordops
    with connection() as conn:
        s = _load(conn, token)
        audit.set_actor(f"Public link “{s['name']}”")
        _require(s, cookie)
        if not s["permissions"].get("add"):
            raise HTTPException(403, "Adding records isn't allowed on this link")
        item, view = _find_view(conn, s, view_id)
        data = _writable(_view_payload(conn, s, item, view), data)
        if item["kind"] == "table":
            tid = item["id"]
        else:
            combo = store.get_combo(conn, item["id"], s["user_id"])
            tid = table_id or (combo["sources"][0]["table_id"] if len(combo["sources"]) == 1 else None)
            if tid not in {x["table_id"] for x in combo["sources"]}:
                raise HTTPException(400, "Choose where to add the record")
            data = store.unified_to_source(conn, combo, tid, data)
        rid, table = recordops.create(conn, tid, data, s["user_id"], anonymous=True)
    with connection() as conn:
        payload = _view_payload(conn, s, item, view)
    return next((x for x in payload["records"] if x["id"] == str(rid)), {"id": str(rid), "data": {}, "table_id": str(tid)})


def core_update(token: str, view_id: str, record_id: str, data: dict, cookie: str | None) -> dict:
    from . import recordops
    with connection() as conn:
        s = _load(conn, token)
        audit.set_actor(f"Public link “{s['name']}”")
        _require(s, cookie)
        if not s["permissions"].get("edit"):
            raise HTTPException(403, "Editing isn't allowed on this link")
        item, view = _find_view(conn, s, view_id)
        payload = _view_payload(conn, s, item, view)
        rec = _visible_record(payload, record_id)
        clean = _writable(payload, data)
        if item["kind"] == "combo":
            combo = store.get_combo(conn, item["id"], s["user_id"])
            clean = store.unified_to_source(conn, combo, rec["table_id"], clean)
        recordops.update(conn, rec["table_id"], record_id, clean, s["user_id"], anonymous=True)
    with connection() as conn:
        payload = _view_payload(conn, s, item, view)
    return next((x for x in payload["records"] if x["id"] == str(record_id)), {**rec, "data": {**rec["data"], **data}})


def core_delete(token: str, view_id: str, record_id: str, cookie: str | None) -> None:
    from . import recordops
    with connection() as conn:
        s = _load(conn, token)
        audit.set_actor(f"Public link “{s['name']}”")
        _require(s, cookie)
        if not s["permissions"].get("delete"):
            raise HTTPException(403, "Deleting isn't allowed on this link")
        item, view = _find_view(conn, s, view_id)
        rec = _visible_record(_view_payload(conn, s, item, view), record_id)
        recordops.delete(conn, rec["table_id"], record_id, s["user_id"])


def core_attachment(token: str, attachment_id: str, cookie: str | None, download: bool):
    with connection() as conn:
        s = _load(conn, token)
        _require(s, cookie)
        table_ids = []
        for it in _items(conn, s):
            if it["kind"] == "table":
                table_ids.append(it["id"])
            else:
                table_ids += [x["table_id"] for x in store.get_combo(conn, it["id"], s["user_id"])["sources"]]
        used = conn.execute(
            "SELECT 1 FROM records WHERE table_id = ANY(%s::uuid[]) AND NOT deleted "
            "AND jsonb_path_exists(data, '$.*[*] ? (@.id == $id)', jsonb_build_object('id', %s::text)) LIMIT 1",
            (table_ids, attachment_id)).fetchone()
        if not used:
            raise HTTPException(404, "File not found")
        return attachments.serve(conn, s["user_id"], attachment_id, download)


def core_file(token: str, record_id: str, cookie: str | None):
    """A file of a files table that this link shows: (table, record, field keys)."""
    from . import files
    with connection() as conn:
        s = _load(conn, token)
        _require(s, cookie)
        table_ids = []
        for it in _items(conn, s):
            if it["kind"] == "table":
                table_ids.append(it["id"])
            else:
                table_ids += [x["table_id"] for x in store.get_combo(conn, it["id"], s["user_id"])["sources"]]
        rec = conn.execute("SELECT * FROM records WHERE id = %s AND table_id = ANY(%s::uuid[]) AND NOT deleted",
                           (record_id, table_ids)).fetchone()
        if not rec:
            raise HTTPException(404, "File not found")
        table = store.get_table(conn, rec["table_id"])
        if table["kind"] != "files":
            raise HTTPException(404, "File not found")
        return table, rec, files._keys(store.get_fields(conn, table["id"]))


def _table_item(conn, s, view_id, need: str | None, kind: str):
    """The shared table behind a view, when the link allows `need` ("add" / "edit") and it's that kind of table."""
    if need and not effective(s["permissions"])[need]:
        raise HTTPException(403, {"add": "Adding isn't allowed on this link", "edit": "Editing isn't allowed on this link",
                                  "upload_files": "Uploading files isn't allowed on this link",
                                  "edit_files": "Changing files isn't allowed on this link"}[need])
    item, view = _find_view(conn, s, view_id)
    if item["kind"] != "table" or item["table_kind"] != kind:
        raise HTTPException(400, "That isn't possible with this table")
    return item, view


def core_upload(token: str, view_id: str, files, folder: str, cookie: str | None) -> list[dict]:
    from . import files as files_mod
    with connection() as conn:
        s = _load(conn, token)
        _require(s, cookie)
        audit.set_actor(f"Public link “{s['name']}”")
        item, _ = _table_item(conn, s, view_id, "upload_files", "files")
        table = store.get_table(conn, item["id"])
    if table["kind"] == "remote":  # shared on again: through its own link
        from . import remote
        return remote.upload(table, files, folder)
    return files_mod.upload_files(item["id"], files, folder, s["user_id"])


def _editable_file(token, view_id, record_id, cookie):
    from . import files as files_mod
    with connection() as conn:
        s = _load(conn, token)
        _require(s, cookie)
        audit.set_actor(f"Public link “{s['name']}”")
        item, view = _table_item(conn, s, view_id, "edit_files", "files")
        _visible_record(_view_payload(conn, s, item, view), record_id)
        table = store.get_table(conn, item["id"])
        if table["kind"] == "remote":
            return table, None, None
        rec = conn.execute("SELECT * FROM records WHERE id = %s AND table_id = %s AND NOT deleted", (record_id, item["id"])).fetchone()
        if not rec:
            raise HTTPException(404, "File not found")
        return table, rec, files_mod._keys(store.get_fields(conn, table["id"]))


def core_replace(token, view_id, record_id, upload, cookie, new_ext: str | None = None):
    """New contents for a file the link shows; new_ext: the new version is in another format (a .doc edited in
    ONLYOFFICE comes back as .docx), so the file is renamed to match."""
    from . import files as files_mod, remote
    table, rec, keys = _editable_file(token, view_id, record_id, cookie)
    if rec is None:
        return remote.replace_file(table, record_id, upload, new_ext)
    files_mod._check_size(upload)
    return files_mod.replace_contents(table, rec, keys, upload.file, files_mod.upload_mime(upload), new_ext)


def core_text(token, view_id, record_id, text, cookie):
    from . import files as files_mod, remote
    table, rec, keys = _editable_file(token, view_id, record_id, cookie)
    if rec is None:
        return remote.save_text(table, record_id, text)
    return files_mod.save_text_contents(table, rec, keys, text)


def core_vcards_import(token, view_id, texts, existing, cookie):
    from . import contacts
    with connection() as conn:
        s = _load(conn, token)
        _require(s, cookie)
        audit.set_actor(f"Public link “{s['name']}”")
        item, _ = _table_item(conn, s, view_id, "add", "contacts")
        if existing == "update" and not s["permissions"].get("edit"):
            existing = "skip"  # updating needs the right to edit
        table = store.get_table(conn, item["id"])
    if table["kind"] == "remote":
        from . import remote
        return remote.import_vcards(table, texts, existing)
    return contacts.import_texts(item["id"], texts, existing, s["user_id"])


def core_vcards_export(token, view_id, cookie):
    from . import contacts
    with connection() as conn:
        s = _load(conn, token)
        _require(s, cookie)
        item, view = _table_item(conn, s, view_id, None, "contacts")
        shown = {r["id"] for r in _view_payload(conn, s, item, view)["records"]}
        table = store.get_table(conn, item["id"])
    if table["kind"] == "remote":
        from . import remote
        return remote.export_vcards(table)
    return contacts.vcards_response(item["id"], only=shown)


# ---------------------------------------------------------------- HTTP endpoints for visitors

def _cookie(request: Request, token: str) -> str | None:
    with connection() as conn:
        s = conn.execute("SELECT id FROM shares WHERE token = %s", (token,)).fetchone()
    return request.cookies.get(_cookie_name(s)) if s else None


def _json(content):
    return FastJSON(content)


class Unlock(BaseModel):
    password: str


class PublicRecord(BaseModel):
    data: dict = {}
    table_id: str | None = None  # combined databases: which source table a new record goes into


@router.get("/api/public/{token}")
def share_meta(token: str, request: Request):
    return core_meta(token, _cookie(request, token))


@router.post("/api/public/{token}/unlock")
def share_unlock(token: str, body: Unlock, request: Request, response: Response):
    name, value = core_unlock(token, body.password, request)
    response.set_cookie(name, value, max_age=30 * 86400, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path=f"/api/public/{token}")
    return {"ok": True}


@router.get("/api/public/{token}/views/{view_id}")
def share_view(token: str, view_id: str, request: Request):
    return _json(core_view(token, view_id, _cookie(request, token)))


@router.post("/api/public/{token}/views/{view_id}/records")
def share_create(token: str, view_id: str, body: PublicRecord, request: Request):
    return _json(core_create(token, view_id, body.data, body.table_id, _cookie(request, token)))


@router.patch("/api/public/{token}/views/{view_id}/records/{record_id}")
def share_update(token: str, view_id: str, record_id: str, body: PublicRecord, request: Request):
    return _json(core_update(token, view_id, record_id, body.data, _cookie(request, token)))


@router.delete("/api/public/{token}/views/{view_id}/records/{record_id}")
def share_delete(token: str, view_id: str, record_id: str, request: Request):
    core_delete(token, view_id, record_id, _cookie(request, token))
    return {"ok": True}


@router.get("/api/public/{token}/attachments/{attachment_id}")
def share_attachment(token: str, attachment_id: str, request: Request, download: bool = False):
    return core_attachment(token, attachment_id, _cookie(request, token), download)


@router.get("/api/public/{token}/files/{record_id}/content")
def share_file(token: str, record_id: str, request: Request, download: bool = False, text: bool = False):
    from . import files
    table, rec, keys = core_file(token, record_id, _cookie(request, token))
    return files.stream(table, rec, keys, download=download, as_text=text, rng=request.headers.get("range"))


@router.get("/api/public/{token}/files/{record_id}/preview")
def share_file_preview(token: str, record_id: str, request: Request):
    from . import files
    return files.preview_response(*core_file(token, record_id, _cookie(request, token)))


@router.get("/api/public/{token}/files/{record_id}/thumb")
def share_file_thumb(token: str, record_id: str, request: Request):
    from . import files
    core_file(token, record_id, _cookie(request, token))
    return files.thumb_response(record_id)


@router.post("/api/public/{token}/views/{view_id}/files")
def share_upload(token: str, view_id: str, request: Request, files: list[UploadFile] = File(...), folder: str = Form("")):
    return {"records": core_upload(token, view_id, files, folder, _cookie(request, token))}


@router.put("/api/public/{token}/views/{view_id}/files/{record_id}/file")
def share_replace(token: str, view_id: str, record_id: str, request: Request, file: UploadFile = File(...), new_ext: str = Form("")):
    return core_replace(token, view_id, record_id, file, _cookie(request, token), new_ext or None)


class PublicText(BaseModel):
    text: str


@router.put("/api/public/{token}/views/{view_id}/files/{record_id}/text")
def share_text(token: str, view_id: str, record_id: str, body: PublicText, request: Request):
    return core_text(token, view_id, record_id, body.text, _cookie(request, token))


@router.post("/api/public/{token}/views/{view_id}/vcards")
async def share_vcards_import(token: str, view_id: str, request: Request, files: list[UploadFile] = File(...), existing: str = Form("skip")):
    from . import contacts
    from starlette.concurrency import run_in_threadpool
    texts = await contacts.read_files(files)
    return await run_in_threadpool(core_vcards_import, token, view_id, texts, existing, _cookie(request, token))


@router.get("/api/public/{token}/views/{view_id}/vcards")
def share_vcards_export(token: str, view_id: str, request: Request):
    return core_vcards_export(token, view_id, _cookie(request, token))


# ======================================================================== PWA: manifest, icons, page

@lru_cache(maxsize=256)
def render_icon(letter: str, color: str, size: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    try:
        rgb = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        rgb = (99, 102, 241)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 5, fill=rgb + (255,))
    font = ImageFont.load_default(size=int(size * 0.55))
    d.text((size / 2, size / 2), letter.upper()[:1] or "P", fill="white", font=font, anchor="mm")
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _png(data: bytes):
    return Response(data, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/icon-{size}.png", include_in_schema=False)
def app_icon(size: int):
    return _png(render_icon("P", "#6366f1", max(16, min(size, 1024))))


@router.get("/manifest.webmanifest", include_in_schema=False)
def app_manifest():
    return JSONResponse({
        "name": "Probyte", "short_name": "Probyte", "start_url": "/", "scope": "/", "display": "standalone",
        "background_color": "#ffffff", "theme_color": "#6366f1",
        "icons": [{"src": f"/icon-{n}.png", "sizes": f"{n}x{n}", "type": "image/png", "purpose": "any maskable"} for n in (192, 512)],
    }, media_type="application/manifest+json")


@router.get("/s/{token}/icon-{size}.png", include_in_schema=False)
def share_icon(token: str, size: int):
    with connection() as conn:
        s = _load(conn, token)
    return _png(render_icon(s["name"][:1], s["color"], max(16, min(size, 1024))))


@router.get("/s/{token}/manifest.webmanifest", include_in_schema=False)
def share_manifest(token: str):
    with connection() as conn:
        s = _load(conn, token)
    return JSONResponse({
        "name": s["name"], "short_name": s["name"][:12], "start_url": f"/s/{token}", "scope": f"/s/{token}",
        "display": "standalone", "background_color": "#ffffff", "theme_color": s["color"],
        "icons": [{"src": f"/s/{token}/icon-{n}.png", "sizes": f"{n}x{n}", "type": "image/png", "purpose": "any maskable"} for n in (192, 512)],
    }, media_type="application/manifest+json")


def _escape(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


@router.get("/s/{token}", include_in_schema=False)
def share_page(token: str):
    """The app shell with this link's name, icon and manifest, so it installs as its own app."""
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(404)
    with connection() as conn:
        try:
            s = _load(conn, token)
            name, color = s["name"], s["color"]
        except HTTPException:
            name, color = "Link unavailable", "#6366f1"
    html = index.read_text()
    head = (f'<link rel="manifest" href="/s/{token}/manifest.webmanifest">'
            f'<link rel="apple-touch-icon" href="/s/{token}/icon-180.png">'
            f'<meta name="apple-mobile-web-app-title" content="{_escape(name)}">'
            f'<meta name="theme-color" content="{_escape(color)}">')
    html = html.replace('<link rel="manifest" href="/manifest.webmanifest">', "")
    html = html.replace('<link rel="apple-touch-icon" href="/icon-180.png">', "")
    html = html.replace('<meta name="theme-color" content="#6366f1">', "")
    html = html.replace("<title>Probyte</title>", f"<title>{_escape(name)}</title>{head}")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
