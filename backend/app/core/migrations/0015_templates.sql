-- Saved templates: a table (fields, views, maybe records) or a view, as portable JSON (see templates.py).
CREATE TABLE IF NOT EXISTS templates (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        text NOT NULL CHECK (kind IN ('table', 'view')),
    name        text NOT NULL,
    description text,
    body        jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS templates_user ON templates (user_id, kind);
