"""Fields that hold several values (options.multi): a contact's phone numbers, a company's websites, someone's emails.

A text, email, URL or phone field holds one value (a string) unless its options say "multi":

    "list"      several values                     [{"value": "a@x.org"}, {"value": "b@x.org"}]
    "labeled"   several values, each with a label  [{"value": "+15551234567", "label": "work"}, …]

The first value is the primary one: what a single-value place shows or sends (a sort, a one-value sync property,
a field in a formula that wants one value). Stored as that list of {value, label}; written by people and API
clients as a string (one value), a list of strings, or a list of {value, label}.
"""
from fastapi import HTTPException

MULTI_TYPES = {"text", "email", "url", "phone"}
MODES = ("list", "labeled")
MAX_VALUES = 50
MAX_LABEL = 40

# labels offered for each type (any other can be typed); also how vCard TYPE= values read and are written
LABELS = {
    "phone": ["mobile", "work", "home", "main", "iPhone", "work fax", "home fax", "pager", "other"],
    "email": ["work", "home", "school", "iCloud", "other"],
    "url": ["homepage", "work", "home", "profile", "other"],
    "text": [],
}


def mode(f) -> str | None:
    """"list" / "labeled" for a field holding several values, else None."""
    m = (f.get("options") or {}).get("multi")
    return m if m in MODES and f.get("type") in MULTI_TYPES else None


def items(v) -> list[dict]:
    """Any stored value as [{value, label}] (a single string too: a field switched to several values reads it)."""
    if v is None or v == "":
        return []
    if isinstance(v, str):
        return [{"value": v}]
    if isinstance(v, dict):
        return [v] if isinstance(v.get("value"), str) and v["value"] else []
    if isinstance(v, list):
        out = []
        for x in v:
            if isinstance(x, str) and x:
                out.append({"value": x})
            elif isinstance(x, dict) and isinstance(x.get("value"), str) and x["value"]:
                out.append({k: x[k] for k in ("value", "label") if x.get(k)})
        return out
    return [{"value": str(v)}]


def values(v) -> list[str]:
    return [x["value"] for x in items(v)]


def primary(v) -> str | None:
    vs = values(v)
    return vs[0] if vs else None


def clean(f, v, one) -> list[dict] | None:
    """What a multi-value field stores for a value written to it: each value cleaned by `one(value)` (the field
    type's own check; raises ValueError / HTTPException with the reason), labels trimmed, repeats and empties
    dropped. None when there are none."""
    labeled = mode(f) == "labeled"
    raw = v
    if isinstance(v, str):
        raw = [p for p in (x.strip() for x in v.replace("\r", "").split("\n")) if p] if "\n" in v else [v]
    out, seen = [], set()
    for i, it in enumerate(items(raw)):
        try:
            val = one(it["value"])
        except ValueError as e:
            raise HTTPException(400, f"{f['name']}: “{it['value']}” {e}")
        if val in (None, ""):
            continue
        if val in seen:
            continue
        seen.add(val)
        item = {"value": val}
        if labeled and it.get("label"):
            item["label"] = " ".join(str(it["label"]).split())[:MAX_LABEL]
        out.append(item)
        if len(out) > MAX_VALUES:
            raise HTTPException(400, f"{f['name']} holds at most {MAX_VALUES} values")
    return out or None


def as_single(v):
    """A value as a one-value field holds it (the primary one)."""
    return primary(v)
