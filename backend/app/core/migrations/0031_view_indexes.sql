-- Views are looked up by their table (or combined database) on every table load.
CREATE INDEX IF NOT EXISTS views_table_idx ON views (table_id) WHERE table_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS views_combo_idx ON views (combo_id) WHERE combo_id IS NOT NULL;
