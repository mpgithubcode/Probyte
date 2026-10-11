"""Record writes that work for every kind of table: local, CalDAV, and linked (remote) tables.

Local writes go through store; writes to a linked table are forwarded to its source link,
which enforces that link's permissions.
"""
from . import remote, store


def _table(conn, table_id, user_id):
    return store.get_table(conn, table_id, user_id)


def create(conn, table_id, data: dict, user_id, anonymous=False, link_table_id=None):
    """Returns (record id, table). link_table_id: for a linked combined database, which of its tables to add to."""
    t = _table(conn, table_id, user_id)
    if t["kind"] == "remote":
        return remote.create(conn, t, data, link_table_id)["id"], t
    if t["kind"] == "files":
        from . import files
        r, t = files.create(conn, t, data, user_id)
    else:
        r, t = store.create_record(conn, table_id, data, user_id)
    if anonymous:
        conn.execute("UPDATE records SET created_by = NULL, updated_by = NULL WHERE id = %s", (r["id"],))
    return r["id"], t


def update(conn, table_id, record_id, data: dict, user_id, anonymous=False):
    t = _table(conn, table_id, user_id)
    if t["kind"] == "remote":
        remote.update(conn, t, record_id, data)
        return t
    if not conn.execute("SELECT 1 FROM records WHERE id = %s AND table_id = %s", (record_id, table_id)).fetchone():
        store.not_found("Record not found")
    if t["kind"] == "files":
        from . import files
        files.update(conn, t, record_id, data, user_id)
    else:
        store.update_record(conn, record_id, data, user_id)
    if anonymous:
        conn.execute("UPDATE records SET updated_by = NULL WHERE id = %s", (record_id,))
    return t


def delete(conn, table_id, record_id, user_id):
    t = _table(conn, table_id, user_id)
    if t["kind"] == "remote":
        remote.delete(conn, t, record_id)
        return t
    if not conn.execute("SELECT 1 FROM records WHERE id = %s AND table_id = %s", (record_id, table_id)).fetchone():
        store.not_found("Record not found")
    return store.delete_record(conn, record_id, user_id)
