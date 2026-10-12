"""Phone numbers already stored get the one stored form (+15551234567 x12): labels, letters and punctuation go.
Values that aren't numbers stay as they are. A synced contact's card on the phone isn't touched (the number is the
same, so nothing is sent back)."""
from psycopg.types.json import Jsonb

from app.data.fieldformats import phone_or_raw


def run(conn):
    for f in conn.execute("SELECT id, table_id FROM fields WHERE type = 'phone'").fetchall():
        fid = str(f["id"])
        rows = conn.execute("SELECT id, data->%s AS v FROM records WHERE table_id = %s AND data ? %s",
                            (fid, f["table_id"], fid)).fetchall()
        for r in rows:
            if isinstance(r["v"], str):
                new = phone_or_raw(r["v"])
                if new != r["v"]:
                    conn.execute("UPDATE records SET data = jsonb_set(data, %s, %s) WHERE id = %s", ([fid], Jsonb(new), r["id"]))
