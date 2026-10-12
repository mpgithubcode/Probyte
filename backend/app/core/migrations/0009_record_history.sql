-- Who changed what in each record (kept 180 days; see audit.py).
CREATE TABLE IF NOT EXISTS record_history (
    id         bigserial PRIMARY KEY,
    record_id  uuid NOT NULL,
    table_id   uuid NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    at         timestamptz NOT NULL DEFAULT now(),
    actor      text NOT NULL,             -- "mike", "API key “Zapier”", "Claude (connected app)", "Phone sync", "Public link “Team”"
    user_id    uuid,
    action     text NOT NULL,             -- created | updated | deleted | restored
    changes    jsonb NOT NULL DEFAULT '{}'  -- field id -> [old, new]
);
CREATE INDEX IF NOT EXISTS record_history_record ON record_history (record_id, at DESC);
CREATE INDEX IF NOT EXISTS record_history_at ON record_history (at);
