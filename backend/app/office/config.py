"""The Document Server settings (Settings → Document editing, admin) and which files it opens how."""
import re

from fastapi import HTTPException

from app.core.secretbox import seal, unseal
from app.files import catalog as files

WORD = {"docx", "docm", "dotx", "dotm", "odt", "ott", "rtf", "txt", "doc", "dot", "epub", "fb2", "html", "htm", "mht", "xml"}
CELL = {"xlsx", "xlsm", "xltx", "xltm", "ods", "ots", "csv", "xls", "xlt"}
SLIDE = {"pptx", "pptm", "ppsx", "potx", "potm", "odp", "otp", "ppt", "pps", "pot"}
PDF = {"pdf", "djvu", "xps", "oxps"}
# opened for editing; the rest (old binary formats, PDF…) open to read
EDITABLE = {"docx", "docm", "dotx", "odt", "ott", "rtf", "txt", "xlsx", "xlsm", "xltx", "ods", "ots", "csv", "pptx", "pptm",
            "ppsx", "potx", "odp", "otp"}
# opened for editing as their newer format (the saved version replaces the file, with the new extension)
CONVERT = {"doc": "docx", "dot": "docx", "dotm": "docx", "xls": "xlsx", "xlt": "xlsx", "xltm": "xlsx",
           "ppt": "pptx", "pps": "pptx", "pot": "pptx", "potm": "pptx"}
SETTINGS = ("url", "secret", "probyte_url", "server_url", "via_probyte", "browser_scheme", "verify_tls")
PREFIX = "/office-ds"   # where Probyte relays the Document Server for browsers
CONVERTED_DIR = files.ATTACH_DIR / "office"   # converted copies, while they're edited


def settings(conn) -> dict:
    rows = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM app_settings WHERE key LIKE 'office.%'").fetchall()}
    out = {k: rows.get(f"office.{k}", "") for k in SETTINGS}
    out["secret"] = unseal(out["secret"]) or ""
    out["via_probyte"] = rows.get("office.via_probyte", "1") == "1"   # (on unless turned off)
    # the protocol browsers use to reach Probyte, as the Document Server is told it (relay): "" = as it arrives
    out["browser_scheme"] = out["browser_scheme"] if out["browser_scheme"] in ("http", "https") else ""
    # check the Document Server's TLS certificate (off: an HTTPS Document Server with a self-signed one)
    out["verify_tls"] = rows.get("office.verify_tls", "1") == "1"
    return out


def public(cfg: dict) -> dict:
    """The settings as the admin sees them (never the secret)."""
    return {"url": cfg["url"], "probyte_url": cfg["probyte_url"], "server_url": cfg["server_url"], "via_probyte": cfg["via_probyte"],
            "browser_scheme": cfg["browser_scheme"], "verify_tls": cfg["verify_tls"], "has_secret": bool(cfg["secret"])}


def clean_url(u: str) -> str:
    u = (u or "").strip().rstrip("/")
    if u and not re.match(r"^https?://[^/\s]+", u):
        raise HTTPException(400, f"“{u}” isn't an address starting with http:// or https://")
    return u


def save(conn, *, url, probyte_url, server_url, via_probyte: bool, browser_scheme: str, verify_tls: bool, secret: str | None):
    """secret: None keeps the saved one; "" clears it (only with no Document Server)."""
    vals = {"url": clean_url(url), "probyte_url": clean_url(probyte_url), "server_url": clean_url(server_url),
            "via_probyte": "1" if via_probyte else "", "verify_tls": "1" if verify_tls else "0"}
    if browser_scheme not in ("", "http", "https"):
        raise HTTPException(400, "The protocol is automatic, https or http")
    vals["browser_scheme"] = browser_scheme
    for k, v in vals.items():
        conn.execute("INSERT INTO app_settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                     (f"office.{k}", v))
    if secret is not None and (secret or not vals["url"]):
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('office.secret', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                     (seal(secret) if secret else "",))


def enabled(conn) -> bool:
    return bool(settings(conn)["url"])


def doc_type(name: str) -> str | None:
    e = files._ext(name)
    return "word" if e in WORD else "cell" if e in CELL else "slide" if e in SLIDE else "pdf" if e in PDF else None
