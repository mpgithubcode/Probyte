"""One-off data fixes that used to run at every startup."""
from psycopg.types.json import Jsonb

from app.formats.ical import SYSTEM_FIELDS


def run(conn):
    # Every local table has a Record ID column.
    for t in conn.execute("SELECT id FROM tables t WHERE kind <> 'remote' AND NOT EXISTS "
                          "(SELECT 1 FROM fields f WHERE f.table_id = t.id AND f.type = 'record_id')").fetchall():
        pos = conn.execute("SELECT COALESCE(MAX(position) + 1, 0) AS n FROM fields WHERE table_id = %s", (t["id"],)).fetchone()["n"]
        conn.execute("INSERT INTO fields (table_id, name, type, options, system_key, position) VALUES (%s, 'Record ID', 'record_id', '{}', 'record_id', %s)",
                     (t["id"], pos))
    # Subtasks were removed: drop Parent Task / Subtasks fields and their values.
    for f in conn.execute("SELECT id, table_id FROM fields WHERE system_key IN ('parent', 'subtasks')").fetchall():
        conn.execute("UPDATE records SET data = data - %s WHERE table_id = %s", (str(f["id"]), f["table_id"]))
        conn.execute("DELETE FROM fields WHERE id = %s", (f["id"],))
    # Newer status options for events (Attended, Missed).
    for kind in ("events", "mixed"):
        want = next(o for k, _, _, o, _ in SYSTEM_FIELDS[kind] if k == "status")["choices"]
        for f in conn.execute("SELECT f.id, f.options FROM fields f JOIN tables t ON t.id = f.table_id "
                              "WHERE t.kind = %s AND f.system_key = 'status'", (kind,)).fetchall():
            have = {c["name"] for c in f["options"].get("choices", [])}
            extra = [c for c in want if c["name"] not in have]
            if extra:
                conn.execute("UPDATE fields SET options = jsonb_set(options, '{choices}', COALESCE(options->'choices', '[]'::jsonb) || %s) WHERE id = %s",
                             (Jsonb(extra), f["id"]))
