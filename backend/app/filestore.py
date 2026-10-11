"""Where a files table's files live: an S3 bucket (AWS, MinIO, Cloudflare R2, Backblaze B2, Wasabi…) or a WebDAV
folder (Nextcloud, ownCloud, a NAS…). Both drivers offer the same small set of operations on paths relative to the
table's folder ("prefix"): list everything, read (with byte ranges, for video), write, delete and move.
"""
import mimetypes
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import BinaryIO, Iterator
from urllib.parse import quote, unquote, urlparse

import requests

from .dav import D, NS, TIMEOUT, DavError, check_url, q, responses
from .secretbox import unseal

MAX_OBJECTS = 100_000   # a sync lists at most this many files


class StorageError(Exception):
    pass


def norm_prefix(prefix: str | None) -> str:
    """'photos/2026' -> 'photos/2026/'; '' stays '' (the whole bucket / folder)."""
    p = "/".join(x for x in (prefix or "").replace("\\", "/").split("/") if x and x not in (".", ".."))
    return p + "/" if p else ""


def clean_rel(path: str) -> str:
    """A file's path inside the table's folder, without empty, '.' or '..' parts."""
    parts = [x for x in (path or "").replace("\\", "/").split("/") if x and x not in (".", "..")]
    if not parts:
        raise StorageError("The file needs a name")
    if any(len(x) > 255 or any(ord(c) < 32 for c in x) for x in parts):
        raise StorageError("That name can't be used for a file")
    return "/".join(parts)


def guess_mime(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


class S3:
    def __init__(self, s: dict, prefix: str):
        import boto3
        from botocore.config import Config
        self.bucket = s["bucket"].strip()
        if not self.bucket:
            raise StorageError("Enter the bucket name")
        self.prefix = norm_prefix(prefix)
        endpoint = s["endpoint"].strip() or None
        try:
            if endpoint:
                check_url(endpoint)
        except DavError as e:
            raise StorageError(str(e).replace("calendar server", "storage server"))
        self.c = boto3.client(
            "s3", endpoint_url=endpoint, region_name=s["region"].strip() or "us-east-1",
            aws_access_key_id=s["username"] or None, aws_secret_access_key=unseal(s["secret"]) or None,
            config=Config(s3={"addressing_style": "path" if s["path_style"] else "virtual"}, signature_version="s3v4",
                          connect_timeout=TIMEOUT[0], read_timeout=TIMEOUT[1], retries={"max_attempts": 2}))

    def _err(self, e) -> StorageError:
        code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        msg = {"NoSuchBucket": f"There's no bucket called {self.bucket}", "InvalidAccessKeyId": "The access key isn't valid",
               "SignatureDoesNotMatch": "The secret key doesn't match the access key", "AccessDenied": "Access denied by the storage"}.get(code)
        return StorageError(msg or f"Storage error: {e}")

    def list(self) -> Iterator[dict]:
        n = 0
        try:
            for page in self.c.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self.prefix):
                for o in page.get("Contents", []):
                    rel = o["Key"][len(self.prefix):]
                    if not rel or rel.endswith("/"):
                        continue  # folder markers
                    n += 1
                    if n > MAX_OBJECTS:
                        return
                    yield {"path": rel, "size": o["Size"], "etag": o["ETag"].strip('"'), "modified": o["LastModified"]}
        except Exception as e:
            raise self._err(e)

    def head(self, rel: str) -> dict | None:
        try:
            o = self.c.head_object(Bucket=self.bucket, Key=self.prefix + rel)
        except Exception as e:
            if getattr(e, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise self._err(e)
        return {"path": rel, "size": o["ContentLength"], "etag": o["ETag"].strip('"'), "modified": o["LastModified"],
                "mime": o.get("ContentType")}

    def get(self, rel: str, rng: str | None = None):
        """(status, headers, chunks) for streaming a file to the browser."""
        try:
            o = self.c.get_object(Bucket=self.bucket, Key=self.prefix + rel, **({"Range": rng} if rng else {}))
        except Exception as e:
            if getattr(e, "response", {}).get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                raise FileNotFoundError(rel)
            if getattr(e, "response", {}).get("Error", {}).get("Code") == "InvalidRange":
                return 416, {}, iter(())
            raise self._err(e)
        headers = {"Content-Length": str(o["ContentLength"])}
        if o.get("ContentRange"):
            headers["Content-Range"] = o["ContentRange"]
        return (206 if o.get("ContentRange") else 200), headers, o["Body"].iter_chunks(256 * 1024)

    def read(self, rel: str, limit: int) -> bytes:
        status, _, chunks = self.get(rel, f"bytes=0-{limit - 1}")
        return b"".join(chunks)[:limit] if status != 416 else b""

    def put(self, rel: str, f: BinaryIO, mime: str) -> dict:
        try:
            self.c.upload_fileobj(f, self.bucket, self.prefix + rel, ExtraArgs={"ContentType": mime})
        except Exception as e:
            raise self._err(e)
        return self.head(rel) or {"path": rel, "size": 0, "etag": "", "modified": datetime.now(timezone.utc)}

    def delete(self, rel: str):
        try:
            self.c.delete_object(Bucket=self.bucket, Key=self.prefix + rel)
        except Exception as e:
            raise self._err(e)

    def move(self, src: str, dst: str) -> dict:
        try:
            self.c.copy({"Bucket": self.bucket, "Key": self.prefix + src}, self.bucket, self.prefix + dst)
            self.c.delete_object(Bucket=self.bucket, Key=self.prefix + src)
        except Exception as e:
            raise self._err(e)
        return self.head(dst)

    def test(self):
        try:
            self.c.list_objects_v2(Bucket=self.bucket, Prefix=self.prefix, MaxKeys=1)
        except Exception as e:
            raise self._err(e)


class WebDAV:
    def __init__(self, s: dict, prefix: str):
        try:
            root = check_url(s["endpoint"])
        except DavError as e:
            raise StorageError(str(e).replace("calendar server", "storage server"))
        self.root = root.rstrip("/") + "/"
        self.base = self.root + quote(norm_prefix(prefix))
        self.base_path = unquote(urlparse(self.base).path)
        self.s = requests.Session()
        if s["username"]:
            self.s.auth = (s["username"], unseal(s["secret"]) or "")
        self.s.headers["User-Agent"] = "Probyte"

    def url(self, rel: str) -> str:
        return self.base + quote(rel)

    def _req(self, method, url, ok=(200, 201, 204, 207), **kw):
        try:
            r = self.s.request(method, url, timeout=TIMEOUT, **kw)
        except requests.RequestException as e:
            raise StorageError(f"Can't reach the storage server: {e.__class__.__name__}")
        if r.status_code == 401:
            raise StorageError("The server rejected the username or password")
        if r.status_code == 404 and 404 not in ok:
            raise FileNotFoundError(url)
        if r.status_code not in ok:
            raise StorageError(f"{method} {unquote(urlparse(url).path)} answered HTTP {r.status_code}")
        return r

    def _entry(self, href: str, props: dict) -> dict | None:
        path = unquote(urlparse(href).path)
        if not path.startswith(self.base_path):
            return None
        rel = path[len(self.base_path):].strip("/")
        rt = props.get(q(D, "resourcetype"))
        is_dir = rt is not None and rt.find(q(D, "collection")) is not None
        lm = props.get(q(D, "getlastmodified"))
        try:
            modified = parsedate_to_datetime(lm.text) if lm is not None and lm.text else None
        except (TypeError, ValueError):
            modified = None
        size = props.get(q(D, "getcontentlength"))
        etag = props.get(q(D, "getetag"))
        ctype = props.get(q(D, "getcontenttype"))
        return {"path": rel, "dir": is_dir, "size": int(size.text) if size is not None and (size.text or "").isdigit() else 0,
                "etag": (etag.text or "").strip('"').removeprefix("W/").strip('"') if etag is not None else "",
                "modified": modified, "mime": ctype.text if ctype is not None else None}

    def _propfind(self, url: str, depth: str):
        body = (f'<?xml version="1.0"?><d:propfind {NS}><d:prop><d:resourcetype/><d:getcontentlength/><d:getetag/>'
                '<d:getlastmodified/><d:getcontenttype/></d:prop></d:propfind>')
        r = self._req("PROPFIND", url, data=body.encode(), headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"},
                      ok=(207,))
        return [e for e in (self._entry(h, p) for h, p in responses(r.content)) if e]

    def list(self) -> Iterator[dict]:
        todo, seen, n = [""], set(), 0
        while todo:
            folder = todo.pop()
            if folder in seen or len(seen) > 5000:
                continue
            seen.add(folder)
            for e in self._propfind(self.base + quote(folder + "/" if folder else ""), "1"):
                if e["path"] == folder:
                    continue
                if e["dir"]:
                    todo.append(e["path"])
                else:
                    n += 1
                    if n > MAX_OBJECTS:
                        return
                    if not e["etag"]:
                        e["etag"] = f"{e['size']}-{e['modified'].timestamp() if e['modified'] else ''}"
                    yield e

    def head(self, rel: str) -> dict | None:
        try:
            got = self._propfind(self.url(rel), "0")
        except FileNotFoundError:
            return None
        e = got[0] if got else None
        if e and not e["etag"]:
            e["etag"] = f"{e['size']}-{e['modified'].timestamp() if e['modified'] else ''}"
        return e

    def get(self, rel: str, rng: str | None = None):
        r = self._req("GET", self.url(rel), ok=(200, 206, 416), stream=True, headers={"Range": rng} if rng else {})
        headers = {k: r.headers[k] for k in ("Content-Length", "Content-Range") if k in r.headers}
        return r.status_code, headers, r.iter_content(256 * 1024)

    def read(self, rel: str, limit: int) -> bytes:
        status, _, chunks = self.get(rel, f"bytes=0-{limit - 1}")
        out = b""
        for c in chunks:
            out += c
            if len(out) >= limit:
                break
        return out[:limit] if status != 416 else b""

    def _mkdirs(self, rel: str):
        parts = rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            self._req("MKCOL", self.url("/".join(parts[:i]) + "/"), ok=(201, 405, 301, 200))  # 405: it exists

    def put(self, rel: str, f: BinaryIO, mime: str) -> dict:
        self._mkdirs(rel)
        self._req("PUT", self.url(rel), data=f, headers={"Content-Type": mime})
        return self.head(rel) or {"path": rel, "size": 0, "etag": "", "modified": datetime.now(timezone.utc)}

    def delete(self, rel: str):
        self._req("DELETE", self.url(rel), ok=(200, 204, 404))

    def move(self, src: str, dst: str) -> dict:
        self._mkdirs(dst)
        self._req("MOVE", self.url(src), headers={"Destination": self.url(dst), "Overwrite": "F"}, ok=(201, 204))
        return self.head(dst)

    def test(self):
        try:
            self._propfind(self.base, "0")
        except FileNotFoundError:
            # a new folder for the table: made inside the connection's folder, which has to exist
            try:
                self._propfind(self.root, "0")
            except FileNotFoundError:
                raise StorageError("That folder doesn't exist on the server")
            parts = [x for x in self.base[len(self.root):].split("/") if x]
            for i in range(1, len(parts) + 1):
                self._req("MKCOL", self.root + "/".join(parts[:i]) + "/", ok=(201, 405, 301, 200))


def driver(storage: dict, prefix: str | None):
    if not storage:
        raise StorageError("This table's storage connection was deleted")
    return S3(storage, prefix or "") if storage["kind"] == "s3" else WebDAV(storage, prefix or "")
