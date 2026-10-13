"""Probyte's own CalDAV / CardDAV server: phones and calendar apps read and write tables directly.

Every tasks, events, mixed or contacts table on the built-in account is a collection (calendar or address book):

    /.well-known/caldav, /.well-known/carddav   -> /dav/
    /dav/                                       root: who is signed in
    /dav/<username>/                            the user's principal, holding their calendars and address books
    /dav/<username>/<slug>/                     a table (tables.dav_slug)
    /dav/<username>/<slug>/<name>.ics|.vcf      a record (records.caldav_href = <name>)

The same paths without /dav serve devices set up while calendars lived in the bundled Radicale (the layout is the
one Radicale used, and its collection and item names were kept when calendars moved here).

There's nothing to sync: a phone's PUT is a record write (in the same transaction as its history entry and the
change-feed event), and a change made in Probyte is in the next answer a phone gets. Each record keeps the item as
last written (caldav_raw), so properties Probyte doesn't show survive; a change made in Probyte renders a new item
from it at once (on_record_change), and the item's ETag is a hash of it. Collections have a CTag (the table's
data_version) and an RFC 6578 sync-token (the highest row version of its records and deletion tombstones), so a
phone asking "anything new?" costs one indexed query, and gets only what changed.

Auth: HTTP Basic with the username and device password (dav/devices.py); everyone sees only their own collections.
"""
import base64
import logging
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from typing import NamedTuple
from urllib.parse import quote, unquote, urlparse
from xml.etree import ElementTree as ET

import psycopg
from fastapi import HTTPException

from app.core import events, jobs, ratelimit
from app.core.db import connection, Jsonb
from app.data import audit, store
from app.dav import devices
from app.dav.items import etag as make_etag, item_name, Mapper, parse_item
from app.formats import ical, vcard

log = logging.getLogger("probyte.dav.server")

D, C, CR, CS, ICAL = "DAV:", "urn:ietf:params:xml:ns:caldav", "urn:ietf:params:xml:ns:carddav", "http://calendarserver.org/ns/", "http://apple.com/ns/ical/"
for _p, _u in (("d", D), ("cal", C), ("card", CR), ("cs", CS), ("ical", ICAL)):
    ET.register_namespace(_p, _u)

SYNC_PREFIX = "https://probyte.app/ns/sync/"
MAX_ITEM = 10 * 1024 * 1024
METHODS = ["OPTIONS", "GET", "HEAD", "PUT", "DELETE", "PROPFIND", "PROPPATCH", "REPORT", "MKCOL", "MKCALENDAR", "MOVE"]
DAV_HEADER = "1, 3, calendar-access, addressbook, extended-mkcol"
WRITES = {"PUT", "DELETE", "MOVE", "MKCOL", "MKCALENDAR", "PROPPATCH"}
COMPONENTS = {"tasks": ["VTODO"], "events": ["VEVENT"], "mixed": ["VEVENT", "VTODO"]}
DEFAULT_COLORS = {"tasks": "#22c55e", "events": "#3b82f6", "mixed": "#a855f7", "contacts": "#64748b"}


class Response(NamedTuple):
    status: int
    headers: dict
    body: bytes = b""


class DavFail(Exception):
    """An error answer: status, and the precondition element (CalDAV/WebDAV error codes) when there is one."""

    def __init__(self, status: int, condition: tuple[str, str] | None = None, message: str = ""):
        super().__init__(message or str(status))
        self.status, self.condition = status, condition


def q(ns, tag):
    return f"{{{ns}}}{tag}"


# ======================================================================== entry point

def handle(method: str, prefix: str, path: str, headers: dict, body: bytes, client: str) -> Response:
    """One request. prefix: "/dav" or "" (the old Radicale layout); path: what follows it."""
    method = method.upper()
    if method == "OPTIONS":
        return Response(200, {"DAV": DAV_HEADER, "Allow": ", ".join(METHODS), "Content-Length": "0"})
    user = _login(headers, client)
    if isinstance(user, Response):
        return user
    agent = headers.get("user-agent", "")
    audit.set_actor(f"{devices.label(agent)} (calendar sync)", user["id"])
    devices.seen(user["id"], agent, client, wrote=method in WRITES)
    parts = [unquote(p) for p in path.split("/") if p]
    if parts and parts[0].lower() != user["username"]:
        return Response(404 if len(parts) > 1 else 403, {})
    ctx = Ctx(prefix, user, parts, headers)
    try:
        fn = getattr(ctx, "do_" + method.lower(), None)
        if fn is None:
            return Response(405, {"Allow": ", ".join(METHODS)})
        return fn(body)
    except DavFail as e:
        if e.condition:
            err = ET.Element(q(D, "error"))
            ET.SubElement(err, q(*e.condition))
            return Response(e.status, {"Content-Type": "application/xml; charset=utf-8"}, _xml(err))
        return Response(e.status, {})
    except ET.ParseError:
        return Response(400, {})


def _login(headers: dict, client: str):
    h = headers.get("authorization", "")
    challenge = {"WWW-Authenticate": f'Basic realm="{devices.REALM}", charset="UTF-8"'}
    if not h.lower().startswith("basic "):
        return Response(401, challenge)
    try:
        name, _, password = base64.b64decode(h[6:].strip()).decode("utf-8").partition(":")
    except Exception:
        return Response(401, challenge)
    account = f"dav:{name.strip().lower()}"
    try:
        ratelimit.check(account, client)
    except HTTPException as e:
        return Response(429, dict(e.headers or {}))
    user = devices.authenticate(name, password)
    if not user:
        ratelimit.failed(account, client)
        return Response(401, challenge)
    return user


def _xml(el) -> bytes:
    return ET.tostring(el, encoding="utf-8", xml_declaration=True)


def _href(path: str) -> ET.Element:
    el = ET.Element(q(D, "href"))
    el.text = quote(path, safe="/@:~!$&'()*+,;=")
    return el


def _http_date(dt) -> str:
    return format_datetime(dt.astimezone(timezone.utc), usegmt=True) if dt else ""


# ======================================================================== resources

def served_tables(conn, user_id):
    return conn.execute("SELECT * FROM tables WHERE user_id = %s AND dav_slug IS NOT NULL AND trashed_at IS NULL AND kind = ANY(%s) "
                        "ORDER BY created_at", (user_id, list(store.DAV_KINDS))).fetchall()


def _table(conn, user_id, slug):
    return conn.execute("SELECT * FROM tables WHERE user_id = %s AND dav_slug = %s AND trashed_at IS NULL AND kind = ANY(%s)",
                        (user_id, slug, list(store.DAV_KINDS))).fetchone()


def sync_token(conn, table_id) -> int:
    return conn.execute(
        "SELECT GREATEST(COALESCE((SELECT max(row_version) FROM records WHERE table_id = %s), 0), "
        "COALESCE((SELECT max(row_version) FROM record_tombstones WHERE table_id = %s), 0)) AS v", (table_id, table_id)).fetchone()["v"]


def _tombstone_floor(conn) -> int:
    row = conn.execute("SELECT value FROM app_settings WHERE key = 'tombstone_floor'").fetchone()
    return int(row["value"]) if row else 0


def render_pending(conn, table) -> None:
    """Records whose item isn't rendered yet (written without the change feed, e.g. before an upgrade): rendered
    before they're served. One lookup in a small partial index when there's nothing to do."""
    rows = conn.execute("SELECT * FROM records WHERE table_id = %s AND sync_status = 'dirty' AND NOT deleted", (table["id"],)).fetchall()
    if rows:
        mapper = Mapper(conn, table, store.get_fields(conn, table["id"]))
        for r in rows:
            _render(conn, table, mapper, r)


def _render(conn, table, mapper, r):
    uid = r["caldav_uid"] or (vcard.new_uid() if table["kind"] == "contacts" else ical.new_uid())
    body = mapper.body({**r, "caldav_uid": uid})
    # only if nothing wrote the record since it was read (a phone's PUT, another render): that write wins
    conn.execute("UPDATE records SET caldav_uid = %s, caldav_href = %s, caldav_raw = %s, caldav_etag = %s, sync_status = 'synced' "
                 "WHERE id = %s AND row_version = %s",
                 (uid, r["caldav_href"] or item_name(table["kind"], uid), body, make_etag(body), r["id"], r["row_version"]))


def on_record_change(conn, table, event, record_id, source):
    """Change feed: a record of a served table was changed in Probyte. Its item is rendered by a job a moment later
    (one job for a burst of changes: an import of thousands of events doesn't render each as it's written), and in
    any case before a phone is answered (render_pending), so a phone never sees a stale item."""
    if event == "record.deleted" or source == "dav" or not table.get("dav_slug") or table.get("kind") not in store.DAV_KINDS:
        return
    jobs.enqueue(conn, "dav_render", {"table_id": str(table["id"])}, key=f"dav_render:{table['id']}", delay=0.5)


@jobs.handler("dav_render")
def _render_job(payload):
    with connection() as conn:
        t = conn.execute("SELECT * FROM tables WHERE id = %s", (payload["table_id"],)).fetchone()
        if t and t["dav_slug"]:
            render_pending(conn, t)


def find_item(conn, t, name, with_body=True, lock=False):
    cols = "*" if with_body else "id, caldav_href, caldav_etag, updated_at, row_version, data, caldav_uid, sync_status"
    return conn.execute(f"SELECT {cols} FROM records WHERE table_id = %s AND caldav_href = %s AND NOT deleted"
                        + (" FOR UPDATE" if lock else ""), (t["id"], name)).fetchone()


def write_item(conn, user, t, name, text, if_match=None, if_none_match=None) -> tuple[int, str]:
    """A device wrote an item: create or update its record. Returns (HTTP status, ETag)."""
    book = t["kind"] == "contacts"
    if ("BEGIN:VCARD" if book else "BEGIN:VCALENDAR") not in text.upper():
        raise DavFail(415 if not book else 400, (CR, "valid-address-data") if book else (C, "supported-calendar-data"))
    try:
        uid, vals = parse_item(t["kind"], name, text)
    except Exception as e:
        log.info("unreadable item %s: %s", name, e)
        raise DavFail(400, (CR, "valid-address-data") if book else (C, "valid-calendar-data"))
    if uid is None:
        raise DavFail(403, (C, "supported-calendar-component"))
    render_pending(conn, t)  # an edit made here a moment ago has its new ETag before the device's If-Match is checked
    existing = find_item(conn, t, name, lock=True)
    if if_none_match == "*" and existing:
        raise DavFail(412)
    if if_match and if_match != "*" and (not existing or existing["caldav_etag"] != if_match):
        raise DavFail(412)
    if if_match == "*" and not existing:
        raise DavFail(412)
    clash = conn.execute("SELECT caldav_href FROM records WHERE table_id = %s AND caldav_uid = %s AND id IS DISTINCT FROM %s AND NOT deleted",
                         (t["id"], uid, existing["id"] if existing else None)).fetchone()
    if clash:
        raise DavFail(409, (CR, "no-uid-conflict") if book else (C, "no-uid-conflict"))
    fields = store.get_fields(conn, t["id"])
    mapper = Mapper(conn, t, fields)
    et = make_etag(text)
    if existing:
        data = mapper.to_data(vals, existing["data"])
        if data == existing["data"]:  # the same values written again (a new DTSTAMP, an alarm acknowledged…): no history, no webhook
            conn.execute("UPDATE records SET caldav_uid = %s, caldav_raw = %s, caldav_etag = %s, sync_status = 'synced' WHERE id = %s",
                         (uid, text, et, existing["id"]))
            return 204, et
        conn.execute("UPDATE records SET data = %s, caldav_uid = %s, caldav_raw = %s, caldav_etag = %s, sync_status = 'synced', "
                     "updated_at = now(), updated_by = %s WHERE id = %s",
                     (Jsonb(data), uid, text, et, user["id"], existing["id"]))
        audit.note(conn, t["id"], existing["id"], "updated", existing["data"], data, fields)
        events.record_changed(conn, t, "record.updated", existing["id"], source="dav")
        return 204, et
    try:
        with conn.transaction():
            new = store.insert_record(conn, t["id"], mapper.to_data(vals, {}), user_id=user["id"], caldav_uid=uid,
                                      caldav_href=name, caldav_raw=text)
    except psycopg.errors.UniqueViolation:  # the same item written twice at once
        raise DavFail(409, (CR, "no-uid-conflict") if book else (C, "no-uid-conflict"))
    conn.execute("UPDATE records SET caldav_etag = %s WHERE id = %s", (et, new["id"]))
    audit.note(conn, t["id"], new["id"], "created")
    events.record_changed(conn, t, "record.created", new["id"], source="dav")
    return 201, et


PRIVILEGES = ["read", "write", "write-properties", "write-content", "bind", "unbind", "read-current-user-privilege-set"]


def _privileges(el):
    for p in PRIVILEGES:
        ET.SubElement(ET.SubElement(el, q(D, "privilege")), q(D, p))


def _reports(names):
    def fill(el):
        for ns, tag in names:
            ET.SubElement(ET.SubElement(ET.SubElement(el, q(D, "supported-report")), q(D, "report")), q(ns, tag))
    return fill


class Ctx:
    """One authenticated request."""

    def __init__(self, prefix, user, parts, headers):
        self.prefix, self.user, self.parts, self.headers = prefix, user, parts, headers
        self.root = prefix + "/"
        self.home = f"{prefix}/{user['username']}/"

    # ---------------------------------------------------------------- paths
    def coll_path(self, t):
        return f"{self.home}{t['dav_slug']}/"

    def item_path(self, t, name):
        return f"{self.home}{t['dav_slug']}/{name}"

    def depth(self) -> int:
        """0 or 1 (infinity is answered as 1, which every client copes with)."""
        return 0 if self.headers.get("depth", "infinity").strip() == "0" else 1

    def target(self, conn):
        """(kind, table, item name) of the requested path; kind: root | home | collection | item."""
        n = len(self.parts)
        if n == 0:
            return "root", None, None
        if n == 1:
            return "home", None, None
        t = _table(conn, self.user["id"], self.parts[1])
        if n == 2:
            return "collection", t, None
        if n == 3:
            return "item", t, self.parts[2]
        return "none", None, None

    # ---------------------------------------------------------------- properties
    def root_props(self):
        return {
            q(D, "resourcetype"): lambda el: ET.SubElement(el, q(D, "collection")),
            q(D, "current-user-principal"): lambda el: el.append(_href(self.home)),
            q(D, "principal-collection-set"): lambda el: el.append(_href(self.root)),
            q(D, "displayname"): "Probyte",
        }

    def home_props(self):
        home = lambda el: el.append(_href(self.home))  # noqa: E731
        return {
            q(D, "resourcetype"): lambda el: (ET.SubElement(el, q(D, "collection")), ET.SubElement(el, q(D, "principal"))),
            q(D, "current-user-principal"): home, q(D, "principal-URL"): home, q(D, "owner"): home,
            q(C, "calendar-home-set"): home, q(CR, "addressbook-home-set"): home,
            q(C, "calendar-user-address-set"): home,
            q(D, "principal-collection-set"): lambda el: el.append(_href(self.root)),
            q(D, "displayname"): self.user["username"],
            q(D, "current-user-privilege-set"): _privileges,
            q(D, "supported-report-set"): _reports([(D, "sync-collection")]),
        }

    def coll_props(self, conn, t, token=None):
        book = t["kind"] == "contacts"
        dead = dict(t.get("dav_props") or {})
        props = {
            q(D, "resourcetype"): lambda el: (ET.SubElement(el, q(D, "collection")), ET.SubElement(el, q(CR, "addressbook") if book else q(C, "calendar"))),
            q(D, "displayname"): t["name"],
            q(CS, "getctag"): str(t["data_version"]),
            q(D, "getetag"): f'"{t["data_version"]}"',
            q(D, "sync-token"): lambda el: setattr(el, "text", SYNC_PREFIX + str(token if token is not None else sync_token(conn, t["id"]))),
            q(D, "owner"): lambda el: el.append(_href(self.home)),
            q(D, "current-user-privilege-set"): _privileges,
            q(D, "getcontenttype"): "text/vcard" if book else "text/calendar",
        }
        if book:
            props[q(CR, "addressbook-description")] = t.get("description") or ""
            props[q(CR, "supported-address-data")] = lambda el: ET.SubElement(el, q(CR, "address-data-type"), {"content-type": "text/vcard", "version": "3.0"})
            props[q(CR, "max-resource-size")] = str(MAX_ITEM)
            props[q(D, "supported-report-set")] = _reports([(CR, "addressbook-multiget"), (CR, "addressbook-query"), (D, "sync-collection")])
        else:
            props[q(C, "calendar-description")] = t.get("description") or ""
            props[q(C, "supported-calendar-component-set")] = lambda el: [ET.SubElement(el, q(C, "comp"), {"name": c}) for c in COMPONENTS[t["kind"]]]
            props[q(C, "supported-calendar-data")] = lambda el: ET.SubElement(el, q(C, "calendar-data"), {"content-type": "text/calendar", "version": "2.0"})
            props[q(C, "max-resource-size")] = str(MAX_ITEM)
            props[q(ICAL, "calendar-color")] = DEFAULT_COLORS.get(t["kind"], "#3b82f6")
            props[q(D, "supported-report-set")] = _reports([(C, "calendar-multiget"), (C, "calendar-query"), (D, "sync-collection")])
        for k, v in dead.items():  # set by devices (PROPPATCH): colour, order…
            props[k] = v
        return props

    def item_props(self, t, r):
        book = t["kind"] == "contacts"
        props = {
            q(D, "resourcetype"): "",
            q(D, "getetag"): r["caldav_etag"],
            q(D, "getcontenttype"): "text/vcard; charset=utf-8" if book else "text/calendar; charset=utf-8",
            q(D, "getlastmodified"): _http_date(r.get("updated_at")),
        }
        if "caldav_raw" in r:
            props[q(D, "getcontentlength")] = str(len(r["caldav_raw"].encode()))
            props[q(CR, "address-data") if book else q(C, "calendar-data")] = r["caldav_raw"]
        return props

    # ---------------------------------------------------------------- multistatus
    def response(self, href, props: dict, wanted) -> ET.Element:
        resp = ET.Element(q(D, "response"))
        resp.append(_href(href))
        names = list(props) if wanted is None or wanted == "propname" else wanted
        found, missing = ET.Element(q(D, "prop")), ET.Element(q(D, "prop"))
        for name in names:
            if name in props:
                el = ET.SubElement(found, name)
                if wanted == "propname":
                    continue
                v = props[name]
                if callable(v):
                    v(el)
                elif v:
                    el.text = v
            else:
                ET.SubElement(missing, name)
        for prop, status in ((found, "200 OK"), (missing, "404 Not Found")):
            if len(prop):
                ps = ET.SubElement(resp, q(D, "propstat"))
                ps.append(prop)
                ET.SubElement(ps, q(D, "status")).text = f"HTTP/1.1 {status}"
        return resp

    @staticmethod
    def status_response(href, status: str) -> ET.Element:
        resp = ET.Element(q(D, "response"))
        resp.append(_href(href))
        ET.SubElement(resp, q(D, "status")).text = f"HTTP/1.1 {status}"
        return resp

    @staticmethod
    def multistatus(responses, extra=None) -> Response:
        ms = ET.Element(q(D, "multistatus"))
        for r in responses:
            ms.append(r)
        if extra is not None:
            ms.append(extra)
        return Response(207, {"Content-Type": "application/xml; charset=utf-8"}, _xml(ms))

    @staticmethod
    def wanted(root) -> list | str | None:
        """The properties asked for: a list of names, None (all), or "propname"."""
        if root is None or root.find(q(D, "allprop")) is not None:
            return None
        if root.find(q(D, "propname")) is not None:
            return "propname"
        p = root.find(q(D, "prop"))
        return [c.tag for c in p] if p is not None else None

    @staticmethod
    def needs_body(wanted) -> bool:
        return wanted is None or wanted == "propname" or any(
            w in (q(C, "calendar-data"), q(CR, "address-data"), q(D, "getcontentlength")) for w in wanted)

    def items(self, conn, t, wanted, where="", args=()):
        cols = "id, caldav_href, caldav_etag, updated_at, row_version" + (", caldav_raw" if self.needs_body(wanted) else "")
        return conn.execute(f"SELECT {cols} FROM records WHERE table_id = %s AND NOT deleted AND caldav_href IS NOT NULL {where} "
                            "ORDER BY caldav_href", (t["id"], *args)).fetchall()

    # ======================================================================== methods
    def do_propfind(self, body):
        wanted = self.wanted(ET.fromstring(body) if body.strip() else None)
        depth = self.depth()
        with connection() as conn:
            kind, t, name = self.target(conn)
            out = []
            if kind == "root":
                out.append(self.response(self.root, self.root_props(), wanted))
                if depth:
                    out.append(self.response(self.home, self.home_props(), wanted))
            elif kind == "home":
                out.append(self.response(self.home, self.home_props(), wanted))
                if depth:
                    for tt in served_tables(conn, self.user["id"]):
                        out.append(self.response(self.coll_path(tt), self.coll_props(conn, tt), wanted))
            elif kind == "collection" and t:
                out.append(self.response(self.coll_path(t), self.coll_props(conn, t), wanted))
                if depth:
                    render_pending(conn, t)
                    for r in self.items(conn, t, wanted):
                        out.append(self.response(self.item_path(t, r["caldav_href"]), self.item_props(t, r), wanted))
            elif kind == "item" and t:
                render_pending(conn, t)
                r = self._item(conn, t, name, with_body=self.needs_body(wanted))
                if not r:
                    raise DavFail(404)
                out.append(self.response(self.item_path(t, name), self.item_props(t, r), wanted))
            else:
                raise DavFail(404)
        return self.multistatus(out)

    @staticmethod
    def _item(conn, t, name, with_body=True, lock=False):
        return find_item(conn, t, name, with_body, lock)

    def do_get(self, body, head=False):
        with connection() as conn:
            kind, t, name = self.target(conn)
            if kind in ("root", "home"):
                return Response(200, {"Content-Type": "text/plain; charset=utf-8"},
                                b"" if head else b"Probyte calendars and contacts (CalDAV / CardDAV). Add this address to your calendar app.\n")
            if kind != "item" or not t:
                raise DavFail(404 if kind != "collection" else 405)
            render_pending(conn, t)
            r = self._item(conn, t, name)
        if not r:
            raise DavFail(404)
        headers = {"ETag": r["caldav_etag"], "Last-Modified": _http_date(r["updated_at"]),
                   "Content-Type": "text/vcard; charset=utf-8" if t["kind"] == "contacts" else "text/calendar; charset=utf-8"}
        if self.headers.get("if-none-match") == r["caldav_etag"]:
            return Response(304, headers)
        data = r["caldav_raw"].encode()
        if head:
            return Response(200, {**headers, "Content-Length": str(len(data))})
        return Response(200, headers, data)

    def do_head(self, body):
        return self.do_get(body, head=True)

    # ---------------------------------------------------------------- writing items
    def do_put(self, body):
        if len(body) > MAX_ITEM:
            raise DavFail(413)
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            raise DavFail(400)
        with connection() as conn:
            kind, t, name = self.target(conn)
            if kind != "item" or not t:
                raise DavFail(409 if kind == "item" else 405)
            status, et = self.write_item(conn, t, name, text, self.headers.get("if-match"), self.headers.get("if-none-match"))
        return Response(status, {"ETag": et})

    def write_item(self, conn, t, name, text, if_match=None, if_none_match=None) -> tuple[int, str]:
        return write_item(conn, self.user, t, name, text, if_match, if_none_match)

    def do_delete(self, body):
        with connection() as conn:
            kind, t, name = self.target(conn)
            if kind == "collection" and t:
                # a calendar deleted on a device goes to Probyte's trash (restorable for 30 days)
                conn.execute("UPDATE tables SET trashed_at = now(), trash_delete_on_server = false WHERE id = %s", (t["id"],))
                return Response(204, {})
            if kind != "item" or not t:
                raise DavFail(404 if kind != "home" else 403)
            render_pending(conn, t)
            r = self._item(conn, t, name, with_body=False, lock=True)
            if not r:
                raise DavFail(404)
            im = self.headers.get("if-match")
            if im and im != "*" and im != r["caldav_etag"]:
                raise DavFail(412)
            store.delete_record(conn, r["id"], self.user["id"], source="dav")
        return Response(204, {})

    def do_move(self, body):
        dest = urlparse(self.headers.get("destination", "")).path
        if not dest.startswith(self.prefix + "/"):
            raise DavFail(502)
        dparts = [unquote(p) for p in dest[len(self.prefix):].split("/") if p]
        if len(self.parts) != 3 or len(dparts) != 3 or dparts[0].lower() != self.user["username"]:
            raise DavFail(403)
        with connection() as conn:
            kind, t, name = self.target(conn)
            dt = _table(conn, self.user["id"], dparts[1])
            if not t or not dt:
                raise DavFail(409)
            render_pending(conn, t)
            r = self._item(conn, t, name, lock=True)
            if not r:
                raise DavFail(404)
            target = self._item(conn, dt, dparts[2], with_body=False)
            if target and self.headers.get("overwrite", "T").upper() == "F":
                raise DavFail(412)
            if dt["id"] == t["id"]:  # a new name in the same collection
                if target:
                    store.delete_record(conn, target["id"], self.user["id"], source="dav")
                conn.execute("UPDATE records SET caldav_href = %s WHERE id = %s", (dparts[2], r["id"]))
                return Response(204 if target else 201, {})
            store.delete_record(conn, r["id"], self.user["id"], source="dav")
            status, et = self.write_item(conn, dt, dparts[2], r["caldav_raw"])
        return Response(status, {"ETag": et})

    # ---------------------------------------------------------------- collections
    def do_mkcalendar(self, body):
        return self._make(body, calendar=True)

    def do_mkcol(self, body):
        return self._make(body, calendar=None)

    def _make(self, body, calendar):
        if len(self.parts) != 2:
            raise DavFail(403)
        root = ET.fromstring(body) if body.strip() else None
        props = {}
        if root is not None:
            for p in root.iter(q(D, "prop")):
                for el in p:
                    props[el.tag] = el
        rt = props.get(q(D, "resourcetype"))
        if calendar is None:  # extended MKCOL: the resource type says what it is
            if rt is not None and rt.find(q(CR, "addressbook")) is not None:
                calendar = False
            elif rt is not None and rt.find(q(C, "calendar")) is not None:
                calendar = True
            else:
                raise DavFail(403, (D, "valid-resourcetype"))
        if calendar:
            comps = {c.get("name", "").upper() for c in (props.get(q(C, "supported-calendar-component-set")) or [])}
            kind = "tasks" if comps == {"VTODO"} else "events" if comps == {"VEVENT"} else "mixed"
        else:
            kind = "contacts"
        name_el = props.get(q(D, "displayname"))
        name = (name_el.text if name_el is not None else None) or self.parts[1]
        dead = {k: (v.text or "") for k, v in props.items() if k not in (q(D, "resourcetype"), q(D, "displayname"),
                q(C, "supported-calendar-component-set"), q(C, "calendar-description"), q(CR, "addressbook-description"))
                and len(v) == 0}
        desc = props.get(q(C, "calendar-description")) or props.get(q(CR, "addressbook-description"))
        with connection() as conn:
            if _table(conn, self.user["id"], self.parts[1]):
                raise DavFail(405, (D, "resource-must-be-null"))
            base = store.ensure_base(conn, self.user["id"])
            t = store.create_table(conn, self.user["id"], base["id"], name.strip()[:200] or "Calendar", kind)
            conn.execute("UPDATE tables SET dav_slug = %s, dav_props = %s, description = %s WHERE id = %s",
                         (self.parts[1], Jsonb(dead), desc.text if desc is not None else None, t["id"]))
        return Response(201, {})

    def do_proppatch(self, body):
        root = ET.fromstring(body)
        with connection() as conn:
            kind, t, _ = self.target(conn)
            if kind == "home":  # nothing to keep on the principal: say yes so clients carry on
                t = None
            elif kind != "collection" or not t:
                raise DavFail(404 if kind != "item" else 403)
            changed = []
            dead = dict((t or {}).get("dav_props") or {})
            name, desc = (t or {}).get("name"), (t or {}).get("description")
            for action in root:
                remove = action.tag == q(D, "remove")
                p = action.find(q(D, "prop"))
                for el in (p if p is not None else []):
                    changed.append(el.tag)
                    if el.tag == q(D, "displayname"):
                        name = (el.text or "").strip()[:200] or name
                    elif el.tag in (q(C, "calendar-description"), q(CR, "addressbook-description")):
                        desc = None if remove else el.text
                    elif remove:
                        dead.pop(el.tag, None)
                    elif len(el) == 0:
                        dead[el.tag] = el.text or ""
            if t:
                conn.execute("UPDATE tables SET name = %s, description = %s, dav_props = %s WHERE id = %s",
                             (name, desc, Jsonb(dead), t["id"]))
        resp = ET.Element(q(D, "response"))
        resp.append(_href(self.home + (f"{self.parts[1]}/" if len(self.parts) > 1 else "")))
        ps = ET.SubElement(resp, q(D, "propstat"))
        prop = ET.SubElement(ps, q(D, "prop"))
        for tag in changed:
            ET.SubElement(prop, tag)
        ET.SubElement(ps, q(D, "status")).text = "HTTP/1.1 200 OK"
        return self.multistatus([resp])

    # ---------------------------------------------------------------- reports
    def do_report(self, body):
        root = ET.fromstring(body)
        wanted = self.wanted(root)
        with connection() as conn:
            kind, t, name = self.target(conn)
            if kind == "home" and root.tag in (q(D, "principal-search-property-set"), q(D, "principal-property-search"), q(D, "expand-property")):
                return self.multistatus([])
            if kind not in ("collection", "item") or not t:
                raise DavFail(404 if kind == "none" else 403, (D, "supported-report"))
            render_pending(conn, t)
            if root.tag in (q(C, "calendar-multiget"), q(CR, "addressbook-multiget")):
                return self._multiget(conn, t, root, wanted)
            if root.tag in (q(C, "calendar-query"), q(CR, "addressbook-query")):
                return self._query(conn, t, root, wanted)
            if root.tag == q(D, "sync-collection"):
                return self._sync_collection(conn, t, root, wanted)
        raise DavFail(403, (D, "supported-report"))

    def _multiget(self, conn, t, root, wanted):
        out = []
        names = []
        for h in root.findall(q(D, "href")):
            path = unquote(urlparse((h.text or "").strip()).path)
            names.append((path, path.rstrip("/").rsplit("/", 1)[-1]))
        rows = {r["caldav_href"]: r for r in conn.execute(
            "SELECT id, caldav_href, caldav_etag, updated_at, caldav_raw FROM records WHERE table_id = %s AND NOT deleted AND caldav_href = ANY(%s)",
            (t["id"], [n for _, n in names])).fetchall()}
        for path, name in names:
            r = rows.get(name)
            if r and path.startswith(self.coll_path(t)):
                out.append(self.response(self.item_path(t, name), self.item_props(t, r), wanted))
            else:
                out.append(self.status_response(path, "404 Not Found"))
        return self.multistatus(out)

    def _query(self, conn, t, root, wanted):
        """calendar-query / addressbook-query: the items of the components asked for (a mixed calendar holds both),
        within the time range when one is given (RFC 4791 9.9; a repeating item is included when it starts before
        the range ends, which errs on the side of sending it)."""
        comps, rng = None, None
        cal = root.find(f"{q(C, 'filter')}/{q(C, 'comp-filter')}")
        inner = cal.findall(q(C, "comp-filter")) if cal is not None else []
        if t["kind"] == "mixed":
            comps = {c.get("name", "").upper() for c in inner} or None
        for c in inner:
            tr = c.find(q(C, "time-range"))
            if tr is not None:
                rng = (_ical_time(tr.get("start")), _ical_time(tr.get("end")))
        need_raw = bool(comps)
        rows = self.items(conn, t, list(wanted or []) + [q(C, "calendar-data")] if need_raw else wanted)
        dates = _item_dates(conn, t) if rng else {}
        out = []
        for r in rows:
            if comps and ("VTODO" if "BEGIN:VTODO" in (r["caldav_raw"] or "").upper() else "VEVENT") not in comps:
                continue
            if rng and not _overlaps(dates.get(r["id"]), rng):
                continue
            out.append(self.response(self.item_path(t, r["caldav_href"]), self.item_props(t, r), wanted))
        return self.multistatus(out)

    def _sync_collection(self, conn, t, root, wanted):
        text = (root.findtext(q(D, "sync-token")) or "").strip()
        since = None
        if text:
            if not text.startswith(SYNC_PREFIX) or not text[len(SYNC_PREFIX):].isdigit():
                raise DavFail(403, (D, "valid-sync-token"))
            since = int(text[len(SYNC_PREFIX):])
            if since < _tombstone_floor(conn):
                raise DavFail(403, (D, "valid-sync-token"))
        token = sync_token(conn, t["id"])
        out, live = [], set()
        rows = self.items(conn, t, wanted, *(("AND row_version > %s", (since,)) if since is not None else ("", ())))
        for r in rows:
            live.add(r["caldav_href"])
            out.append(self.response(self.item_path(t, r["caldav_href"]), self.item_props(t, r), wanted))
        if since is not None:
            for d in conn.execute("SELECT DISTINCT href FROM record_tombstones WHERE table_id = %s AND row_version > %s AND href IS NOT NULL",
                                  (t["id"], since)).fetchall():
                if d["href"] not in live and not self._item(conn, t, d["href"], with_body=False):
                    out.append(self.status_response(self.item_path(t, d["href"]), "404 Not Found"))
        tok = ET.Element(q(D, "sync-token"))
        tok.text = SYNC_PREFIX + str(token)
        return self.multistatus(out, tok)


# ---------------------------------------------------------------- time ranges (calendar-query)

def _ical_time(v: str | None):
    """An iCalendar UTC time (20261020T000000Z) as a datetime; None when absent (an open end)."""
    if not v:
        return None
    try:
        return datetime.strptime(v.strip(), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _value_time(v, end=False):
    """A record's date value as a UTC datetime: "YYYY-MM-DD" (the start of that day, or the end for an end date)
    or ISO-8601."""
    if not v or not isinstance(v, str):
        return None
    try:
        if len(v) == 10:
            d = datetime.strptime(v, "%Y-%m-%d").replace(tzinfo=ical.DEFAULT_TZ)
            return (d + timedelta(days=1) if end else d).astimezone(timezone.utc)
        return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _item_dates(conn, t) -> dict:
    """{record id: (start, end, repeats)} from the records' Start / End / Due / Repeat fields."""
    keys = {f["system_key"]: str(f["id"]) for f in store.get_fields(conn, t["id"]) if f["system_key"]}
    out = {}
    for r in conn.execute("SELECT id, data FROM records WHERE table_id = %s AND NOT deleted", (t["id"],)).fetchall():
        d = r["data"]
        start = _value_time(d.get(keys.get("dtstart", "")))
        end = _value_time(d.get(keys.get("dtend", "")) or d.get(keys.get("due", "")), end=True)
        out[r["id"]] = (start, end, bool(d.get(keys.get("rrule", ""))))
    return out


def _overlaps(dates, rng) -> bool:
    if not dates:
        return True
    start, end, repeats = dates
    lo, hi = rng
    if start is None and end is None:
        return True  # a task with no dates is in every range (RFC 4791 9.9)
    first = start or end
    last = end or start
    if repeats:
        return hi is None or first < hi
    if lo is not None and last < lo:
        return False
    if hi is not None and first >= hi:
        return False
    return True
