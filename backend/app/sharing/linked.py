"""Linked tables: a table in your base whose data comes live from a Probyte public link —
on this instance or on any other Probyte server.

Only what the link exposes ever arrives (its views' visible fields and filtered records), and
writes are forwarded to the link, so the link owner's permissions always apply.
Links to this same instance are served directly, without going over the network.
"""
import hashlib
import ipaddress
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

from app.calc import compute
from app.core.db import Jsonb

# Fetching user-supplied URLs from the server: refuse private / internal addresses unless allowed
# (e.g. for Probyte servers on your own LAN), so the feature can't be used to probe the network.
ALLOW_PRIVATE = os.environ.get("REMOTE_ALLOW_PRIVATE", "false").lower() in ("1", "true", "yes")
CACHE_SECONDS = float(os.environ.get("REMOTE_CACHE_SECONDS", "10"))
TIMEOUT = 20
MAX_BYTES = 50 * 1024 * 1024
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# ======================================================================== links & transport

def parse_link(url: str) -> dict:
    u = urlparse((url or "").strip())
    parts = u.path.strip("/").split("/")
    if u.scheme not in ("http", "https") or not u.netloc or len(parts) < 2 or parts[0] != "s" or not parts[1]:
        raise HTTPException(400, "Paste a Probyte public link, like https://example.com/s/AbC123")
    return {"origin": f"{u.scheme}://{u.netloc}", "token": parts[1], "view_id": u.fragment.lstrip("/") or None}


def is_local(conn, token: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM shares WHERE token = %s", (token,)).fetchone())


def _check_host(origin: str):
    host = urlparse(origin).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        raise HTTPException(400, f"Can't find the server {host}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ALLOW_PRIVATE and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                                  or ip.is_multicast or ip.is_unspecified):
            raise HTTPException(400, f"{host} is on a private network address. To link Probyte servers on your own network, "
                                     "set REMOTE_ALLOW_PRIVATE=true on this server.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # redirects could lead to internal addresses: don't follow them


def _open(method, url, body=None, cookie=None):
    _check_host(url)
    headers = {"Accept": "application/json", "User-Agent": "Probyte-link/1"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    req = urllib.request.Request(url, method=method, headers=headers, data=json.dumps(body).encode() if body is not None else None)
    host = urlparse(url).hostname
    try:
        return urllib.request.build_opener(_NoRedirect).open(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            raise HTTPException(502, "The link redirects somewhere else — paste the final address instead")
        try:
            detail = json.loads(e.read(65536)).get("detail", e.reason)
        except Exception:
            detail = e.reason
        if e.code == 401 and detail == "password_required":
            raise HTTPException(401, "password_required")
        raise HTTPException(e.code if e.code in (400, 401, 403, 404) else 502, f"{detail}" if e.code in (401, 403, 404) else f"The source server said: {detail}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HTTPException(502, f"Couldn't reach {host}: {getattr(e, 'reason', e)}")


def _http(method, link, path, body=None):
    with _open(method, link["origin"] + path, body, link.get("cookie")) as r:
        raw = r.read(MAX_BYTES + 1)
        cookie = r.headers.get("Set-Cookie")
    if len(raw) > MAX_BYTES:
        raise HTTPException(502, "The linked table is too large to load")
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(502, "That address doesn't look like a Probyte link")
    return data, cookie


def _cookie_value(link) -> str | None:
    c = link.get("cookie")
    return c.split("=", 1)[1] if c and "=" in c else None


def _base(link) -> str:
    return f"/api/public/{quote(link['token'], safe='')}"


# ---- operations on a link (local fast path or HTTP)

def fetch_meta(conn, link) -> dict:
    from app.sharing import links as shares
    if link.get("local"):
        return shares.core_meta(link["token"], _cookie_value(link))
    data, _ = _http("GET", link, _base(link))
    if not isinstance(data, dict) or "items" not in data:
        raise HTTPException(502, "That address doesn't look like a Probyte link")
    return data


def unlock(conn, link, password: str) -> str:
    """Enter the link's password; returns the access cookie ('name=value') to store."""
    from app.sharing import links as shares
    if link.get("local"):
        name, value = shares.core_unlock(link["token"], password)
        return f"{name}={value}"
    _, set_cookie = _http("POST", link, _base(link) + "/unlock", {"password": password})
    if not set_cookie:
        raise HTTPException(502, "The source server didn't accept the password")
    return set_cookie.split(";", 1)[0].strip()


def fetch_view(conn, link, view_id) -> dict:
    from app.sharing import links as shares
    if link.get("local"):
        return shares.core_view(link["token"], view_id, _cookie_value(link))
    data, _ = _http("GET", link, f"{_base(link)}/views/{quote(str(view_id), safe='')}")
    return data


def _write(link, method, suffix, body=None, view_id=None):
    """A write through the link: through view_id (one of its views the record shows in), else the link's own view."""
    from app.sharing import links as shares
    tok, cookie = link["token"], _cookie_value(link)
    vid = str(view_id or link["view_id"])
    if link.get("local"):
        parts = suffix.strip("/").split("/")  # records[/<id>]
        if method == "POST":
            return shares.core_create(tok, vid, body["data"], body.get("table_id"), cookie)
        if method == "PATCH":
            return shares.core_update(tok, vid, parts[1], body["data"], cookie)
        shares.core_delete(tok, vid, parts[1], cookie)
        return {"ok": True}
    data, _ = _http(method, link, f"{_base(link)}/views/{quote(vid, safe='')}/{suffix}", body)
    return data


VIEWS_FIELD = "__views"   # a linked table's own field: which of the source's views each record is in


def fetch_all(conn, link) -> list[dict]:
    """The link's own view, then every other view of the same table that the link shares (each brings the fields it
    shows and the records its filter lets through). A view gone from the link since is skipped."""
    first = fetch_view(conn, link, link["view_id"])
    others = [str(v["id"]) for v in first.get("views") or [] if str(v["id"]) != str(link["view_id"])]
    if not others or first.get("record") or link.get("all_views") is False:
        return [first]

    def one(vid):
        try:
            return fetch_view(conn, link, vid)
        except HTTPException as e:
            if e.status_code == 404:
                return None
            raise
    if link.get("local"):
        rest = [one(v) for v in others]
    else:   # another server: the views at once
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(others))) as ex:
            rest = list(ex.map(one, others))
    return [first, *[p for p in rest if p]]


def _view_names(payloads) -> dict:
    """Each source view's name as its records are marked with it (a repeated name gets a number)."""
    out, used = {}, {}
    for p in payloads:
        n = (p.get("view") or {}).get("name") or "View"
        used[n] = used.get(n, 0) + 1
        out[str(p["view"]["id"])] = n if used[n] == 1 else f"{n} ({used[n]})"
    return out


def merge(payloads) -> dict:
    """One payload from several views of the same source table: every field any of them shows, every record any of
    them lets through (a record in several views once), and, when there are several, which views each record is in
    (the VIEWS_FIELD), so each mirrored view shows just its own."""
    if len(payloads) == 1:
        return {**payloads[0], "_via": {str(r["id"]): [str(payloads[0]["view"]["id"])] for r in payloads[0].get("records") or []},
                "_view_fields": {str(payloads[0]["view"]["id"]): {str(f["id"]) for f in payloads[0].get("fields") or []}}}
    names = _view_names(payloads)
    fields, seen = [], set()
    for p in payloads:
        for f in p.get("fields") or []:
            if str(f["id"]) not in seen:
                seen.add(str(f["id"]))
                fields.append(f)
    palette = ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#a855f7", "#14b8a6", "#ec4899", "#64748b"]
    fields.append({"id": VIEWS_FIELD, "name": "Source views", "type": "multi_select", "readonly": True,
                   "options": {"choices": [{"name": n, "color": palette[i % len(palette)]} for i, n in enumerate(names.values())]}})
    records, via = {}, {}
    refs = {"records": {}, "users": {}}
    for p in payloads:
        vid = str(p["view"]["id"])
        for r in p.get("records") or []:
            rid = str(r["id"])
            cur = records.get(rid)
            if cur is None:
                cur = records[rid] = {**r, "data": dict(r.get("data") or {})}
                cur["data"][VIEWS_FIELD] = []
            else:
                cur["data"].update(r.get("data") or {})
            cur["data"][VIEWS_FIELD].append(names[vid])
            via.setdefault(rid, []).append(vid)
        pr = p.get("refs") or {}
        for k in ("records", "users"):
            refs[k].update(pr.get(k) or {})
        for k in ("display", "chips"):
            for fid, d in (pr.get(k) or {}).items():
                cur = refs.setdefault(k, {}).setdefault(fid, {**d, "values": {}})
                cur["values"].update(d.get("values") or {})
    return {**payloads[0], "fields": fields, "records": list(records.values()), "refs": refs, "_via": via,
            "_view_fields": {str(p["view"]["id"]): {str(f["id"]) for f in p.get("fields") or []} for p in payloads}}


# ======================================================================== mirroring into a local table

REC_ID = "__record_id"  # a linked table's own Record ID field (the source's record ids)


def lid(table_id, remote_id) -> str:
    """Stable local id for a remote field/record (so the same table linked twice never collides)."""
    return str(uuid.uuid5(uuid.UUID(str(table_id)), str(remote_id)))


def _parse_dt(v):
    if isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return EPOCH


def map_view_config(table_id, cfg: dict) -> dict:
    """A remote view's settings, re-pointed at the mirrored fields."""
    m = lambda x: lid(table_id, x) if x else x  # noqa: E731
    out = dict(cfg)
    if isinstance(out.get("sort"), dict) and not isinstance(out.get("sorts"), list):  # a source server from before multi-sort
        out["sorts"] = [out["sort"]]
    out.pop("sort", None)
    for k in ("groupBy", "dateField", "endField", "colorField", "coverField"):
        if out.get(k):
            out[k] = m(out[k])
    out["hidden"] = [m(x) for x in out.get("hidden") or []]
    out["shown"] = [m(x) for x in out.get("shown") or []]
    for k in ("sorts", "groups"):
        if isinstance(out.get(k), list):
            out[k] = [{**x, "field": m(x.get("field"))} for x in out[k] if isinstance(x, dict)]
    out.pop("filters", None)  # the source already applied its filters
    out.pop("filter", None)
    if out.get("chart"):
        out["chart"] = {**out["chart"], "xField": m(out["chart"].get("xField")), "yField": m(out["chart"].get("yField"))}
    return out


def sync_fields(conn, table, payload) -> str:
    """Create/update/remove the local mirror of the link's fields. Returns the fields signature."""
    tid = str(table["id"])
    desired = []
    for i, f in enumerate(payload.get("fields") or []):
        opts = dict(f.get("options") or {})
        opts["_remote_id"] = str(f["id"])
        opts["_readonly"] = bool(f.get("readonly"))
        if f.get("system_key") and f["system_key"] not in ("remote", "record_id"):
            opts["_source_key"] = f["system_key"]  # its built-in role there (a task's status, a file's File…): the app acts on it
        for k in ("description", "required", "unique_values"):
            if f.get(k):
                opts["_" + k] = f[k]
        if f["type"] == "button" and opts.get("updates"):
            opts["updates"] = [{**u, "fieldId": lid(tid, u["fieldId"])} for u in opts["updates"]]
        desired.append([lid(tid, f["id"]), f["name"], f["type"], opts, bool(f.get("is_primary")), i])
    if not any(d[2] == "record_id" for d in desired):  # the source's record ids, even when its view doesn't show them
        desired.append([lid(tid, REC_ID), "Record ID", "record_id", {"_remote_id": REC_ID, "_readonly": True}, False, len(desired)])
    sig = hashlib.sha256(json.dumps(desired, sort_keys=True, default=str).encode()).hexdigest()
    if (table.get("remote") or {}).get("fields_hash") == sig:
        return sig
    ids = [d[0] for d in desired]
    conn.execute("DELETE FROM fields WHERE table_id = %s AND system_key = 'remote' AND NOT (id = ANY(%s::uuid[]))", (tid, ids))
    for fid, name, ftype, opts, primary, pos in desired:
        conn.execute(
            "INSERT INTO fields (id, table_id, name, type, options, system_key, is_primary, position) "
            "VALUES (%s,%s,%s,%s,%s,'remote',%s,%s) ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, type = EXCLUDED.type, "
            "options = EXCLUDED.options, is_primary = EXCLUDED.is_primary, position = EXCLUDED.position",
            (fid, tid, name, ftype, Jsonb(opts), primary, pos))
    return sig


def _convert(tid, payload) -> dict:
    fmap = {str(f["id"]): lid(tid, f["id"]) for f in payload.get("fields") or []}
    files = {str(f["id"]) for f in payload.get("fields") or [] if f["type"] == "attachment"}
    own_rid = not any(f["type"] == "record_id" for f in payload.get("fields") or [])  # (see sync_fields)
    records, reverse = [], {}
    for r in payload.get("records") or []:
        rid = lid(tid, r["id"])
        reverse[rid] = str(r["id"])
        data = {}
        for k, v in (r.get("data") or {}).items():
            if k not in fmap:
                continue
            if k in files and isinstance(v, list):
                # through this server: an attachment's own URL; a file of a files table, the base of its URLs
                # (…/content, …/thumb, …/preview), see file() below
                v = [{**a, "url": f"/api/remote/{tid}/files/{a['id']}" if a.get("file") else f"/api/remote/{tid}/attachments/{a['id']}"}
                     for a in v if isinstance(a, dict) and a.get("id")]
            data[fmap[k]] = v
        if own_rid:
            data[lid(tid, REC_ID)] = str(r["id"])
        created = _parse_dt(r.get("created_at"))
        records.append({"id": rid, "source_id": str(r["id"]), "table_id": tid, "data": data, "sync_status": "synced", "created_at": created,
                        "updated_at": created, "seq": None, "created_by": None, "updated_by": None})
    via = {lid(tid, k): v for k, v in (payload.get("_via") or {}).items()}
    return {"records": records, "reverse": reverse, "refs": payload.get("refs") or {"records": {}, "users": {}},
            "sources": payload.get("sources"), "via": via, "view_fields": payload.get("_view_fields") or {}}


# ======================================================================== cache & reading

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def invalidate(table_id):
    with _lock:
        _cache.pop(str(table_id), None)


def load(conn, table, force=False) -> dict:
    """Converted records/refs of a linked table, cached for a few seconds."""
    tid = str(table["id"])
    with _lock:
        hit = _cache.get(tid)
    if hit and not force and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    link = dict(table["remote"] or {})
    error, perms, name = None, link.get("permissions") or {}, link.get("name")
    source_kind = link.get("source_kind")
    try:
        payloads = fetch_all(conn, link)
        payload = merge(payloads)
        sig = sync_fields(conn, table, payload)
        perms = payload.get("permissions", perms)
        name = payload.get("link_name", name)
        source_kind = (payload.get("item") or {}).get("table_kind") or link.get("source_kind")
        entry = _convert(tid, payload)
        if link.get("follow_views", True):
            _follow_views(conn, table, link, payloads, payload)
    except HTTPException as e:
        error = "The link needs its password again (it may have changed)" if e.detail == "password_required" else str(e.detail)
        sig = link.get("fields_hash")
        empty = {"records": [], "reverse": {}, "refs": {"records": {}, "users": {}}, "sources": None, "via": {}, "view_fields": {}}
        # Access revoked (link off, deleted, expired, password changed): show nothing. If the source is just
        # unreachable for a moment, keep showing the last copy.
        entry = empty if e.status_code in (401, 403, 404) or not hit else hit[1]
    if (error, sig, perms, name, source_kind) != (link.get("error"), link.get("fields_hash"), link.get("permissions"), link.get("name"),
                                                   link.get("source_kind")):
        link.update(error=error, fields_hash=sig, permissions=perms, name=name, source_kind=source_kind)
        conn.execute("UPDATE tables SET remote = %s WHERE id = %s", (Jsonb(link), tid))
        table["remote"] = link
    with _lock:
        now = time.time()
        _cache[tid] = (now, entry)
        # copies nobody has looked at for a while (tables deleted, links no one opens) don't stay in memory
        for k in [k for k, (at, _) in _cache.items() if now - at > 900]:
            _cache.pop(k, None)
    return entry


def _follow_views(conn, table, link, payloads, merged):
    """Each of the source table's views that the link shares has a view here that stays in step with it: its type,
    name, sort, grouping, layout and the fields it shows; and, when there are several, just its own records (a filter
    on Source views). Views added at the source appear, ones removed go; views made here are left alone. Only a change
    at the source is applied: what the owner changes here stays until the source's view changes again."""
    tid = str(table["id"])
    mirrors = dict(link.get("mirrors") or {})       # source view id -> {"view": local view id, "seen": what was applied}
    if not mirrors and link.get("mirror_view"):     # a table linked before every view came along: its view is the link's
        mirrors[str(link["view_id"])] = {"view": link["mirror_view"], "seen": link.get("mirror_src")}
    several = len(payloads) > 1
    names = _view_names(payloads)
    every = [lid(tid, f["id"]) for f in merged.get("fields") or []]
    changed = False
    for p in payloads:
        sv = p.get("view") or {}
        svid = str(sv["id"])
        cfg = map_view_config(tid, sv.get("config") or {})
        shows = {lid(tid, f["id"]) for f in p.get("fields") or []} | {lid(tid, REC_ID)} & set(cfg.get("shown") or [])
        cfg["hidden"] = sorted(set(cfg.get("hidden") or []) | {x for x in every if x not in shows})
        if several:
            cfg["filter"] = {"conjunction": "and", "items": [{"field": lid(tid, VIEWS_FIELD), "op": "is", "value": names[svid]}]}
        vtype = sv.get("type") or "grid"
        vname = sv.get("name") or "View"
        seen = hashlib.sha256(json.dumps([vtype, vname, cfg], sort_keys=True, default=str).encode()).hexdigest()
        m = mirrors.get(svid) or {}
        local = conn.execute("SELECT * FROM views WHERE id = %s AND table_id = %s", (m.get("view"), tid)).fetchone() if m.get("view") else None
        if local is None:
            v = conn.execute("INSERT INTO views (table_id, name, type, config, position) VALUES (%s,%s,%s,%s,"
                             "(SELECT COALESCE(MAX(position), -1) + 1 FROM views WHERE table_id = %s)) RETURNING id",
                             (tid, vname, vtype, Jsonb(cfg), tid)).fetchone()
            mirrors[svid] = {"view": str(v["id"]), "seen": seen}
            changed = True
        elif m.get("seen") != seen:
            conn.execute("UPDATE views SET config = %s, type = %s, name = %s, updated_at = now() WHERE id = %s",
                         (Jsonb(cfg), vtype, vname if m.get("seen") is not None or local["name"] == vname else local["name"], local["id"]))
            mirrors[svid] = {"view": str(local["id"]), "seen": seen}
            changed = True
    current = {str(p["view"]["id"]) for p in payloads}
    for svid, m in list(mirrors.items()):
        if svid not in current:   # gone from the source (or from the link): its view here goes too, unless it's the last one
            left = conn.execute("SELECT count(*) AS n FROM views WHERE table_id = %s AND user_id IS NULL", (tid,)).fetchone()["n"]
            if left > 1:
                conn.execute("DELETE FROM views WHERE id = %s AND table_id = %s", (m.get("view"), tid))
            mirrors.pop(svid)
            changed = True
    if changed or link.get("mirrors") != mirrors:
        primary = mirrors.get(str(link["view_id"]), {}).get("view")
        link.update(mirrors=mirrors, mirror_view=primary)
        link.pop("mirror_src", None)
        conn.execute("UPDATE tables SET remote = (remote - 'mirror_src') || %s WHERE id = %s",
                     (Jsonb({"mirrors": mirrors, "mirror_view": primary}), tid))


def set_follow_views(conn, table, on: bool):
    """Turned back on: the source's view is applied again at the next load (its own changes replaced)."""
    link = dict(table.get("remote") or {})
    mirrors = {k: {**v, "seen": None} for k, v in (link.get("mirrors") or {}).items()}   # applied again at the next load
    conn.execute("UPDATE tables SET remote = (remote - 'mirror_src') || %s WHERE id = %s",
                 (Jsonb({"follow_views": bool(on), "mirrors": mirrors}), table["id"]))
    invalidate(table["id"])


def records(conn, table) -> list:
    return [{**r, "data": dict(r["data"])} for r in load(conn, table)["records"]]


def refs(conn, table) -> dict:
    return load(conn, table)["refs"]


def public_info(table) -> dict | None:
    """What the browser may know about a linked table's source (never the access cookie)."""
    from app.sharing.links import effective
    r = table.get("remote") if table else None
    if not r:
        return None
    return {"url": r.get("url"), "origin": r.get("origin"), "local": r.get("local"), "name": r.get("name"),
            "source_kind": r.get("source_kind"), "follow_views": r.get("follow_views", True),
            "permissions": effective(r.get("permissions")), "error": r.get("error"), "has_password": bool(r.get("cookie"))}


# ======================================================================== creating & writing

def create_table(conn, store, user_id, base_id, name, url, password, item_id, view_id, cookie=None, follow_views=True):
    link = parse_link(url)
    link.update(url=url.strip(), local=is_local(conn, link["token"]))
    if password:
        link["cookie"] = unlock(conn, link, password)
    elif cookie:   # (a linked base's tables: its password was entered once, for the base)
        link["cookie"] = cookie
    meta = fetch_meta(conn, link)
    if meta.get("needs_password"):
        raise HTTPException(401, "password_required")
    item = next((i for i in meta["items"] if i["id"] == item_id), None) or (meta["items"][0] if meta["items"] else None)
    if not item:
        raise HTTPException(400, "That link doesn't share any tables")
    view = next((v for v in item["views"] if v["id"] == view_id), None) or item["views"][0]
    link.update(item_id=item["id"], view_id=view["id"], name=meta["name"], permissions=meta.get("permissions") or {},
                source_kind=item.get("table_kind") or item.get("kind"))  # what it is there: it behaves like one here
    t = conn.execute(
        "INSERT INTO tables (user_id, base_id, name, kind, remote) VALUES (%s,%s,%s,'remote',%s) RETURNING *",
        (user_id, base_id, (name or "").strip() or item["name"], Jsonb(link)),
    ).fetchone()
    payload = fetch_view(conn, link, view["id"])
    link["fields_hash"] = sync_fields(conn, t, payload)
    conn.execute("UPDATE tables SET remote = %s WHERE id = %s", (Jsonb(link), t["id"]))
    cfg = map_view_config(t["id"], payload.get("view", {}).get("config") or {})
    mirror = store.create_view(conn, table_id=t["id"], name=view["name"], type=view["type"], config=cfg)
    link.update(mirror_view=str(mirror["id"]), follow_views=True, mirrors={str(view["id"]): {"view": str(mirror["id"]), "seen": None}})
    conn.execute("UPDATE tables SET remote = %s WHERE id = %s", (Jsonb(link), t["id"]))
    # every other view of the table that the link shares comes along (and the data they show)
    invalidate(t["id"])
    load(conn, conn.execute("SELECT * FROM tables WHERE id = %s", (t["id"],)).fetchone(), force=True)
    invalidate(t["id"])   # (that copy was for its views: the first look at the table fetches its data afresh)
    if not follow_views:
        conn.execute("UPDATE tables SET remote = remote || %s WHERE id = %s", (Jsonb({"follow_views": False}), t["id"]))
    if not conn.execute("SELECT 1 FROM views WHERE table_id = %s AND type = 'grid'", (t["id"],)).fetchone():
        store.create_view(conn, table_id=t["id"], name="Table", type="grid", config={"hidden": cfg.get("hidden", [])})
    return conn.execute("SELECT * FROM tables WHERE id = %s", (t["id"],)).fetchone()


def relink(conn, table, url=None, password=None):
    """Change the link of a linked table and/or re-enter its password."""
    link = dict(table["remote"])
    if url and url.strip() != link.get("url"):
        new = parse_link(url)
        link.update(new, url=url.strip(), local=is_local(conn, new["token"]), cookie=None, fields_hash=None)
        link["view_id"] = new.get("view_id") or link.get("view_id")
    if password:
        link["cookie"] = unlock(conn, link, password)
    meta = fetch_meta(conn, link)
    if meta.get("needs_password"):
        raise HTTPException(401, "password_required")
    views = [v["id"] for i in meta["items"] for v in i["views"]]
    if link.get("view_id") not in views and views:
        link["view_id"] = views[0]
    link.update(name=meta["name"], permissions=meta.get("permissions") or {}, error=None)
    conn.execute("UPDATE tables SET remote = %s WHERE id = %s", (Jsonb(link), table["id"]))
    invalidate(table["id"])
    return conn.execute("SELECT * FROM tables WHERE id = %s", (table["id"],)).fetchone()


def _to_remote(conn, table, data: dict) -> dict:
    rows = conn.execute("SELECT id, options FROM fields WHERE table_id = %s AND system_key = 'remote'", (table["id"],)).fetchall()
    m = {str(r["id"]): r["options"] for r in rows}
    return {m[k]["_remote_id"]: v for k, v in data.items() if k in m and not m[k].get("_readonly")}


def _remote_record_id(conn, table, record_id) -> str:
    rid = load(conn, table)["reverse"].get(str(record_id)) or load(conn, table, force=True)["reverse"].get(str(record_id))
    if not rid:
        raise HTTPException(404, "Record not found in the linked table")
    return rid


def _perm(table, what):
    if not ((table.get("remote") or {}).get("permissions") or {}).get(what):
        verb = {"add": "Adding records", "edit": "Editing", "delete": "Deleting"}[what]
        raise HTTPException(403, f"{verb} isn't allowed by this link's owner")


def sources(conn, table) -> list | None:
    """For a link to a combined database: its source tables (where a new record can go)."""
    src = load(conn, table).get("sources")
    return [{"table_id": str(x["table_id"]), "name": x["name"], "color": x.get("color"), "kind": x.get("kind")} for x in src] if src else None


def _route(conn, table, record_id, remote_data: dict) -> str | None:
    """Which of the source's views a write goes through: one that shows the record (for a new one, any) and as many of
    the fields written as possible, the link's own view first among equals (the source accepts a write only through a
    view that shows the record, and only the fields that view shows)."""
    entry = load(conn, table)
    own = str(table["remote"]["view_id"])
    shows = entry.get("view_fields") or {}
    if not shows:
        return None
    cands = entry.get("via", {}).get(str(record_id)) if record_id else list(shows)
    if not cands:
        return None
    want = set(remote_data)
    return max(cands, key=lambda v: (len(want & shows.get(v, set())), v == own))


def create(conn, table, data: dict, source_table_id=None) -> dict:
    _perm(table, "add")
    link = table["remote"]
    srcs = sources(conn, table)
    if srcs and len(srcs) > 1 and source_table_id not in {s["table_id"] for s in srcs}:
        raise HTTPException(400, "Choose which of the linked tables the record goes into")
    remote = _to_remote(conn, table, data)
    r = _write(link, "POST", "records", {"data": remote, "table_id": source_table_id}, _route(conn, table, None, remote))
    invalidate(table["id"])
    return {"id": lid(table["id"], r["id"])}


def update(conn, table, record_id, data: dict):
    _perm(table, "edit")
    remote = _to_remote(conn, table, data)
    _write(table["remote"], "PATCH", f"records/{_remote_record_id(conn, table, record_id)}", {"data": remote},
           _route(conn, table, record_id, remote))
    invalidate(table["id"])


def delete(conn, table, record_id):
    _perm(table, "delete")
    _write(table["remote"], "DELETE", f"records/{_remote_record_id(conn, table, record_id)}", None, _route(conn, table, record_id, {}))
    invalidate(table["id"])


FILE_PARTS = {"content", "thumb", "preview"}
# what may be shown inline when it comes from another server (anything else downloads)
_INLINE = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp", "image/svg+xml", "application/pdf",
           "text/plain"}


def file(conn, table, record_id: str, part: str, *, download=False, text=False, rng: str | None = None):
    """A file of a files table shared by the link: its contents (with byte ranges), cover image or document preview,
    from the source (this server's own share, or the other server's public link), with this server's safety rules."""
    from app.files import catalog as files
    from app.sharing import links as shares
    if part not in FILE_PARTS:
        raise HTTPException(404, "Not found")
    link = table["remote"]
    if link.get("local"):
        t, rec, keys = shares.core_file(link["token"], record_id, _cookie_value(link))
        if part == "thumb":
            return files.thumb_response(record_id)
        if part == "preview":
            return files.preview_response(t, rec, keys)
        return files.stream(t, rec, keys, download=download, as_text=text, rng=rng)
    params = "&".join(k for k, on in (("download=1", download), ("text=1", text)) if on)
    url = f"{link['origin']}{_base(link)}/files/{quote(record_id, safe='')}/{part}" + (f"?{params}" if params else "")
    _check_host(url)
    headers = {"User-Agent": "Probyte-link/1"}
    if link.get("cookie"):
        headers["Cookie"] = link["cookie"]
    if rng and part == "content":
        headers["Range"] = rng
    try:
        r = urllib.request.build_opener(_NoRedirect).open(urllib.request.Request(url, headers=headers), timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        if e.code == 416:
            return Response(status_code=416)
        raise HTTPException(404 if e.code in (403, 404) else 502, "File not found" if e.code in (403, 404) else "The source server couldn't send the file")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HTTPException(502, f"Couldn't reach {urlparse(url).hostname}: {getattr(e, 'reason', e)}")
    ctype = r.headers.get("Content-Type", "application/octet-stream")
    base = ctype.split(";")[0].strip().lower()
    inline = (base in _INLINE or base.startswith(("video/", "audio/"))) and not download
    h = {k: r.headers[k] for k in ("Content-Length", "Content-Range") if r.headers.get(k)}
    h.update({"Accept-Ranges": "bytes", "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-cache"})
    disp = r.headers.get("Content-Disposition") or "attachment"
    if inline:
        h["Content-Disposition"] = disp.replace("attachment", "inline")
        if base == "image/svg+xml":
            h["Content-Security-Policy"] = files.SVG_CSP
        elif base != "application/pdf":
            h["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; media-src 'self'; sandbox"
    else:
        ctype, h["Content-Disposition"] = "application/octet-stream", disp.replace("inline", "attachment")

    def chunks():
        with r:
            while True:
                block = r.read(256 * 1024)
                if not block:
                    break
                yield block
    return StreamingResponse(chunks(), status_code=r.status, media_type=ctype, headers=h)


# ---- what a files / contacts source table can do, through its link (within the link's permissions)

def _send(table, method: str, path: str, **kw):
    """A request to the link's own server (HTTP), for uploads and other writes; returns the JSON answer."""
    import requests
    link = table["remote"]
    url = f"{link['origin']}{_base(link)}{path}"
    _check_host(url)
    headers = {"User-Agent": "Probyte-link/1"}
    if link.get("cookie"):
        headers["Cookie"] = link["cookie"]
    try:
        r = requests.request(method, url, headers=headers, timeout=(10, 600), allow_redirects=False, **kw)
    except requests.RequestException as e:
        raise HTTPException(502, f"Couldn't reach {urlparse(url).hostname}: {e.__class__.__name__}")
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.reason
        raise HTTPException(r.status_code if r.status_code in (400, 401, 403, 404, 413) else 502, detail or "The source server refused")
    invalidate(table["id"])
    return r


def _view(table) -> str:
    return quote(str(table["remote"]["view_id"]), safe="")


def _done(table, result):
    invalidate(table["id"])
    return result


def upload(table, files, folder: str):
    """Add files to the linked files table (the link must allow adding)."""
    from app.sharing import links as shares
    link = table["remote"]
    if link.get("local"):
        return _done(table, shares.core_upload(link["token"], link["view_id"], files, folder, _cookie_value(link)))
    sent = [("files", (f.filename or "file", f.file, f.content_type or "application/octet-stream")) for f in files]
    return _send(table, "POST", f"/views/{_view(table)}/files", files=sent, data={"folder": folder}).json()["records"]


def replace_file(table, record_id: str, upload_file, new_ext: str | None = None):
    """New contents for a file of the linked files table (the link must allow changing files); new_ext: the new
    version is in another format (the file is renamed to match)."""
    from app.sharing import links as shares
    link = table["remote"]
    if link.get("local"):
        return _done(table, shares.core_replace(link["token"], link["view_id"], record_id, upload_file, _cookie_value(link), new_ext))
    return _send(table, "PUT", f"/views/{_view(table)}/files/{quote(record_id, safe='')}/file", data={"new_ext": new_ext or ""},
                 files={"file": (upload_file.filename or "file", upload_file.file, upload_file.content_type or "application/octet-stream")}).json()


def can(table, what: str) -> bool:
    """What the link allows (add, edit, delete, upload_files, edit_files)."""
    from app.sharing.links import effective
    return effective((table.get("remote") or {}).get("permissions"))[what]


def save_text(table, record_id: str, text: str):
    from app.sharing import links as shares
    link = table["remote"]
    if link.get("local"):
        return _done(table, shares.core_text(link["token"], link["view_id"], record_id, text, _cookie_value(link)))
    return _send(table, "PUT", f"/views/{_view(table)}/files/{quote(record_id, safe='')}/text", json={"text": text}).json()


def import_vcards(table, texts: list[tuple[str, str]], existing: str):
    from app.sharing import links as shares
    link = table["remote"]
    if link.get("local"):
        return _done(table, shares.core_vcards_import(link["token"], link["view_id"], texts, existing, _cookie_value(link)))
    sent = [("files", (name, text.encode(), "text/vcard")) for name, text in texts]
    return _send(table, "POST", f"/views/{_view(table)}/vcards", files=sent, data={"existing": existing}).json()


def export_vcards(table):
    from app.sharing import links as shares
    link = table["remote"]
    if link.get("local"):
        return shares.core_vcards_export(link["token"], link["view_id"], _cookie_value(link))
    r = _send(table, "GET", f"/views/{_view(table)}/vcards")
    return Response(r.content, media_type="text/vcard; charset=utf-8",
                    headers={"Content-Disposition": r.headers.get("Content-Disposition", 'attachment; filename="contacts.vcf"')})


def attachment(conn, table, attachment_id, download: bool):
    from app.sharing import links as shares
    link = table["remote"]
    if link.get("local"):
        return shares.core_attachment(link["token"], attachment_id, _cookie_value(link), download)
    path = f"{_base(link)}/attachments/{quote(attachment_id, safe='')}" + ("?download=1" if download else "")
    with _open("GET", link["origin"] + path, cookie=link.get("cookie")) as r:
        body = r.read(MAX_BYTES + 1)
        ctype = r.headers.get("Content-Type", "application/octet-stream")
        disp = r.headers.get("Content-Disposition", "attachment")
    if len(body) > MAX_BYTES:
        raise HTTPException(502, "File too large")
    from app.blobs.attachments import INLINE_TYPES
    inline = ctype.split(";")[0].strip() in INLINE_TYPES and not download
    return Response(body, media_type=ctype if inline else "application/octet-stream",
                    headers={"Content-Disposition": disp if inline else disp.replace("inline", "attachment"),
                             "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; img-src 'self'; sandbox",
                             "Cache-Control": "private, max-age=3600"})


# the computed-values engine reads linked tables' records (and what they refer to) through their link
compute.READERS["remote"] = records
compute.REF_READERS["remote"] = refs
