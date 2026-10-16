-- Values a type change couldn't convert ("Opportunity" in a field made a number): kept here rather than lost, and
-- put back when the field changes to a type they fit again (see data/values.py convert_field).
CREATE TABLE IF NOT EXISTS field_value_backups (
    field_id   uuid NOT NULL REFERENCES fields(id) ON DELETE CASCADE,
    record_id  uuid NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    old_type   text NOT NULL,
    raw        jsonb,
    items      jsonb NOT NULL DEFAULT '[]',   -- the value as it was shown (titles, names), for converting it later
    vt         text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (field_id, record_id)
);
