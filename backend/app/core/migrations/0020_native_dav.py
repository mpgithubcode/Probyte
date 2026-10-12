"""Calendars and contacts are served by Probyte itself (app/dav/server.py) instead of the bundled Radicale.

Tables on the built-in account become collections of Probyte's own server: they keep the collection name they had
in Radicale (tables.dav_slug) and each record keeps its item's name (records.caldav_href, now just the name), so a
phone set up before sees the same calendars at the same addresses. Device passwords stay valid (users.dav_hash is
the hash Radicale checked). What a phone changed in Radicale since the last sync is brought in by the worker
(dav/radicale_import.py) from Radicale's files.

Deleted records leave their item name in the tombstone, for sync-collection reports ("these were deleted").
"""
from collections import defaultdict
from urllib.parse import unquote, urlparse


def _last(url: str) -> str:
    return unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])


def run(conn):
    if conn.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'radicale_hash'").fetchone():
        conn.execute("ALTER TABLE users RENAME COLUMN radicale_hash TO dav_hash")
    conn.execute("ALTER TABLE tables ADD COLUMN IF NOT EXISTS dav_slug text")
    conn.execute("ALTER TABLE tables ADD COLUMN IF NOT EXISTS dav_props jsonb NOT NULL DEFAULT '{}'")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS tables_dav_slug ON tables (user_id, dav_slug) WHERE dav_slug IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS records_href ON records (table_id, caldav_href) WHERE caldav_href IS NOT NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS records_dirty ON records (table_id) WHERE sync_status = 'dirty'")

    conn.execute("ALTER TABLE record_tombstones ADD COLUMN IF NOT EXISTS href text")
    conn.execute("""
CREATE OR REPLACE FUNCTION records_deleted() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE tables SET data_version = data_version + 1 WHERE id IN (SELECT DISTINCT table_id FROM old_rows);
    INSERT INTO record_tombstones (table_id, record_id, row_version, href)
        SELECT table_id, id, nextval('records_row_version'), caldav_href FROM old_rows;
    PERFORM notify_tables((SELECT string_agg(DISTINCT table_id::text, ',') FROM old_rows));
    RETURN NULL;
END $$""")

    # Tables on the built-in account: now served here
    taken = defaultdict(set)
    for r in conn.execute("SELECT user_id, dav_slug FROM tables WHERE dav_slug IS NOT NULL").fetchall():
        taken[r["user_id"]].add(r["dav_slug"])
    rows = conn.execute(
        "SELECT t.id, t.user_id, t.calendar_url FROM tables t JOIN caldav_accounts a ON a.id = t.account_id "
        "WHERE a.is_builtin AND t.dav_slug IS NULL ORDER BY t.created_at").fetchall()
    for t in rows:
        slug = _last(t["calendar_url"] or "") or str(t["id"])
        base, n = slug, 2
        while slug in taken[t["user_id"]]:  # two tables on one calendar: the second becomes a calendar of its own
            slug, n = f"{base}-{n}", n + 1
        taken[t["user_id"]].add(slug)
        conn.execute("UPDATE tables SET dav_slug = %s, account_id = NULL, calendar_url = NULL, sync_state = '{}', last_sync_error = NULL "
                     "WHERE id = %s", (slug, t["id"]))
        # deletions that were waiting to be pushed to Radicale: done
        conn.execute("DELETE FROM records WHERE table_id = %s AND deleted", (t["id"],))
        for rec in conn.execute("SELECT id, caldav_href FROM records WHERE table_id = %s AND caldav_href IS NOT NULL", (t["id"],)).fetchall():
            conn.execute("UPDATE records SET caldav_href = %s WHERE id = %s", (_last(rec["caldav_href"]), rec["id"]))
        conn.execute("UPDATE records SET caldav_etag = '\"' || md5(caldav_raw) || '\"' WHERE table_id = %s AND caldav_raw IS NOT NULL", (t["id"],))
        conn.execute("UPDATE records SET sync_status = 'dirty' WHERE table_id = %s AND (caldav_raw IS NULL OR caldav_href IS NULL)", (t["id"],))
    conn.execute("UPDATE caldav_accounts SET url = '/dav/', name = CASE WHEN name = 'Probyte CalDAV' THEN 'Probyte' ELSE name END WHERE is_builtin")
    if rows or conn.execute("SELECT 1 FROM caldav_accounts WHERE is_builtin LIMIT 1").fetchone():
        # the worker brings in what was changed in Radicale since the last sync (dav/radicale_import.py)
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('radicale_import', 'pending') ON CONFLICT (key) DO NOTHING")
