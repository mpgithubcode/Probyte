-- Probyte schema. Applied idempotently at startup.

CREATE TABLE IF NOT EXISTS users (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username       text NOT NULL UNIQUE,
    password_hash  text NOT NULL,
    is_admin       boolean NOT NULL DEFAULT false,
    radicale_hash  text,            -- bcrypt hash written to the bundled Radicale's htpasswd file
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  text PRIMARY KEY,   -- sha256 of the cookie value
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at  timestamptz NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);

CREATE TABLE IF NOT EXISTS caldav_accounts (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    url         text NOT NULL,
    username    text,
    password    text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- A user-facing "database". kind = generic | tasks | events | mixed
CREATE TABLE IF NOT EXISTS tables (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name             text NOT NULL,
    kind             text NOT NULL DEFAULT 'generic',
    account_id       uuid REFERENCES caldav_accounts(id) ON DELETE SET NULL,
    calendar_url     text,
    last_synced_at   timestamptz,
    last_sync_error  text,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fields (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    table_id    uuid NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    name        text NOT NULL,
    type        text NOT NULL,
    options     jsonb NOT NULL DEFAULT '{}',
    system_key  text,              -- set for CalDAV-managed fields (summary, due, dtstart...)
    is_primary  boolean NOT NULL DEFAULT false,
    position    integer NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS fields_table_idx ON fields(table_id);

CREATE TABLE IF NOT EXISTS records (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    table_id     uuid NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    data         jsonb NOT NULL DEFAULT '{}',   -- { field_id: value }
    caldav_uid   text,
    caldav_href  text,
    caldav_raw   text,                          -- last iCalendar body seen on / sent to the server
    sync_status  text NOT NULL DEFAULT 'synced' CHECK (sync_status IN ('synced', 'dirty')),
    deleted      boolean NOT NULL DEFAULT false, -- tombstone until the remote copy is deleted
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS records_table_idx ON records(table_id);
CREATE UNIQUE INDEX IF NOT EXISTS records_uid_idx ON records(table_id, caldav_uid) WHERE caldav_uid IS NOT NULL;

-- A combined database: several source tables shown together through a field mapping.
-- fields: [{ "id": "...", "name": "...", "mapping": { "<table_id>": "<field_id>" } }]
CREATE TABLE IF NOT EXISTS combos (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    sources     jsonb NOT NULL DEFAULT '[]',   -- [{ "table_id": "...", "color": "#..." }]
    fields      jsonb NOT NULL DEFAULT '[]',
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS views (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    table_id    uuid REFERENCES tables(id) ON DELETE CASCADE,
    combo_id    uuid REFERENCES combos(id) ON DELETE CASCADE,
    name        text NOT NULL,
    type        text NOT NULL,
    config      jsonb NOT NULL DEFAULT '{}',
    position    integer NOT NULL DEFAULT 0,
    CHECK ((table_id IS NULL) <> (combo_id IS NULL))
);

-- ---------------------------------------------------------------- migrations

-- Per-user ownership (rows created before multi-user support are claimed by the first user).
ALTER TABLE caldav_accounts ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE caldav_accounts ADD COLUMN IF NOT EXISTS is_builtin boolean NOT NULL DEFAULT false;
ALTER TABLE tables ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE combos ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES users(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS caldav_accounts_user_idx ON caldav_accounts(user_id);
CREATE INDEX IF NOT EXISTS tables_user_idx ON tables(user_id);
CREATE INDEX IF NOT EXISTS combos_user_idx ON combos(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS caldav_accounts_builtin_idx ON caldav_accounts(user_id) WHERE is_builtin;

-- 'mixed' = one calendar holding both VTODO and VEVENT.
ALTER TABLE tables DROP CONSTRAINT IF EXISTS tables_kind_check;
ALTER TABLE tables ADD CONSTRAINT tables_kind_check CHECK (kind IN ('generic', 'tasks', 'events', 'mixed', 'contacts', 'remote'));

-- Notion-style layouts. grid = Table, kanban = Board.
ALTER TABLE views DROP CONSTRAINT IF EXISTS views_type_check;
ALTER TABLE views ADD CONSTRAINT views_type_check
    CHECK (type IN ('grid', 'kanban', 'timeline', 'calendar', 'list', 'gallery', 'chart'));

-- Auto number, created/modified by.
ALTER TABLE records ADD COLUMN IF NOT EXISTS seq bigint;
ALTER TABLE records ADD COLUMN IF NOT EXISTS created_by uuid REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE records ADD COLUMN IF NOT EXISTS updated_by uuid REFERENCES users(id) ON DELETE SET NULL;
UPDATE records r SET seq = s.n
  FROM (SELECT id, row_number() OVER (PARTITION BY table_id ORDER BY created_at, id) AS n FROM records) s
  WHERE r.id = s.id AND r.seq IS NULL;

-- Uploaded files for attachment fields (bytes live on disk, see attachments.py).
CREATE TABLE IF NOT EXISTS attachments (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        text NOT NULL,
    mime        text NOT NULL,
    size        bigint NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS attachments_user_idx ON attachments(user_id);

-- ---------------------------------------------------------------- bases (Bases -> tables)
CREATE TABLE IF NOT EXISTS bases (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        text NOT NULL,
    color       text NOT NULL DEFAULT '#6366f1',
    position    integer NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS bases_user_idx ON bases(user_id);
ALTER TABLE tables ADD COLUMN IF NOT EXISTS base_id uuid REFERENCES bases(id) ON DELETE CASCADE;
ALTER TABLE combos ADD COLUMN IF NOT EXISTS base_id uuid REFERENCES bases(id) ON DELETE CASCADE;
-- Existing data: one "My Base" per user that has tables or combined databases.
INSERT INTO bases (user_id, name)
  SELECT DISTINCT u, 'My Base' FROM (SELECT user_id AS u FROM tables WHERE base_id IS NULL AND user_id IS NOT NULL
                                     UNION SELECT user_id FROM combos WHERE base_id IS NULL AND user_id IS NOT NULL) x
  WHERE NOT EXISTS (SELECT 1 FROM bases b WHERE b.user_id = x.u);
UPDATE tables t SET base_id = (SELECT id FROM bases b WHERE b.user_id = t.user_id ORDER BY created_at LIMIT 1)
  WHERE base_id IS NULL AND user_id IS NOT NULL;
UPDATE combos c SET base_id = (SELECT id FROM bases b WHERE b.user_id = c.user_id ORDER BY created_at LIMIT 1)
  WHERE base_id IS NULL AND user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS tables_base_idx ON tables(base_id);

-- ---------------------------------------------------------------- public links
-- items: [{"kind": "table"|"combo", "id": "...", "views": ["view id", ...]}]
-- permissions: {"add": bool, "edit": bool, "delete": bool}
CREATE TABLE IF NOT EXISTS shares (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    base_id        uuid NOT NULL REFERENCES bases(id) ON DELETE CASCADE,
    token          text NOT NULL UNIQUE,
    name           text NOT NULL,
    items          jsonb NOT NULL DEFAULT '[]',
    permissions    jsonb NOT NULL DEFAULT '{}',
    password_hash  text,
    expires_at     timestamptz,
    enabled        boolean NOT NULL DEFAULT true,
    color          text NOT NULL DEFAULT '#6366f1',
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS shares_user_idx ON shares(user_id);

-- ---------------------------------------------------------------- API keys
CREATE TABLE IF NOT EXISTS api_keys (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          text NOT NULL,
    prefix        text NOT NULL,             -- first characters, to recognise a key in the list
    key_hash      text NOT NULL UNIQUE,      -- sha256 of the full key; the key itself is never stored
    scope         text NOT NULL DEFAULT 'read' CHECK (scope IN ('read', 'write')),
    base_ids      jsonb,                     -- null = all bases
    expires_at    timestamptz,
    last_used_at  timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS api_keys_user_idx ON api_keys(user_id);

CREATE TABLE IF NOT EXISTS app_settings (
    key    text PRIMARY KEY,
    value  text NOT NULL
);

-- ---------------------------------------------------------------- linked tables (from a public link, any instance)
-- remote: {"url", "origin", "token", "item_id", "view_id", "cookie", "local", "name", "permissions", "fields_hash", "error"}
ALTER TABLE tables ADD COLUMN IF NOT EXISTS remote jsonb;
ALTER TABLE tables DROP CONSTRAINT IF EXISTS tables_kind_check;
ALTER TABLE tables ADD CONSTRAINT tables_kind_check CHECK (kind IN ('generic', 'tasks', 'events', 'mixed', 'contacts', 'remote'));

-- ---------------------------------------------------------------- contacts (CardDAV address books)
-- tables.calendar_url holds the address book URL for kind 'contacts'.
-- carddav_url: contacts address when the provider uses a different host than for calendars (iCloud).
ALTER TABLE caldav_accounts ADD COLUMN IF NOT EXISTS carddav_url text;

-- ---------------------------------------------------------------- webhooks (record events POSTed to a URL)
CREATE TABLE IF NOT EXISTS webhooks (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name              text NOT NULL,
    url               text NOT NULL,
    secret            text NOT NULL,                 -- signs each delivery (X-Probyte-Signature)
    events            jsonb NOT NULL DEFAULT '["record.created", "record.updated", "record.deleted"]',
    table_ids         jsonb,                         -- null = every table
    enabled           boolean NOT NULL DEFAULT true,
    last_status       integer,
    last_error        text,
    last_delivery_at  timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS webhooks_user_idx ON webhooks(user_id);

-- ---------------------------------------------------------------- OAuth 2.1 for MCP clients (Claude, ChatGPT, …)
-- Clients register themselves (RFC 7591); an approved authorization becomes an API key (source = 'oauth').
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id      text PRIMARY KEY,
    client_name    text,
    redirect_uris  jsonb NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS oauth_codes (
    code_hash       text PRIMARY KEY,
    client_id       text NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
    user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    redirect_uri    text NOT NULL,
    code_challenge  text NOT NULL,
    scope           text NOT NULL,
    base_ids        jsonb,
    expires_at      timestamptz NOT NULL,
    used            boolean NOT NULL DEFAULT false
);
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'manual';
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS client_id text;
