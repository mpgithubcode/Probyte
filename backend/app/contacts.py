"""Contacts tables: import contacts from vCard files (.vcf) and download them as one.

A .vcf file can hold one card or thousands (phones and Google / Outlook / iCloud exports). Each card becomes a
record, keeping the whole original card: the next sync puts it in the address book with its photo, extra phone
numbers and everything else Probyte doesn't show. vCard 2.1 files (older phones: quoted-printable text, other
character sets) are read too.

A card that is already in the table (same UID, or same email, or same name and phone) is skipped, or updated with
the card's values when asked.
"""
import quopri
import re

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from . import audit, store, vcard
from .db import connection
from .routes.common import User

router = APIRouter()
MAX_BYTES = 25 * 1024 * 1024
MAX_CARDS = 20000


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("cp1252", "replace")


def _qp_fix(card: str) -> str:
    """vCard 2.1: quoted-printable values (soft line breaks with '=') decoded to plain UTF-8 text."""
    out, lines, i = [], card.replace("\r\n", "\n").split("\n"), 0
    while i < len(lines):
        ln = lines[i]
        if re.search(r"(?i)ENCODING=QUOTED-PRINTABLE|;QUOTED-PRINTABLE[;:]", ln.split(":", 1)[0]):
            while ln.endswith("=") and i + 1 < len(lines):  # soft line break
                i += 1
                ln = ln[:-1] + lines[i]
            head, _, value = ln.partition(":")
            charset = (re.search(r"(?i)CHARSET=([\w-]+)", head) or [None, "utf-8"])[1]
            try:
                value = quopri.decodestring(value.encode("latin-1", "replace")).decode(charset, "replace")
            except LookupError:
                value = quopri.decodestring(value.encode("latin-1", "replace")).decode("utf-8", "replace")
            value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")  # line breaks inside a value, as vCard 3 writes them
            head = re.sub(r"(?i);(ENCODING=)?QUOTED-PRINTABLE", "", head)
            head = re.sub(r"(?i);CHARSET=[\w-]+", "", head)
            ln = f"{head}:{value}"
        out.append(ln)
        i += 1
    return "\r\n".join(out) + "\r\n"


def split_cards(text: str) -> list[str]:
    """Every BEGIN:VCARD … END:VCARD in a file, ready to read."""
    cards = re.findall(r"(?ims)^BEGIN:VCARD\s*$.*?^END:VCARD\s*$", text)
    return [_qp_fix(c) for c in cards]


def _key(vals: dict) -> list[str]:
    """What identifies a contact besides its UID: its email, or its name with its phone."""
    keys = []
    if vals.get("email"):
        keys.append("e:" + vals["email"].strip().lower())
    if vals.get("fn") and vals.get("phone"):
        keys.append("np:" + vals["fn"].strip().lower() + "|" + re.sub(r"\D", "", vals["phone"]))
    return keys


async def read_files(files: list[UploadFile]) -> list[tuple[str, str]]:
    texts = []
    for f in files:
        data = await f.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise HTTPException(413, f"{f.filename} is too large (25 MB at most)")
        texts.append((f.filename or "file", _decode(data)))
    return texts


@router.post("/api/tables/{table_id}/vcards")
async def import_vcards(table_id: str, files: list[UploadFile] = File(...), existing: str = Form("skip"), user=User):
    """Add the contacts in .vcf files. existing: what to do with a contact that's already here, "skip" or "update"."""
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
    from starlette.concurrency import run_in_threadpool
    texts = await read_files(files)
    return await run_in_threadpool(import_texts, table_id, texts, existing, user["id"])


def import_texts(table_id: str, texts: list[tuple[str, str]], existing: str, user_id) -> dict:
    """The contacts of vCard files' texts into a contacts table (the app, a public link that may add, a linked table)."""
    created = updated = skipped = 0
    errors: list[str] = []
    user = {"id": user_id}
    with connection() as conn:
        table = store.get_table(conn, table_id)
        if table["kind"] != "contacts":
            raise HTTPException(400, "vCard files can be imported into contacts tables")
        fields = store.get_fields(conn, table_id)
        keys = {f["system_key"]: f for f in fields if f.get("system_key")}
        rows = conn.execute("SELECT id, caldav_uid, data FROM records WHERE table_id = %s AND NOT deleted", (table_id,)).fetchall()
        by_uid = {r["caldav_uid"]: r for r in rows if r["caldav_uid"]}
        by_key = {}
        for r in rows:
            vals = {k: r["data"].get(str(f["id"])) for k, f in keys.items()}
            for k in _key(vals):
                by_key.setdefault(k, r)
        cards = [(name, c) for name, text in texts for c in split_cards(text)]
        if not cards:
            raise HTTPException(400, "No contacts found: a vCard file has BEGIN:VCARD … END:VCARD in it")
        if len(cards) > MAX_CARDS:
            raise HTTPException(400, f"That's more than {MAX_CARDS:,} contacts at once; split the file")
        for n, (name, raw) in enumerate(cards, start=1):
            try:
                uid, vals = vcard.card_to_values(raw)
            except Exception as e:  # an unreadable card doesn't stop the rest
                errors.append(f"{name}, card {n}: {e}")
                continue
            if not vals.get("fn"):
                errors.append(f"{name}, card {n}: no name, email or phone")
                continue
            data = {}
            for k, v in vals.items():
                f = keys.get(k)
                if f and v not in (None, "", []):
                    store.ensure_choices(conn, f, v)
                    data[str(f["id"])] = v
            match = by_uid.get(uid) if uid else None
            match = match or next((by_key[k] for k in _key(vals) if k in by_key), None)
            if match:
                if existing != "update":
                    skipped += 1
                    continue
                store.update_record(conn, match["id"], data, user["id"])  # pushed to the address book by the next sync
                updated += 1
                continue
            if not uid:
                uid = vcard.new_uid()
                raw = re.sub(r"(?i)END:VCARD\s*$", f"UID:{uid}\r\nEND:VCARD\r\n", raw.rstrip())
            if uid in by_uid:  # the same card twice in the file(s)
                skipped += 1
                continue
            data = store.apply_defaults([f for f in fields if not f.get("system_key")], data, user["id"])
            r = store.insert_record(conn, table_id, data, user_id=user["id"], caldav_uid=uid, caldav_raw=raw, sync_status="dirty")
            audit.note(conn, table_id, r["id"], "created")
            store._changed(conn, table, "record.created", r["id"])  # webhooks, and a push to the address book
            by_uid[uid] = r
            for k in _key(vals):
                by_key.setdefault(k, r)
            created += 1
        if created or updated:
            store.schedule_sync(table_id, 1, conn=conn)
    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors[:20], "error_count": len(errors)}


@router.get("/api/tables/{table_id}/vcards")
def export_vcards(table_id: str, user=User):
    """All the table's contacts as one .vcf file (with everything each card had: photos, extra numbers…)."""
    with connection() as conn:
        store.get_table(conn, table_id, user["id"])
    return vcards_response(table_id)


def vcards_response(table_id: str, only: set[str] | None = None) -> Response:
    """The contacts (all, or the record ids in `only`: what a public link's view shows) as one .vcf file."""
    with connection() as conn:
        table = store.get_table(conn, table_id)
        if table["kind"] != "contacts":
            raise HTTPException(400, "Only contacts tables can be downloaded as vCards")
        keys = {f["system_key"]: str(f["id"]) for f in store.get_fields(conn, table_id) if f.get("system_key")}
        rows = conn.execute("SELECT id, caldav_uid, caldav_raw, data FROM records WHERE table_id = %s AND NOT deleted ORDER BY seq",
                            (table_id,)).fetchall()
    if only is not None:
        rows = [r for r in rows if str(r["id"]) in only]
    cards = []
    for r in rows:
        vals = {k: r["data"].get(fid) for k, fid in keys.items()}
        cards.append(vcard.build_vcard(r["caldav_uid"] or vcard.new_uid(), vals, r["caldav_raw"]))
    slug = re.sub(r"[^A-Za-z0-9]+", "-", table["name"]).strip("-") or "contacts"
    return Response("".join(c if c.endswith("\r\n") else c + "\r\n" for c in cards), media_type="text/vcard; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{slug}.vcf"'})
