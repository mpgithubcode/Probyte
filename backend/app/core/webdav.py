"""WebDAV plumbing shared by CalDAV and CardDAV: requests, multistatus parsing, and an incremental view of a
collection (calendar or address book).

Collection.state() is the server's change tag for the whole collection (CTag or sync-token): when it hasn't
changed, nothing inside has. Collection.etags() lists every item with its ETag, so only new or changed items
need downloading with Collection.fetch() (a calendar-multiget / addressbook-multiget REPORT).
"""
import ipaddress
import socket
from urllib.parse import unquote, urljoin, urlparse
from xml.etree import ElementTree as ET

import requests

D, CAL, CARD, CS = "DAV:", "urn:ietf:params:xml:ns:caldav", "urn:ietf:params:xml:ns:carddav", "http://calendarserver.org/ns/"
NS = f'xmlns:d="{D}" xmlns:cal="{CAL}" xmlns:card="{CARD}" xmlns:cs="{CS}"'
TIMEOUT = (8, 30)  # seconds to connect, seconds to wait for an answer: an unreachable server fails fast
BATCH = 100


class DavError(Exception):
    pass


def check_url(url: str) -> str:
    """A CalDAV/CardDAV server address a user may connect Probyte to. Servers on your own network are fine
    (a home Nextcloud); loopback, link-local (cloud metadata services), multicast and reserved addresses never are."""
    url = (url or "").strip()
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise DavError("Enter the server address, starting with https://")
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))}
    except (socket.gaierror, UnicodeError):
        return url  # can't be resolved now: connecting will say so
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
            raise DavError("That address can't be used for a calendar server")
    return url


def q(ns, tag):
    return f"{{{ns}}}{tag}"


def xml_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def path_of(url: str) -> str:
    """A URL or href as a comparable path (servers may answer with either)."""
    return unquote(urlparse(url).path).rstrip("/")


def responses(content: bytes) -> list[tuple[str, dict]]:
    """Multistatus -> [(href, {tag: element})], only properties reported with a 2xx status."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise DavError(f"Unreadable server reply: {e}")
    out = []
    for resp in root.findall(q(D, "response")):
        h = resp.find(q(D, "href"))
        if h is None or not h.text:
            continue
        props = {}
        for ps in resp.findall(q(D, "propstat")):
            st = ps.find(q(D, "status"))
            if st is not None and st.text and " 2" not in st.text:
                continue
            p = ps.find(q(D, "prop"))
            if p is not None:
                for el in p:
                    props[el.tag] = el
        out.append((h.text.strip(), props))
    return out


class DavClient:
    def __init__(self, url: str, username: str | None, password: str | None):
        self.url = url if url.endswith("/") else url + "/"
        self.s = requests.Session()
        if username:
            self.s.auth = (username, password or "")
        self.s.headers["User-Agent"] = "Probyte"

    def req(self, method, url, body: str | None = None, depth: str | None = None, headers=None, ok=(200, 201, 204, 207)):
        h = {"Content-Type": "application/xml; charset=utf-8"} if body and body.lstrip().startswith("<") else {}
        if depth is not None:
            h["Depth"] = depth
        h.update(headers or {})
        r = self.s.request(method, url, data=body.encode() if body else None, headers=h, timeout=TIMEOUT)
        if r.status_code not in ok:
            if r.status_code == 401:
                raise DavError("The server rejected the username or password")
            raise DavError(f"{method} {urlparse(url).path} answered HTTP {r.status_code}")
        return r

    def propfind(self, url, props: str, depth="0"):
        r = self.req("PROPFIND", url, f'<?xml version="1.0"?><d:propfind {NS}><d:prop>{props}</d:prop></d:propfind>', depth)
        return r.url, responses(r.content)


class Collection(DavClient):
    """One calendar or address book, read incrementally."""

    def __init__(self, url, username, password, kind: str):
        super().__init__(url, username, password)
        self.kind = kind  # "calendar" | "addressbook"

    def state(self) -> str | None:
        try:
            _, resps = self.propfind(self.url, "<cs:getctag/><d:sync-token/>")
        except DavError:
            return None
        for _, props in resps:
            vals = [(props[t].text or "").strip() for t in (q(CS, "getctag"), q(D, "sync-token")) if t in props]
            if any(vals):
                return "|".join(vals)
        return None

    def etags(self) -> dict[str, str]:
        """{absolute item URL: ETag} for every item in the collection."""
        base, resps = self.propfind(self.url, "<d:getetag/><d:resourcetype/>", "1")
        own = path_of(self.url)
        out = {}
        for href, props in resps:
            url = urljoin(base, href)
            rt = props.get(q(D, "resourcetype"))
            if path_of(url) == own or (rt is not None and rt.find(q(D, "collection")) is not None):
                continue
            et = props.get(q(D, "getetag"))
            out[url] = (et.text or "").strip() if et is not None else ""
        return out

    def fetch(self, urls: list[str]) -> dict[str, tuple[str, str]]:
        """{absolute URL: (ETag, body)} for the given items."""
        if self.kind == "calendar":
            report, data = "cal:calendar-multiget", "<cal:calendar-data/>"
            data_tag = q(CAL, "calendar-data")
        else:
            report, data = "card:addressbook-multiget", "<card:address-data/>"
            data_tag = q(CARD, "address-data")
        out = {}
        for i in range(0, len(urls), BATCH):
            hrefs = "".join(f"<d:href>{xml_text(urlparse(u).path)}</d:href>" for u in urls[i:i + BATCH])
            body = f'<?xml version="1.0"?><{report} {NS}><d:prop><d:getetag/>{data}</d:prop>{hrefs}</{report}>'
            r = self.req("REPORT", self.url, body, "1")
            for href, props in responses(r.content):
                el = props.get(data_tag)
                if el is not None and el.text:
                    et = props.get(q(D, "getetag"))
                    out[urljoin(r.url, href)] = ((et.text or "").strip() if et is not None else "", el.text)
        return out

    def put(self, url: str, body: str) -> str | None:
        """Write an item; returns its new ETag when the server says."""
        ctype = "text/calendar; charset=utf-8" if self.kind == "calendar" else "text/vcard; charset=utf-8"
        r = self.req("PUT", url, body, headers={"Content-Type": ctype}, ok=(200, 201, 204))
        return r.headers.get("ETag")

    def delete(self, url: str):
        self.req("DELETE", url, ok=(200, 204, 404))
