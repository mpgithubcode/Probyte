"""Conversion between Probyte records and vCards (CardDAV contacts).

Only the properties Probyte shows are touched: when a card is written back, every other line of the
original card (photos, extra phone numbers, Apple labels, X- properties...) is kept as it was, and a
managed property is only rewritten when its value actually changed.

EMAIL, TEL, ADR and URL can repeat; Probyte manages the first of each (the preferred one if marked).
"""
import re
import uuid
from datetime import datetime, timezone


# (system_key, name, type, options, is_primary)
CONTACT_FIELDS = [
    ("fn", "Name", "text", {}, True),
    ("first", "First Name", "text", {}, False),
    ("last", "Last Name", "text", {}, False),
    ("org", "Company", "text", {}, False),
    ("title", "Job Title", "text", {}, False),
    ("email", "Email", "email", {}, False),
    ("phone", "Phone", "text", {}, False),
    ("address", "Address", "long_text", {}, False),
    ("birthday", "Birthday", "date", {}, False),
    ("url", "Website", "url", {}, False),
    ("note", "Notes", "long_text", {}, False),
    ("categories", "Groups", "multi_select", {"choices": []}, False),
]


# ---------------------------------------------------------------- line level

class Prop:
    __slots__ = ("group", "name", "params", "value")

    def __init__(self, group: str, name: str, params: str, value: str):
        self.group, self.name, self.params, self.value = group, name, params, value

    def line(self) -> str:
        return f"{self.group + '.' if self.group else ''}{self.name}{self.params}:{self.value}"

    def has_type(self, t: str) -> bool:
        return bool(re.search(rf"(?i)type=([^;:]*,)?\"?{t}\b", self.params)) or bool(re.search(rf"(?i);{t}\b", self.params))


def _unfold(raw: str) -> list[str]:
    return [ln for ln in re.sub(r"\r?\n[ \t]", "", raw).replace("\r\n", "\n").split("\n") if ln.strip()]


def _parse_line(ln: str) -> Prop | None:
    # The value starts at the first ':' outside a quoted parameter value.
    quoted = False
    for i, ch in enumerate(ln):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            head, value = ln[:i], ln[i + 1:]
            break
    else:
        return None
    m = re.match(r"^(?:([A-Za-z0-9-]+)\.)?([A-Za-z0-9-]+)(.*)$", head)
    if not m:
        return None
    return Prop(m.group(1) or "", m.group(2), m.group(3), value)  # names are case-insensitive; keep the original spelling


def _fold(line: str) -> str:
    """Fold to 75 octets per RFC 6350 without splitting UTF-8 characters."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode())
        if size + n > (75 if not out else 74):
            out.append(cur)
            cur, size = "", 0
        cur += ch
        size += n
    out.append(cur)
    return "\r\n ".join(out)


def unescape(v: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def _split(v: str, sep: str) -> list[str]:
    """Split a structured value on unescaped separators, then unescape each part."""
    parts, cur, i = [], "", 0
    while i < len(v):
        if v[i] == "\\" and i + 1 < len(v):
            cur += v[i:i + 2]
            i += 2
            continue
        if v[i] == sep:
            parts.append(cur)
            cur = ""
        else:
            cur += v[i]
        i += 1
    parts.append(cur)
    return [unescape(p) for p in parts]


class Card:
    def __init__(self, raw: str | None):
        self.props: list[Prop] = []
        if raw:
            lines = _unfold(raw)
            # Keep only the first VCARD; BEGIN/END are re-added on output.
            inside = False
            for ln in lines:
                up = ln.upper()
                if up.startswith("BEGIN:VCARD"):
                    inside = True
                    continue
                if up.startswith("END:VCARD"):
                    break
                if inside and (p := _parse_line(ln)):
                    self.props.append(p)

    def all(self, name: str) -> list[Prop]:
        return [p for p in self.props if p.name.upper() == name]

    def first(self, name: str) -> Prop | None:
        props = self.all(name)
        for p in props:
            if p.has_type("pref") or re.search(r"(?i)pref=1", p.params):
                return p
        return props[0] if props else None

    def get(self, name: str) -> str | None:
        p = self.first(name)
        return p.value if p else None

    def set(self, name: str, value: str | None, params: str = ""):
        """Replace the managed occurrence of `name` (value already escaped), or remove it."""
        p = self.first(name)
        if value is None or value == "":
            if p:
                self.props.remove(p)
            return
        if p:
            p.value = value
        else:
            self.props.append(Prop("", name, params, value))

    def to_vcard(self) -> str:
        return "\r\n".join(["BEGIN:VCARD", *(_fold(p.line()) for p in self.props), "END:VCARD"]) + "\r\n"


# ---------------------------------------------------------------- values

def _text(card: Card, name: str) -> str | None:
    v = card.get(name)
    return unescape(v).strip() or None if v is not None else None


def _uri(card: Card, name: str, scheme: str) -> str | None:
    v = _text(card, name)
    if v and v.lower().startswith(scheme):
        v = v[len(scheme):]
    return v or None


def _bday(v: str | None) -> str | None:
    if not v:
        return None
    v = v.strip()
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})", v)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None  # e.g. "--0412" (no year): left as is on the server


def _address(card: Card) -> str | None:
    p = card.first("ADR")
    if not p:
        return None
    parts = (_split(p.value, ";") + [""] * 7)[:7]
    _pobox, ext, street, city, region, code, country = (x.strip() for x in parts)
    line2 = " ".join(x for x in (", ".join(x for x in (city, region) if x), code) if x)
    lines = [x for x in (", ".join(x for x in (street, ext) if x), line2, country) if x]
    return "\n".join(lines) or None


def card_to_values(raw: str) -> tuple[str | None, dict]:
    card = Card(raw)
    n = (_split(card.get("N") or "", ";") + [""] * 5)[:5]
    org = card.get("ORG")
    cats = card.get("CATEGORIES")
    vals = {
        "fn": _text(card, "FN"),
        "first": n[1].strip() or None,
        "last": n[0].strip() or None,
        "org": (_split(org, ";")[0].strip() or None) if org else None,
        "title": _text(card, "TITLE"),
        "email": _uri(card, "EMAIL", "mailto:"),
        "phone": _uri(card, "TEL", "tel:"),
        "address": _address(card),
        "birthday": _bday(card.get("BDAY")),
        "url": _text(card, "URL"),
        "note": _text(card, "NOTE"),
        "categories": [c.strip() for c in _split(cats, ",") if c.strip()] if cats else [],
    }
    if not vals["fn"]:
        vals["fn"] = " ".join(x for x in (vals["first"], vals["last"]) if x) or vals["org"] or vals["email"] or vals["phone"]
    uid = _text(card, "UID")
    return uid, vals


def _norm(v):
    if v in ("", [], None):
        return None
    return v


def build_vcard(uid: str, vals: dict, raw: str | None = None) -> str:
    """Build (or update, keeping everything Probyte doesn't manage) a vCard."""
    card = Card(raw)
    if not card.props:
        card.props = [Prop("", "VERSION", "", "3.0"), Prop("", "PRODID", "", "-//Probyte//EN")]
    old = card_to_values(card.to_vcard())[1] if raw else {}
    changed = lambda k: _norm(vals.get(k)) != _norm(old.get(k))  # noqa: E731

    first, last = vals.get("first") or "", vals.get("last") or ""
    fn = vals.get("fn") or " ".join(x for x in (first, last) if x) or vals.get("org") or ""
    if not first and not last and fn and not card.first("N") and not vals.get("org"):
        # Phones sort and display by N: derive it from the name for cards made in Probyte.
        bits = fn.split()
        first, last = (" ".join(bits[:-1]), bits[-1]) if len(bits) > 1 else (fn, "")

    card.set("UID", escape(uid))
    if changed("fn") or not card.first("FN"):
        card.set("FN", escape(fn) or " ")
    if changed("first") or changed("last") or not card.first("N"):
        rest = (_split(card.get("N") or "", ";") + [""] * 5)[2:5]
        card.set("N", ";".join(escape(x) for x in (last, first, *rest)))
    if changed("org"):
        org = card.get("ORG")
        extra = _split(org, ";")[1:] if org else []
        card.set("ORG", ";".join(escape(x) for x in [vals.get("org") or "", *extra]) if vals.get("org") else None)
    if changed("title"):
        card.set("TITLE", escape(vals.get("title") or ""))
    if changed("email"):
        card.set("EMAIL", escape(vals.get("email") or ""), ";TYPE=INTERNET")
    if changed("phone"):
        card.set("TEL", escape(vals.get("phone") or ""), ";TYPE=CELL")
    if changed("address"):
        addr = (vals.get("address") or "").strip()
        # Free text can't be split back into street/city/...; it goes into the street part.
        card.set("ADR", ";;" + escape(addr.replace("\n", ", ")) + ";;;;" if addr else None, ";TYPE=HOME")
    if changed("birthday"):
        card.set("BDAY", (vals.get("birthday") or "")[:10], ";VALUE=date" if not card.first("BDAY") else "")
    if changed("url"):
        card.set("URL", escape(vals.get("url") or ""))
    if changed("note"):
        card.set("NOTE", escape(vals.get("note") or ""))
    if changed("categories"):
        card.set("CATEGORIES", ",".join(escape(str(c)) for c in vals.get("categories") or []))
    card.set("REV", datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    # VERSION must come first.
    card.props.sort(key=lambda p: p.name.upper() != "VERSION")
    return card.to_vcard()


def new_uid() -> str:
    return str(uuid.uuid4()).upper()
