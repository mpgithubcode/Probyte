-- Trash: deleted records and tables are kept for 30 days and can be restored.
-- Records: a copy of the deleted row (the row itself is gone, or goes once its deletion has synced).
CREATE TABLE IF NOT EXISTS record_trash (
    id          uuid PRIMARY KEY,              -- the record's own id (restored under the same id)
    table_id    uuid NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    data        jsonb NOT NULL,
    seq         integer,
    caldav_uid  text,
    caldav_raw  text,
    created_at  timestamptz NOT NULL,
    created_by  uuid,
    deleted_at  timestamptz NOT NULL DEFAULT now(),
    deleted_by  uuid
);
CREATE INDEX IF NOT EXISTS record_trash_table ON record_trash (table_id, deleted_at DESC);
-- Tables: marked, hidden everywhere, and really deleted (with their calendar, if asked) after 30 days.
ALTER TABLE tables ADD COLUMN IF NOT EXISTS trashed_at timestamptz;
ALTER TABLE tables ADD COLUMN IF NOT EXISTS trash_delete_on_server boolean NOT NULL DEFAULT false;
