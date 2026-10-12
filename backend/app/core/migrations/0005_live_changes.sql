-- Live updates. Hard-deleted records leave a tombstone (with a row version from the same sequence as record
-- writes) so a client can ask "what changed since version N" and learn about deletions too; the worker prunes
-- old tombstones. Every record or field change also sends NOTIFY probyte_changes with the affected table ids,
-- which the web process relays to browsers (live.py).
CREATE TABLE IF NOT EXISTS record_tombstones (
    table_id    uuid NOT NULL,
    record_id   uuid NOT NULL,
    row_version bigint NOT NULL,
    deleted_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS record_tombstones_since ON record_tombstones (table_id, row_version);

CREATE OR REPLACE FUNCTION notify_tables(ids text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    IF ids IS NOT NULL THEN
        PERFORM pg_notify('probyte_changes', left(ids, 7900));
    END IF;
END $$;

CREATE OR REPLACE FUNCTION bump_version_new() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE tables SET data_version = data_version + 1 WHERE id IN (SELECT DISTINCT table_id FROM new_rows);
    PERFORM notify_tables((SELECT string_agg(DISTINCT table_id::text, ',') FROM new_rows));
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION bump_version_old() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE tables SET data_version = data_version + 1 WHERE id IN (SELECT DISTINCT table_id FROM old_rows);
    PERFORM notify_tables((SELECT string_agg(DISTINCT table_id::text, ',') FROM old_rows));
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION bump_version_both() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE tables SET data_version = data_version + 1
    WHERE id IN (SELECT table_id FROM new_rows UNION SELECT table_id FROM old_rows);
    PERFORM notify_tables((SELECT string_agg(DISTINCT table_id::text, ',') FROM (SELECT table_id FROM new_rows UNION SELECT table_id FROM old_rows) t));
    RETURN NULL;
END $$;

-- Record deletes: bump (which locks the table rows, keeping versions in commit order), then leave tombstones.
CREATE OR REPLACE FUNCTION records_deleted() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE tables SET data_version = data_version + 1 WHERE id IN (SELECT DISTINCT table_id FROM old_rows);
    INSERT INTO record_tombstones (table_id, record_id, row_version)
        SELECT table_id, id, nextval('records_row_version') FROM old_rows;
    PERFORM notify_tables((SELECT string_agg(DISTINCT table_id::text, ',') FROM old_rows));
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS records_version_del ON records;
CREATE TRIGGER records_version_del AFTER DELETE ON records REFERENCING OLD TABLE AS old_rows
    FOR EACH STATEMENT EXECUTE FUNCTION records_deleted();
