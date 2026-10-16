"""Records, attachments, bulk edits and imports."""
import re
from typing import Any, Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.blobs import attachments
from app.calc.compute import COMPUTED_TYPES, Engine
from app.calc.viewfilter import apply_view
from app.core.db import connection
from app.data import store, values
from app.sharing import recordops
from app.web.common import _record_out, FastJSON, RecordIn, User

router = APIRouter()


# ======================================================================== records API (keyed by field id or name)

def _by_name(fields, data: dict) -> dict:
    names = {str(f["id"]): f["name"] for f in fields}
    return {names.get(k, k): v for k, v in data.items()}


def _from_names(fields, data: dict) -> dict:
    ids = {f["name"]: str(f["id"]) for f in fields}
    return {ids.get(k, k): v for k, v in data.items()}


@router.get("/api/tables/{table_id}/records")
def api_list_records(table_id: str, field_key: Literal["id", "name"] = "id", view: str | None = None, user=User):
    """Records with computed values. ?view=<id> applies that view's filters and sort; ?field_key=name keys data by field name."""
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
        eng = Engine(conn)
        fields = eng.describe(table_id)
        records = eng.records(table_id)
        if view:
            v = conn.execute("SELECT config FROM views WHERE id = %s AND table_id = %s", (view, table_id)).fetchone() or store.not_found("View not found")
            records = apply_view(eng, fields, records, v["config"])
        out = [store.clean_record(r) for r in records]
    if field_key == "name":
        out = [{**r, "data": _by_name(fields, r["data"])} for r in out]
    return FastJSON({"records": out})


class RecordIds(BaseModel):
    ids: list[str]


@router.post("/api/tables/{table_id}/records/lookup")
def api_lookup_records(table_id: str, body: RecordIds, user=User):
    """Some of a table's records (up to 500, in the order asked), every field computed, with what's needed to show them:
    a link field's records shown as a list, a table or cards on another table's record."""
    ids = list(dict.fromkeys(str(i) for i in body.ids))[:500]
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
        eng = Engine(conn)
        order = {rid: n for n, rid in enumerate(ids)}
        recs = sorted((r for r in eng.records(table_id) if str(r["id"]) in order), key=lambda r: order[str(r["id"])])
        return FastJSON({"fields": eng.describe(table_id), "records": [store.clean_record(r) for r in recs],
                         "refs": eng.refs(table_id, recs)})


class RelatedIn(BaseModel):
    field: str            # this table's link field
    record: str           # the record (of the table it links to) whose related records these are
    view: str | None = None
    limit: int = 100


@router.post("/api/tables/{table_id}/records/related")
def api_related_records(table_id: str, body: RelatedIn, user=User):
    """The records of a table that link to a record through its link field: a page's related list (in a view's filter and
    order, when given), with every field computed and what's needed to show them."""
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
        eng = Engine(conn)
        fields = eng.describe(table_id)
        if not any(str(f["id"]) == body.field and f["type"] == "link" for f in fields):
            store.not_found("Link field not found")
        records = [r for r in eng.records(table_id) if body.record in (r["data"].get(body.field) or [])]
        if body.view:
            v = conn.execute("SELECT config FROM views WHERE id = %s AND table_id = %s", (body.view, table_id)).fetchone()
            if v:
                records = apply_view(eng, fields, records, v["config"])
        total = len(records)
        records = records[:max(1, min(body.limit, 500))]
        return FastJSON({"fields": fields, "records": [store.clean_record(r) for r in records], "refs": eng.refs(table_id, records),
                         "total": total})


@router.get("/api/records/{record_id}")
def api_get_record(record_id: str, field_key: Literal["id", "name"] = "id", user=User):
    with connection() as conn:
        r = conn.execute("SELECT table_id FROM records WHERE id = %s AND NOT deleted", (record_id,)).fetchone() or store.not_found("Record not found")
        store.get_table(conn, r["table_id"], user["id"])
        out = store.computed_record(conn, record_id, r["table_id"])
        fields = store.get_fields(conn, r["table_id"])
    return {**out, "data": _by_name(fields, out["data"])} if field_key == "name" else out


# ======================================================================== attachments & users

@router.post("/api/attachments")
async def upload_attachment(file: UploadFile = File(...), user=User):
    with connection() as conn:
        return await attachments.save_upload(conn, user["id"], file)


@router.get("/api/attachments/{attachment_id}")
def get_attachment(attachment_id: str, download: bool = False, user=User):
    with connection() as conn:
        return attachments.serve(conn, user["id"], attachment_id, download)


@router.get("/api/users/lookup")
def lookup_user(username: str, user=User):
    """Exact-username lookup for user fields (no listing, so accounts can't be enumerated)."""
    with connection() as conn:
        u = conn.execute("SELECT id, username FROM users WHERE username = %s", (username.strip().lower(),)).fetchone()
    if not u:
        raise HTTPException(404, "No user with that username")
    return u




# ======================================================================== records

@router.post("/api/tables/{table_id}/records")
def create_record(table_id: str, body: RecordIn, field_key: Literal["id", "name"] = "id", user=User):
    with connection() as conn:
        data = _from_names(store.get_fields(conn, table_id), body.data) if field_key == "name" else body.data
        rid, table = recordops.create(conn, table_id, data, user["id"], link_table_id=body.table_id)
        out = _record_out(conn, table_id, rid)
        if field_key == "name":
            out = {**out, "data": _by_name(store.get_fields(conn, table_id), out["data"])}
    return out


@router.patch("/api/records/{record_id}")
def update_record(record_id: str, body: RecordIn, field_key: Literal["id", "name"] = "id", user=User):
    with connection() as conn:
        data = body.data
        row = conn.execute("SELECT table_id FROM records WHERE id = %s AND NOT deleted", (record_id,)).fetchone() or store.not_found("Record not found")
        if field_key == "name":
            data = _from_names(store.get_fields(conn, row["table_id"]), data)
        recordops.update(conn, str(row["table_id"]), record_id, data, user["id"])  # files tables: renames / moves the file
        out = store.computed_record(conn, record_id, row["table_id"])
        if field_key == "name":
            out = {**out, "data": _by_name(store.get_fields(conn, row["table_id"]), out["data"])}
    return out


class OccurrenceIn(BaseModel):
    occurrence: str   # the date (record value form) of the repeat being done


@router.post("/api/records/{record_id}/complete")
def complete_occurrence(record_id: str, body: OccurrenceIn, user=User):
    """Mark one date of a repeating task done: the current one moves it on; a later one is kept as a done copy and
    skipped in the series."""
    with connection() as conn:
        store.complete_occurrence(conn, record_id, body.occurrence, user["id"])
        row = conn.execute("SELECT table_id FROM records WHERE id = %s", (record_id,)).fetchone()
        return store.computed_record(conn, record_id, row["table_id"])


class BulkItem(BaseModel):
    id: str
    table_id: str


class BulkIn(BaseModel):
    action: Literal["update", "delete"]
    items: list[BulkItem]
    data: dict[str, Any] = {}
    combo_id: str | None = None  # items come from this combined database: `data` uses its field ids


@router.post("/api/records/bulk")
def bulk_records(body: BulkIn, user=User):
    """Update or delete many records at once (any table kind, or a combined database's records)."""
    if len(body.items) > 5000:
        raise HTTPException(400, "Select at most 5000 records at a time")
    done, errors = 0, []
    with connection() as conn:
        combo = store.get_combo(conn, body.combo_id, user["id"]) if body.combo_id else None
        sources = {s["table_id"] for s in combo["sources"]} if combo else None
        for it in body.items:
            try:
                with conn.transaction():  # one bad record doesn't undo the others
                    if sources is not None and it.table_id not in sources:
                        store.not_found()
                    if body.action == "delete":
                        recordops.delete(conn, it.table_id, it.id, user["id"])
                    else:
                        data = store.unified_to_source(conn, combo, it.table_id, body.data) if combo else body.data
                        recordops.update(conn, it.table_id, it.id, data, user["id"])
                done += 1
            except HTTPException as e:
                errors.append({"id": it.id, "error": e.detail})
    return {"ok": not errors, "done": done, "errors": errors[:20]}


class ImportColumn(BaseModel):
    target: str | None = None                 # existing field id, or None to skip the column
    new: dict[str, str] | None = None         # {"name": ..., "type": ...}: create a field for it


class ImportIn(BaseModel):
    columns: list[ImportColumn]
    rows: list[list[Any]]


IMPORT_NEW_TYPES = {"text", "long_text", "number", "checkbox", "single_select", "multi_select", "date", "url", "email", "phone", "rating"}


@router.post("/api/tables/{table_id}/import")
def import_rows(table_id: str, body: ImportIn, user=User):
    """Add rows from a spreadsheet. Each column goes into an existing field, a new field, or nowhere."""
    if len(body.rows) > 50000:
        raise HTTPException(400, "That file has too many rows (50,000 at most)")
    with connection() as conn:
        table = store.get_table(conn, table_id, user["id"])
        existing = {str(f["id"]): f for f in store.get_fields(conn, table_id)}
        names = {f["name"].lower() for f in existing.values()}
        targets = []
        for col in body.columns:
            f = None
            if col.new:
                if table["kind"] == "remote":
                    raise HTTPException(400, "A linked table's fields come from its source; map columns to its existing fields")
                ftype = col.new.get("type") if col.new.get("type") in IMPORT_NEW_TYPES else "text"
                name, n = (col.new.get("name") or "Imported").strip()[:100] or "Imported", 2
                base = name
                while name.lower() in names:
                    name, n = f"{base} {n}", n + 1
                names.add(name.lower())
                raw_opts = {"choices": []} if ftype in store.SELECT_TYPES else {}
                if ftype == "date":  # keep times when the column has them
                    i = len(targets)
                    raw_opts["includeTime"] = any(isinstance(r[i] if i < len(r) else None, str) and re.search(r"[T ]\d{1,2}:\d{2}", r[i])
                                                  for r in body.rows[:500])
                opts = store.prepare_field(conn, table, ftype, raw_opts, user["id"])
                f = store.create_field(conn, table_id, name, ftype, opts)
            elif col.target:
                f = existing.get(col.target)
                if not f:
                    raise HTTPException(400, "A column is mapped to a field that no longer exists")
                if f["type"] in COMPUTED_TYPES or f["type"] == "attachment":
                    raise HTTPException(400, f"{f['name']} can't be filled from a file")
            targets.append(f)
        lookups = values.Lookups(conn)
        created, errors, live_fields = 0, [], None
        for n, row in enumerate(body.rows, start=1):
            data = {}
            for f, cell in zip(targets, row):
                if f is None or cell is None or (isinstance(cell, str) and not cell.strip()):
                    continue
                v = values.from_cell(f, cell, lookups)
                if v is not None:
                    data[str(f["id"])] = v
            if not data:
                continue
            if live_fields is None:  # once for the whole file (choices added on the way are kept in it)
                live_fields = store.get_fields(conn, table_id)
            try:
                with conn.transaction():
                    recordops.create(conn, table_id, data, user["id"], table=table, fields=live_fields)
                created += 1
            except HTTPException as e:
                errors.append(f"Row {n}: {e.detail}")
    # The fields the columns went to, so the rest of a large file can be sent in further requests.
    return {"created": created, "errors": errors[:20], "error_count": len(errors),
            "targets": [str(f["id"]) if f else None for f in targets]}


@router.patch("/api/tables/{table_id}/records/{record_id}")
def update_table_record(table_id: str, record_id: str, body: RecordIn, user=User):
    """Update a record of any table — local, CalDAV or linked."""
    with connection() as conn:
        recordops.update(conn, table_id, record_id, body.data, user["id"])
        out = _record_out(conn, table_id, record_id)
    return out


@router.delete("/api/tables/{table_id}/records/{record_id}")
def delete_table_record(table_id: str, record_id: str, user=User):
    with connection() as conn:
        recordops.delete(conn, table_id, record_id, user["id"])
    return {"ok": True}


@router.delete("/api/records/{record_id}")
def delete_record(record_id: str, user=User):
    with connection() as conn:
        store.delete_record(conn, record_id, user["id"])
    return {"ok": True}


# ======================================================================== paste (a block copied from a spreadsheet)

class PasteItem(BaseModel):
    record_id: str | None = None     # None: a new record (the block ran past the last row)
    values: dict[str, Any]           # field id -> the pasted text


class PasteIn(BaseModel):
    items: list[PasteItem]


@router.post("/api/tables/{table_id}/paste")
def paste(table_id: str, body: PasteIn, user=User):
    """Cells pasted into the grid: each text is converted to its field's type (as in an import); empty clears it.
    Cells of fields that can't be typed into (calculated, files, locked) are skipped."""
    if len(body.items) > 5000:
        raise HTTPException(400, "Paste at most 5000 rows at a time")
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
        fields = {str(f["id"]): f for f in store.get_fields(conn, table_id)}
        lookups = values.Lookups(conn)
        updated, created, errors = 0, [], []
        for n, it in enumerate(body.items, start=1):
            data = {}
            for fid, text in it.values.items():
                f = fields.get(fid)
                if not f or f["type"] in COMPUTED_TYPES or f["type"] in ("attachment", "button"):
                    continue
                blank = text is None or (isinstance(text, str) and not text.strip())
                data[fid] = None if blank else values.from_cell(f, text, lookups)
            if not data:
                continue
            try:
                with conn.transaction():
                    if it.record_id:
                        recordops.update(conn, table_id, it.record_id, data, user["id"])
                        updated += 1
                    else:
                        rid, _ = recordops.create(conn, table_id, {k: v for k, v in data.items() if v is not None}, user["id"])
                        created.append(str(rid))
            except HTTPException as e:
                errors.append(f"Row {n}: {e.detail}")
    return {"updated": updated, "created": created, "errors": errors[:20]}


# ======================================================================== history

@router.get("/api/records/{record_id}/history")
def record_history(record_id: str, user=User):
    """Who changed what in a record, newest first (kept 180 days)."""
    with connection() as conn:
        rec = conn.execute("SELECT table_id FROM records WHERE id = %s UNION SELECT table_id FROM record_history WHERE record_id = %s "
                           "LIMIT 1", (record_id, record_id)).fetchone()
        if not rec:
            store.not_found("Record not found")
        store.get_table(conn, rec["table_id"], user["id"], trashed=True)
        rows = conn.execute("SELECT at, actor, action, changes, table_id FROM record_history WHERE record_id = %s "
                            "ORDER BY at DESC, id DESC LIMIT 200", (record_id,)).fetchall()
        if not rows:
            return {"entries": []}
        fields = {str(f["id"]): f for f in store.get_fields(conn, rows[0]["table_id"])}
    out = []
    for h in rows:
        changes = [{"field_id": fid, "field": fields[fid]["name"] if fid in fields else "(deleted field)", "old": old, "new": new}
                   for fid, (old, new) in h["changes"].items()]
        changes.sort(key=lambda c: list(fields).index(c["field_id"]) if c["field_id"] in fields else 999)
        out.append({"at": h["at"], "actor": h["actor"], "action": h["action"], "changes": changes})
    return {"entries": out}
