"""Record history: who changed what. The "who" is set once per request (signed-in user, API key, AI assistant,
public link) or by the sync, and every record write notes it with the fields it changed."""
from psycopg.types.json import Jsonb

from app.core.context import actor, set_actor  # noqa: F401  (set_actor: callers set the "who" through here too)

KEEP_DAYS = 180
SKIP_TYPES = {"formula", "lookup", "rollup", "conditional_lookup", "conditional_rollup", "autonumber", "record_id",
              "created_time", "last_modified_time", "created_by", "last_modified_by", "button"}


def note(conn, table_id, record_id, action: str, old: dict | None = None, new: dict | None = None, fields=None):
    """Add a history entry. For updates, only fields whose value changed (and that aren't calculated)."""
    changes = {}
    if action == "updated":
        skip = {str(f["id"]) for f in (fields or []) if f["type"] in SKIP_TYPES}
        empty = (None, "", [], {})
        for k in set(old or {}) | set(new or {}):
            a, b = (old or {}).get(k), (new or {}).get(k)
            if a != b and not (a in empty and b in empty) and k not in skip:
                changes[k] = [a, b]
        if not changes:
            return
    label, user_id = actor()
    conn.execute("INSERT INTO record_history (record_id, table_id, actor, user_id, action, changes) VALUES (%s,%s,%s,%s,%s,%s)",
                 (record_id, table_id, label, user_id, action, Jsonb(changes)))


def prune(conn):
    conn.execute("DELETE FROM record_history WHERE at < now() - make_interval(days => %s)", (KEEP_DAYS,))
