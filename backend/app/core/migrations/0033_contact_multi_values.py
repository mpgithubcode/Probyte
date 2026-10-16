"""Contacts tables: Email, Phone and Website hold several labelled values (data/multi.py), as phones keep them. Each
contact's values are read again from its card, so the extra numbers, emails and websites a phone had (kept in the
card all along, but only the first shown) are in Probyte now; a contact with no card gets its one value as a list."""
from psycopg.types.json import Jsonb


def run(conn):
    from app.formats import vcard
    fields = conn.execute(
        "SELECT f.id, f.table_id, f.system_key, f.options FROM fields f JOIN tables t ON t.id = f.table_id "
        "WHERE t.kind = 'contacts' AND f.system_key IN ('email', 'phone', 'url')").fetchall()
    by_table: dict = {}
    for f in fields:
        if (f["options"] or {}).get("multi") != "labeled":
            conn.execute("UPDATE fields SET options = options || '{\"multi\": \"labeled\"}'::jsonb WHERE id = %s", (f["id"],))
        by_table.setdefault(str(f["table_id"]), {})[f["system_key"]] = str(f["id"])
    for tid, keys in by_table.items():
        for r in conn.execute("SELECT id, data, caldav_raw, sync_status FROM records WHERE table_id = %s", (tid,)).fetchall():
            data = dict(r["data"])
            # (a contact changed here and not sent yet: its card is older than its values; those stay)
            fresh = r["caldav_raw"] and r["sync_status"] != "dirty"
            try:
                card = vcard.card_to_values(r["caldav_raw"])[1] if fresh else {}
            except Exception:
                card, fresh = {}, False
            for key, fid in keys.items():
                was = data.get(fid)
                now = card.get(key) if fresh else None
                if not now and isinstance(was, str) and was.strip():
                    now = [{"value": was.strip()}]
                if now:
                    data[fid] = now
                else:
                    data.pop(fid, None)
            if data != r["data"]:
                conn.execute("UPDATE records SET data = %s WHERE id = %s", (Jsonb(data), r["id"]))
