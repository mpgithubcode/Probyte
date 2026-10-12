"""The same rules for email, URL and phone number fields in every table, however a value arrives (the app, the API,
imports, public links, linked tables, type changes). frontend/src/util.ts has the same rules for instant feedback.

- email: name@domain.tld, no spaces; the domain is stored in lower case.
- url: a web address (http / https; "example.com/page" gets https:// in front). Other kinds of link (javascript:,
  data:…) are refused, because the address becomes a clickable link.
- phone: whatever is around the number goes (labels, "tel:", letters, punctuation); what's kept is the digits, a
  leading + and an extension (ext 12, x12, #12): stored as "+15551234567 x12", 3 to 20 digits. Everywhere it's shown
  the same way (format_phone; frontend/src/util.ts formatPhone): (555) 123-4567, +1 (555) 123-4567,
  +44 207 946 0958, 123-4567, with " ext. 12".

Values synced in from a phone or an address book are tidied the same way when they can be (phone_or_raw), else kept.
"""
import re

FORMAT_TYPES = {"email", "url", "phone"}

_EMAIL = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[^@\s<>\"',;.]{2,}$")
_EXT = re.compile(r"(?:\b(?:ext|extension)\b\.?|;ext=|(?:(?<=\d)|\b)x|#)\s*(\d{1,6})\s*$", re.I)
_KEYPAD = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "22233344455566677778889999")
# country codes with 1 or 2 digits (the rest have 3), to show international numbers as +CC rest
_CC1 = {"1", "7"}
_CC2 = {"20", "27", "30", "31", "32", "33", "34", "36", "39", "40", "41", "43", "44", "45", "46", "47", "48", "49", "51", "52",
        "53", "54", "55", "56", "57", "58", "60", "61", "62", "63", "64", "65", "66", "81", "82", "84", "86", "90", "91", "92",
        "93", "94", "95", "98"}
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
        return phone(text)
    return text
    return text


def _phone_parts(text: str):
    """(plus, digits, extension) of a phone number written any way, or None when there's no number in it."""
    t = re.sub(r"(?i)^\s*tel:", "", str(text)).strip()
    ext = None
    m = _EXT.search(t)
    if m and re.search(r"\d", t[:m.start()]):
        ext, t = m.group(1), t[:m.start()]
    t = re.sub(r"[A-Za-z]+\s*:", " ", t)               # "Mobile:", "Phone:"
    # 1-800-FLOWERS: capital letters after the digits are the keypad's
    t = re.sub(r"(?<=[\d\s\-.])[A-Z]{3,7}\b", lambda m: m.group(0).translate(_KEYPAD), t) if re.search(r"\d", t) else t
    plus = bool(re.match(r"^[^\d]*\+", t))
    digits = re.sub(r"\D", "", t)
    if not 3 <= len(digits) <= 20:
        return None
    if not plus and len(digits) == 11 and digits[0] == "1":  # 1 555 123 4567: North America with its country code
        plus = True
    elif not plus and digits.startswith("00") and len(digits) >= 10:  # 0044…: the international prefix
        plus, digits = True, digits[2:]
    elif not plus and digits.startswith("011") and len(digits) >= 13:  # 011 44…: the same, dialled from North America
        plus, digits = True, digits[3:]
    return plus, digits, ext


def phone(text: str) -> str:
    """The stored form: +15551234567 x12. ValueError when there's no phone number in it."""
    parts = _phone_parts(text)
    if not parts:
        raise ValueError("isn't a phone number")
    plus, digits, ext = parts
    return ("+" if plus else "") + digits + (f" x{ext}" if ext else "")


def phone_or_raw(text):
    """For values from devices and address books: the stored form when it is a number, else as it was."""
    if not text:
        return text
    try:
        return phone(text)
    except ValueError:
        return text


def _groups(n: str) -> str:
    """207 946 0958, 30 123 4567, 1234 5678: groups of three, the last of four (from the end)."""
    out = [n[-4:]] if len(n) > 4 else [n]
    n = n[:-4] if len(n) > 4 else ""
    while n:
        out.insert(0, n[-3:])
        n = n[:-3]
    if len(out) > 1 and len(out[0]) == 1:
        first = out.pop(0)
        out[0] = first + out[0]
    return " ".join(out)


def format_phone(value) -> str:
    """How a phone number is shown, everywhere."""
    if value is None or value == "":
        return ""
    parts = _phone_parts(str(value))
    if not parts:
        return str(value)
    plus, d, ext = parts
    if plus and len(d) == 11 and d[0] == "1":
        s = f"+1 ({d[1:4]}) {d[4:7]}-{d[7:]}"
    elif plus:
        cc = d[:1] if d[:1] in _CC1 else d[:2] if d[:2] in _CC2 else d[:3]
        s = f"+{cc} {_groups(d[len(cc):])}".strip()
    elif len(d) == 10:
        s = f"({d[:3]}) {d[3:6]}-{d[6:]}"
    elif len(d) == 7:
        s = f"{d[:3]}-{d[3:]}"
    else:
        s = d
    return s + (f" ext. {ext}" if ext else "")
