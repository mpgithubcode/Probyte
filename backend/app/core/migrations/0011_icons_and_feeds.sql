-- An emoji as a table's or combined database's icon (empty = the default icon by kind).
ALTER TABLE tables ADD COLUMN IF NOT EXISTS icon text;
ALTER TABLE combos ADD COLUMN IF NOT EXISTS icon text;

-- Calendar subscription links: a secret URL serving a table's (or a view's) records as an iCalendar feed that
-- calendar apps subscribe to, read-only.
CREATE TABLE IF NOT EXISTS calendar_feeds (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token      text NOT NULL UNIQUE,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    table_id   uuid REFERENCES tables(id) ON DELETE CASCADE,
    combo_id   uuid REFERENCES combos(id) ON DELETE CASCADE,
    view_id    uuid REFERENCES views(id) ON DELETE SET NULL,   -- that view's filters and date fields
    name       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_fetched_at timestamptz
);
