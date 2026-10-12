"""Storage connections: the S3 buckets and WebDAV folders files tables show (secrets encrypted)."""

from fastapi import HTTPException
from pydantic import BaseModel

from app.blobs.filestore import driver, StorageError
from app.core.secretbox import seal
from app.data import store

class StorageIn(BaseModel):
    name: str | None = None
    kind: str = "s3"
    endpoint: str = ""
    region: str = ""
    bucket: str = ""
    username: str = ""
    secret: str | None = None      # empty when editing = keep the saved one
    path_style: bool = True
    prefix: str = ""               # only for testing: the folder a table would use


def storage_out(s) -> dict:
    return {k: (str(s[k]) if k == "id" else s[k]) for k in ("id", "name", "kind", "endpoint", "region", "bucket", "username", "path_style")} \
        | {"has_secret": bool(s["secret"])}


def _storage_row(body: StorageIn, old=None) -> dict:
    if body.kind not in ("s3", "webdav"):
        raise HTTPException(400, "Choose S3 or WebDAV")
    if body.kind == "webdav" and not body.endpoint.strip():
        raise HTTPException(400, "Enter the WebDAV folder's address")
    secret = seal(body.secret) if body.secret else (old["secret"] if old else "")
    name = (body.name or "").strip() or (body.bucket.strip() if body.kind == "s3" else body.endpoint.strip())[:80] or "Storage"
    return {"name": name[:100], "kind": body.kind, "endpoint": body.endpoint.strip(), "region": body.region.strip(),
            "bucket": body.bucket.strip(), "username": body.username.strip(), "secret": secret, "path_style": body.path_style}


def test_storage(row: dict, prefix: str = ""):
    try:
        driver(row, prefix).test()
    except StorageError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Can't connect: {e}")


def save_storage(conn, user_id, body: StorageIn) -> dict:
    row = _storage_row(body)
    test_storage(row, body.prefix)
    return conn.execute("INSERT INTO storages (user_id, name, kind, endpoint, region, bucket, username, secret, path_style) "
                        "VALUES (%(u)s, %(name)s, %(kind)s, %(endpoint)s, %(region)s, %(bucket)s, %(username)s, %(secret)s, %(path_style)s) RETURNING *",
                        {**row, "u": user_id}).fetchone()


def own_storage(conn, storage_id, user_id):
    return conn.execute("SELECT * FROM storages WHERE id = %s AND user_id = %s", (storage_id, user_id)).fetchone() \
        or store.not_found("Storage connection not found")


def _storage(conn, table):
    s = conn.execute("SELECT * FROM storages WHERE id = %s", (table["storage_id"],)).fetchone() if table.get("storage_id") else None
    return driver(s, table.get("storage_prefix"))
