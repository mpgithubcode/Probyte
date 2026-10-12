"""CardDAV discovery (RFC 6352): find a user's address books and create new ones.
Reading and writing cards goes through dav.Collection like calendars do."""
import uuid
from urllib.parse import unquote, urljoin

import requests

from app.dav.client import CARD, D, NS, DavClient, DavError, q, xml_text

CardDAVError = DavError


class CardDAV(DavClient):
    def _href_prop(self, url, ns, tag) -> str | None:
        base, resps = self.propfind(url, f"<{'d' if ns == D else 'card'}:{tag}/>")
        for _href, props in resps:
            el = props.get(q(ns, tag))
            if el is not None:
                h = el.find(q(D, "href"))
                if h is not None and h.text:
                    return urljoin(base, h.text.strip())
        return None

    def home(self) -> str:
        """The address book home collection of the logged-in user."""
        last = None
        for start in (self.url, urljoin(self.url, "/.well-known/carddav")):
            try:
                principal = self._href_prop(start, D, "current-user-principal") or start
                home = self._href_prop(principal, CARD, "addressbook-home-set")
                if home:
                    return home
            except (requests.ConnectionError, requests.Timeout) as e:
                raise DavError(f"Can't reach the server: {e}")  # trying another path on the same host won't help
            except (DavError, requests.RequestException) as e:
                last = e
        if last:
            raise DavError(str(last))
        raise DavError("No address books found at this address (is CardDAV enabled on the server?)")

    def addressbooks(self) -> list[dict]:
        # The URL may point straight at an address book.
        try:
            base, resps = self.propfind(self.url, "<d:resourcetype/><d:displayname/>")
            for href, props in resps:
                if _is_book(props):
                    return [_book(base, href, props)]
        except (DavError, requests.RequestException):
            pass
        base, resps = self.propfind(self.home(), "<d:resourcetype/><d:displayname/>", "1")
        return [_book(base, href, props) for href, props in resps if _is_book(props)]

    def create_addressbook(self, name: str) -> str:
        url = urljoin(self.home(), f"{uuid.uuid4()}/")
        body = (f'<?xml version="1.0"?><d:mkcol {NS}><d:set><d:prop><d:resourcetype><d:collection/><card:addressbook/>'
                f'</d:resourcetype><d:displayname>{xml_text(name)}</d:displayname></d:prop></d:set></d:mkcol>')
        self.req("MKCOL", url, body, ok=(200, 201))
        return url


def _is_book(props) -> bool:
    rt = props.get(q(D, "resourcetype"))
    return rt is not None and rt.find(q(CARD, "addressbook")) is not None


def _book(base, href, props) -> dict:
    url = urljoin(base, href)
    dn = props.get(q(D, "displayname"))
    name = (dn.text or "").strip() if dn is not None else ""
    return {"url": url, "name": name or unquote(url.rstrip("/").rsplit("/", 1)[-1]), "components": ["VCARD"]}
