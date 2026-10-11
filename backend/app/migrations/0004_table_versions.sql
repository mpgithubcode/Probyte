-- A per-table counter that moves whenever the table's records or fields change. Computed values are cached
-- in memory against it (compute.py), so reading an unchanged table skips the database scan and computation.
ALTER TABLE tables ADD COLUMN IF NOT EXISTS data_version bigint NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION bump_version_new() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE tables SET data_version = data_version + 1 WHERE id IN (SELECT DISTINCT table_id FROM new_rows);
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION bump_version_old() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE tables SET data_version = data_version + 1 WHERE id IN (SELECT DISTINCT table_id FROM old_rows);
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION bump_version_both() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE tables SET data_version = data_version + 1
    WHERE id IN (SELECT table_id FROM new_rows UNION SELECT table_id FROM old_rows);
    RETURN NULL;
END $$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['records', 'fields'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS %1$s_version_ins ON %1$s', t);
        EXECUTE format('DROP TRIGGER IF EXISTS %1$s_version_upd ON %1$s', t);
        EXECUTE format('DROP TRIGGER IF EXISTS %1$s_version_del ON %1$s', t);
        EXECUTE format('CREATE TRIGGER %1$s_version_ins AFTER INSERT ON %1$s REFERENCING NEW TABLE AS new_rows '
                       'FOR EACH STATEMENT EXECUTE FUNCTION bump_version_new()', t);
        EXECUTE format('CREATE TRIGGER %1$s_version_upd AFTER UPDATE ON %1$s REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows '
                       'FOR EACH STATEMENT EXECUTE FUNCTION bump_version_both()', t);
        EXECUTE format('CREATE TRIGGER %1$s_version_del AFTER DELETE ON %1$s REFERENCING OLD TABLE AS old_rows '
                       'FOR EACH STATEMENT EXECUTE FUNCTION bump_version_old()', t);
    END LOOP;
END $$;

-- Each record row also gets a version from a sequence when written, so a cached table can fetch just the rows
-- changed since it was read. The table row is locked first: per table, versions are then handed out in commit
-- order, and a reader never sees a version without every lower one of that table.
CREATE SEQUENCE IF NOT EXISTS records_row_version;
ALTER TABLE records ADD COLUMN IF NOT EXISTS row_version bigint NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS records_table_row_version ON records (table_id, row_version);

CREATE OR REPLACE FUNCTION set_row_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM 1 FROM tables WHERE id = NEW.table_id FOR UPDATE;
    NEW.row_version := nextval('records_row_version');
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS records_row_version ON records;
CREATE TRIGGER records_row_version BEFORE INSERT OR UPDATE ON records FOR EACH ROW EXECUTE FUNCTION set_row_version();
