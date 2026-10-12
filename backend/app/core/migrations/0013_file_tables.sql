-- Files tables: a table whose records are the files in an S3 bucket or a WebDAV folder.
-- A storage is a saved connection (S3: endpoint, region, bucket, keys; WebDAV: URL, username, password).
CREATE TABLE IF NOT EXISTS storages (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        text NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('s3', 'webdav')),
    endpoint    text NOT NULL DEFAULT '',     -- S3: empty = AWS; WebDAV: the folder URL
    region      text NOT NULL DEFAULT '',
    bucket      text NOT NULL DEFAULT '',
    username    text NOT NULL DEFAULT '',     -- S3 access key id / WebDAV username
    secret      text NOT NULL DEFAULT '',     -- S3 secret key / WebDAV password (encrypted, see secretbox.py)
    path_style  boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS storages_user ON storages (user_id);

ALTER TABLE tables ADD COLUMN IF NOT EXISTS storage_id uuid REFERENCES storages(id) ON DELETE SET NULL;
ALTER TABLE tables ADD COLUMN IF NOT EXISTS storage_prefix text;     -- the folder inside the bucket / WebDAV URL ('' = all of it)
ALTER TABLE tables DROP CONSTRAINT IF EXISTS tables_kind_check;
ALTER TABLE tables ADD CONSTRAINT tables_kind_check CHECK (kind IN ('generic', 'tasks', 'events', 'mixed', 'contacts', 'remote', 'files'));
-- a files record's object key (records.caldav_href) is looked up on every sync and write
CREATE INDEX IF NOT EXISTS records_href ON records (table_id, caldav_href) WHERE caldav_href IS NOT NULL;
