"""The same rules for email, URL and phone number fields in every table, however a value arrives (the app, the API,
imports, public links, linked tables, type changes). frontend/src/util.ts has the same rules for instant feedback.

- email: name@domain.tld, no spaces; the domain is stored in lower case.
- url: a web address (http / https; "example.com/page" gets https:// in front). Other kinds of link (javascript:,
  data:…) are refused, because the address becomes a clickable link.
- phone: digits with the usual punctuation (+ ( ) - . / spaces) and an optional extension (ext 12, x12); 3 to 20
  digits. Stored as typed, with spaces tidied; shown as a link that dials it.

Values synced in from a phone or an address book aren't checked (they're kept as the device has them).
"""
import re

FORMAT_TYPES = {"email", "url", "phone"}

_EMAIL = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[^@\s<>\"',;.]{2,}$")
_PHONE = re.compile(r"^\+?[\d\s().\-/]+(\s*(ext\.?|x|#)\s*\d{1,6})?$", re.I)
_DOMAINISH = re.compile(r"^[\w-]+(\.[\w-]+)+(:\d+)?([/?#].*)?$")


def clean(ftype: str, value):
    """The value as stored, or None when empty; ValueError (a sentence for the user) when it isn't valid."""
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    if ftype == "email":
        text = text.removeprefix("mailto:")
        if not _EMAIL.match(text):
            raise ValueError("isn't a valid email address")
        local, _, domain = text.rpartition("@")
        return f"{local}@{domain.lower()}"
    if ftype == "url":
        if " " in text:
            raise ValueError("isn't a web address (it has spaces)")
        scheme = re.match(r"^([a-z][a-z0-9+.-]*):", text, re.I)
        if not scheme or (scheme.group(1).lower() not in ("http", "https") and _DOMAINISH.match(text)):
            if not _DOMAINISH.match(text):
                raise ValueError("isn't a web address")
            text = "https://" + text
        elif scheme.group(1).lower() not in ("http", "https"):
            raise ValueError("must be a web address starting with http:// or https://")
        if not re.match(r"^https?://[^/\s]+", text, re.I):
            raise ValueError("isn't a web address")
        return text
    if ftype == "phone":
        if not _PHONE.match(text):
            raise ValueError("isn't a phone number")
        digits = re.sub(r"\D", "", re.split(r"(?i)\s*(ext\.?|x|#)\s*", text)[0])
        if not 3 <= len(digits) <= 20:
            raise ValueError("isn't a phone number")
        return text
    return text
