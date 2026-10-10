"""A small CardDAV client (RFC 6352): find address books, read, write and delete vCards."""
import uuid
from urllib.parse import unquote, urljoin, urlparse
from xml.etree import ElementTree as ET

import requests

D, C = "DAV:", "urn:ietf:params:xml:ns:carddav"
NS = f'xmlns:d="{D}" xmlns:c="{C}"'
TIMEOUT = 30


class CardDAVError(Exception):
    pass


def _q(ns, tag):
    return f"{{{ns}}}{tag}"


class CardDAV:
    def __init__(self, url: str, username: str | None, password: str | None):
        self.url = url if url.endswith("/") else url + "/"
        self.s = requests.Session()
        if username:
            self.s.auth = (username, password or "")
        self.s.headers["User-Agent"] = "Probyte"

    # ------------------------------------------------------------ http

    def _req(self, method, url, body: str | None = None, depth: str | None = None, headers=None, ok=(200, 201, 204, 207)):
        h = {"Content-Type": "application/xml; charset=utf-8"} if body and body.lstrip().startswith("<") else {}
        if depth is not None:
            h["Depth"] = depth
        h.update(headers or {})
        r = self.s.request(method, url, data=body.encode() if body else None, headers=h, timeout=TIMEOUT)
        if r.status_code not in ok:
            if r.status_code == 401:
                raise CardDAVError("The server rejected the username or password")
            raise CardDAVError(f"{method} {urlparse(url).path} answered HTTP {r.status_code}")
        return r

    def _propfind(self, url, props: str, depth="0"):
        r = self._req("PROPFIND", url, f'<?xml version="1.0"?><d:propfind {NS}><d:prop>{props}</d:prop></d:propfind>', depth)
        return r.url, _responses(r.content)

    # ------------------------------------------------------------ discovery

    def _href_prop(self, url, ns, tag) -> str | None:
        base, resps = self._propfind(url, f"<{'d' if ns == D else 'c'}:{tag}/>")
        for _href, props in resps:
            el = props.get(_q(ns, tag))
            if el is not None:
                h = el.find(_q(D, "href"))
                if h is not None and h.text:
                    return urljoin(base, h.text.strip())
        return None

    def home(self) -> str:
        """The address book home collection of the logged-in user."""
        candidates = [self.url, urljoin(self.url, "/.well-known/carddav")]
        last = None
        for start in candidates:
            try:
                principal = self._href_prop(start, D, "current-user-principal") or start
                home = self._href_prop(principal, C, "addressbook-home-set")
                if home:
                    return home
            except (CardDAVError, requests.RequestException) as e:
                last = e
        if last:
            raise CardDAVError(str(last))
        raise CardDAVError("No address books found at this address (is CardDAV enabled on the server?)")

    def addressbooks(self) -> list[dict]:
        # The URL may point straight at an address book.
        try:
            base, resps = self._propfind(self.url, "<d:resourcetype/><d:displayname/>")
            for href, props in resps:
                if _is_book(props):
                    return [_book(base, href, props)]
        except (CardDAVError, requests.RequestException):
            pass
        home = self.home()
        base, resps = self._propfind(home, "<d:resourcetype/><d:displayname/>", "1")
        return [_book(base, href, props) for href, props in resps if _is_book(props)]

    def create_addressbook(self, name: str) -> str:
        url = urljoin(self.home(), f"{uuid.uuid4()}/")
        body = (f'<?xml version="1.0"?><d:mkcol {NS}><d:set><d:prop><d:resourcetype><d:collection/><c:addressbook/>'
                f'</d:resourcetype><d:displayname>{_xml(name)}</d:displayname></d:prop></d:set></d:mkcol>')
        self._req("MKCOL", url, body, ok=(200, 201))
        return url

    # ------------------------------------------------------------ cards

    def cards(self, book_url: str) -> list[tuple[str, str]]:
        """[(absolute href, vCard text)] of every card in an address book."""
        base, resps = self._propfind(book_url, "<d:resourcetype/><d:getcontenttype/><d:getetag/>", "1")
        own = _path(book_url)
        hrefs = []
        for href, props in resps:
            rt = props.get(_q(D, "resourcetype"))
            if _path(urljoin(base, href)) == own or (rt is not None and rt.find(_q(D, "collection")) is not None):
                continue
            ctype = (props.get(_q(D, "getcontenttype")).text or "") if props.get(_q(D, "getcontenttype")) is not None else ""
            if ctype and "vcard" not in ctype.lower() and not href.lower().endswith(".vcf"):
                continue
            hrefs.append(urlparse(urljoin(base, href)).path)
        out = []
        for i in range(0, len(hrefs), 100):
            chunk = "".join(f"<d:href>{_xml(h)}</d:href>" for h in hrefs[i:i + 100])
            body = (f'<?xml version="1.0"?><c:addressbook-multiget {NS}><d:prop><d:getetag/><c:address-data/></d:prop>'
                    f"{chunk}</c:addressbook-multiget>")
            r = self._req("REPORT", book_url, body, "1")
            for href, props in _responses(r.content):
                data = props.get(_q(C, "address-data"))
                if data is not None and data.text:
                    out.append((urljoin(r.url, href), data.text))
        return out

    def put(self, href: str, vcard: str):
        self._req("PUT", href, vcard, headers={"Content-Type": "text/vcard; charset=utf-8"}, ok=(200, 201, 204))

    def delete(self, href: str):
        self._req("DELETE", href, ok=(200, 204, 404))


# ---------------------------------------------------------------- helpers

def _responses(content: bytes) -> list[tuple[str, dict]]:
    """Multistatus -> [(href, {tag: element})], only properties reported with a 2xx status."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise CardDAVError(f"Unreadable server reply: {e}")
    out = []
    for resp in root.findall(_q(D, "response")):
        h = resp.find(_q(D, "href"))
        if h is None or not h.text:
            continue
        props = {}
        for ps in resp.findall(_q(D, "propstat")):
            st = ps.find(_q(D, "status"))
            if st is not None and st.text and " 2" not in st.text:
                continue
            p = ps.find(_q(D, "prop"))
            if p is not None:
                for el in p:
                    props[el.tag] = el
        out.append((h.text.strip(), props))
    return out


def _is_book(props) -> bool:
    rt = props.get(_q(D, "resourcetype"))
    return rt is not None and rt.find(_q(C, "addressbook")) is not None


def _book(base, href, props) -> dict:
    url = urljoin(base, href)
    dn = props.get(_q(D, "displayname"))
    name = (dn.text or "").strip() if dn is not None else ""
    return {"url": url, "name": name or unquote(url.rstrip("/").rsplit("/", 1)[-1]), "components": ["VCARD"]}


def _path(url: str) -> str:
    return unquote(urlparse(url).path).rstrip("/")


def _xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
