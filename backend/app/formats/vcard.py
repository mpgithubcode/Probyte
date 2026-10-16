"""Conversion between Probyte records and vCards (CardDAV contacts).

Only the properties Probyte shows are touched: when a card is written back, every other line of the
original card (photos, extra phone numbers, Apple labels, X- properties...) is kept as it was, and a
managed property is only rewritten when its value actually changed.

EMAIL, TEL and URL can repeat: they're fields with several labelled values (data/multi.py), every one read and
written, with its label (TYPE=WORK, CELL…, or the label Apple keeps in an item's X-ABLabel). ADR can repeat too;
Probyte manages the first (the preferred one if marked).
"""
import re
import uuid
from datetime import datetime, timezone

from app.formats import fieldformats


# (system_key, name, type, options, is_primary)
CONTACT_FIELDS = [
    ("fn", "Name", "text", {}, True),
    ("first", "First Name", "text", {}, False),
    ("last", "Last Name", "text", {}, False),
    ("org", "Company", "text", {}, False),
    ("title", "Job Title", "text", {}, False),
    ("email", "Email", "email", {"multi": "labeled"}, False),
    ("phone", "Phone", "phone", {"multi": "labeled"}, False),
    ("address", "Address", "long_text", {}, False),
    ("birthday", "Birthday", "date", {}, False),
    ("url", "Website", "url", {"multi": "labeled"}, False),
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


# vCard TYPE values <-> labels (the ones phones show); anything else Apple keeps as an item's X-ABLabel
_TYPE_LABEL = {"CELL": "mobile", "MOBILE": "mobile", "WORK": "work", "HOME": "home", "MAIN": "main", "IPHONE": "iPhone",
               "PAGER": "pager", "OTHER": "other", "SCHOOL": "school"}
_APPLE = re.compile(r"^_\$!<(.+)>!\$_$")
_APPLE_KNOWN = {"mobile": "mobile", "home": "home", "work": "work", "main": "main", "other": "other", "homepage": "homepage",
                "homefax": "home fax", "workfax": "work fax", "pager": "pager", "school": "school"}
MULTI_PROPS = {"email": ("EMAIL", "mailto:"), "phone": ("TEL", "tel:"), "url": ("URL", "")}


def _types(p: Prop) -> list[str]:
    out = []
    for m in re.finditer(r"(?i)type=(\"[^\"]*\"|[^;:]*)", p.params):
        out += [t.strip().strip('"').upper() for t in m.group(1).split(",") if t.strip()]
    out += [x.upper() for x in re.findall(r";([A-Za-z]+)(?=;|$)", p.params)]   # vCard 2.1: ;CELL;WORK
    return out


def _label(card: "Card", p: Prop) -> str | None:
    if p.group:   # Apple: item1.TEL + item1.X-ABLabel:_$!<Mobile>!$_ (or the user's own label)
        lab = next((q for q in card.props if q.group.lower() == p.group.lower() and q.name.upper() == "X-ABLABEL"), None)
        if lab and lab.value.strip():
            v = unescape(lab.value).strip()
            m = _APPLE.match(v)
            return _APPLE_KNOWN.get(m.group(1).lower().replace(" ", ""), m.group(1).lower()) if m else v[:40]
    types = _types(p)
    if "FAX" in types:
        return "work fax" if "WORK" in types else "home fax" if "HOME" in types else "fax"
    return next((_TYPE_LABEL[t] for t in types if t in _TYPE_LABEL), None)


def _multi(card: "Card", key: str) -> list[dict] | None:
    """Every EMAIL / TEL / URL of the card, the preferred one first, each with its label."""
    name, scheme = MULTI_PROPS[key]
    props = card.all(name)
    pref = card.first(name)
    if pref in props:
        props = [pref] + [p for p in props if p is not pref]
    out = []
    for p in props:
        v = unescape(p.value).strip()
        if scheme and v.lower().startswith(scheme):
            v = v[len(scheme):]
        if key == "phone":
            v = fieldformats.phone_or_raw(v)
        if v and not any(x["value"] == v for x in out):
            item = {"value": v}
            if (lab := _label(card, p)):
                item["label"] = lab
            out.append(item)
    return out or None


_LABEL_TYPE = {"mobile": "CELL", "work": "WORK", "home": "HOME", "main": "MAIN", "iPhone": "IPHONE", "pager": "PAGER",
               "other": "OTHER", "school": "SCHOOL", "work fax": "WORK,FAX", "home fax": "HOME,FAX", "fax": "FAX"}


def _set_multi(card: "Card", key: str, items: list[dict]):
    """Replace every EMAIL / TEL / URL of the card with these (their labels as TYPE=, or Apple's X-ABLabel)."""
    name, _ = MULTI_PROPS[key]
    old = card.all(name)
    groups = {p.group.lower() for p in old if p.group}
    card.props = [p for p in card.props if p not in old and not (p.group and p.group.lower() in groups and p.name.upper() == "X-ABLABEL")]
    used = {int(m.group(1)) for p in card.props if p.group and (m := re.match(r"(?i)item(\d+)$", p.group))}
    n = max(used, default=0)
    base = {"email": "INTERNET", "phone": "VOICE", "url": ""}[key]
    for i, it in enumerate(items):
        label = it.get("label")
        known = _LABEL_TYPE.get(label) if label else None
        types = [t for t in [base, *(known.split(",") if known else []), "PREF" if i == 0 and len(items) > 1 else ""] if t]
        params = f";TYPE={','.join(types)}" if types else ""
        if label and not known:   # a label of their own: Apple's way, which phones show
            n += 1
            card.props.append(Prop(f"item{n}", name, params, escape(it["value"])))
            card.props.append(Prop(f"item{n}", "X-ABLabel", "", escape(label)))
        else:
            card.props.append(Prop("", name, params, escape(it["value"])))


def _primary(v) -> str | None:
    if isinstance(v, list):
        return next((x.get("value") for x in v if isinstance(x, dict) and x.get("value")), None)
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
        "email": _multi(card, "email"),
        "phone": _multi(card, "phone"),
        "address": _address(card),
        "birthday": _bday(card.get("BDAY")),
        "url": _multi(card, "url"),
        "note": _text(card, "NOTE"),
        "categories": [c.strip() for c in _split(cats, ",") if c.strip()] if cats else [],
    }
    if not vals["fn"]:
        vals["fn"] = " ".join(x for x in (vals["first"], vals["last"]) if x) or vals["org"] or _primary(vals["email"]) or _primary(vals["phone"])
    uid = _text(card, "UID")
    return uid, vals


def _norm(v):
    if v in ("", [], None):
        return None
    return v


def _items_of(v) -> list[dict]:
    """A contact's email / phone / website as it's kept (several labelled values), or a plain value from before."""
    if isinstance(v, str):
        return [{"value": v}] if v.strip() else []
    return [{k: x[k] for k in ("value", "label") if x.get(k)} for x in (v or []) if isinstance(x, dict) and x.get("value")]


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
    for key in ("email", "phone", "url"):   # several labelled values: all of them, when they changed
        if _items_of(vals.get(key)) != _items_of(old.get(key)):
            _set_multi(card, key, _items_of(vals.get(key)))
    if changed("address"):
        addr = (vals.get("address") or "").strip()
        # Free text can't be split back into street/city/...; it goes into the street part.
        card.set("ADR", ";;" + escape(addr.replace("\n", ", ")) + ";;;;" if addr else None, ";TYPE=HOME")
    if changed("birthday"):
        card.set("BDAY", (vals.get("birthday") or "")[:10], ";VALUE=date" if not card.first("BDAY") else "")
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
