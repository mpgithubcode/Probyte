"""The HTTP routes of files tables: a file's contents, preview and cover; uploads and new contents; storage connections."""

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.core.db import connection
from app.data import store
from app.files.catalog import _check_size, _own_record, preview_response, replace_contents, save_text_contents, stream, thumb_response, upload_files, upload_mime
from app.files.storages import _storage_row, own_storage, save_storage, storage_out, StorageIn, test_storage
from app.identity.auth import User

router = APIRouter()


@router.get("/api/files/{record_id}/content")
def file_content(record_id: str, request: Request, download: bool = False, text: bool = False, user=User):
    with connection() as conn:
        table, rec, keys = _own_record(conn, record_id, user["id"])
    return stream(table, rec, keys, download=download, as_text=text, rng=request.headers.get("range"))


@router.get("/api/files/{record_id}/preview")
def file_preview(record_id: str, user=User):
    """Word / PowerPoint / OpenDocument files as PDF, to view them in the browser."""
    with connection() as conn:
        table, rec, keys = _own_record(conn, record_id, user["id"])
    return preview_response(table, rec, keys)


@router.get("/api/files/{record_id}/thumb")
def file_thumb(record_id: str, user=User):
    with connection() as conn:
        _own_record(conn, record_id, user["id"])
    return thumb_response(record_id)


@router.post("/api/tables/{table_id}/files")
def upload(table_id: str, files: list[UploadFile] = File(...), folder: str = Form(""), user=User):
    """Add files (to a folder inside the table's folder). A name that's taken gets a number: report (2).pdf."""
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
    return {"records": upload_files(table_id, files, folder, user["id"])}


@router.put("/api/records/{record_id}/file")
def replace_file(record_id: str, file: UploadFile = File(...), user=User):
    """New contents for a file; its name, folder, links and your own fields stay."""
    _check_size(file)
    with connection() as conn:
        table, rec, keys = _own_record(conn, record_id, user["id"])
    return replace_contents(table, rec, keys, file.file, upload_mime(file))


class TextIn(BaseModel):
    text: str


@router.put("/api/files/{record_id}/text")
def save_text(record_id: str, body: TextIn, user=User):
    """Save a text file edited in the preview."""
    with connection() as conn:
        table, rec, keys = _own_record(conn, record_id, user["id"])
    return save_text_contents(table, rec, keys, body.text)


@router.get("/api/storages")
def list_storages(user=User):
    with connection() as conn:
        rows = conn.execute("SELECT s.*, (SELECT count(*) FROM tables t WHERE t.storage_id = s.id) AS tables FROM storages s "
                            "WHERE s.user_id = %s ORDER BY s.name", (user["id"],)).fetchall()
    return [storage_out(s) | {"tables": s["tables"]} for s in rows]


@router.post("/api/storages")
def create_storage(body: StorageIn, user=User):
    with connection() as conn:
        return storage_out(save_storage(conn, user["id"], body))


@router.post("/api/storages/test")
def test_storage_route(body: StorageIn, storage_id: str | None = None, user=User):
    """Check a connection before saving it (with an id: test changes to a saved one)."""
    with connection() as conn:
        old = own_storage(conn, storage_id, user["id"]) if storage_id else None
    test_storage(_storage_row(body, old), body.prefix)
    return {"ok": True}


@router.patch("/api/storages/{storage_id}")
def update_storage(storage_id: str, body: StorageIn, user=User):
    with connection() as conn:
        row = _storage_row(body, own_storage(conn, storage_id, user["id"]))
        test_storage(row)
        s = conn.execute("UPDATE storages SET name = %(name)s, kind = %(kind)s, endpoint = %(endpoint)s, region = %(region)s, "
                         "bucket = %(bucket)s, username = %(username)s, secret = %(secret)s, path_style = %(path_style)s "
                         "WHERE id = %(id)s RETURNING *", {**row, "id": storage_id}).fetchone()
    return storage_out(s)


@router.delete("/api/storages/{storage_id}")
def delete_storage(storage_id: str, user=User):
    with connection() as conn:
        own_storage(conn, storage_id, user["id"])
        n = conn.execute("SELECT count(*) AS n FROM tables WHERE storage_id = %s", (storage_id,)).fetchone()["n"]
        if n:
            raise HTTPException(400, f"{n} table{'s use' if n > 1 else ' uses'} this connection; delete {'them' if n > 1 else 'it'} first")
        conn.execute("DELETE FROM storages WHERE id = %s", (storage_id,))
    return {"ok": True}
