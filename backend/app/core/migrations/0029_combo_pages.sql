-- A combined database's page: its records open as pages, like a table's (see 0028_pages.sql and data/pages.py).
ALTER TABLE combos ADD COLUMN IF NOT EXISTS page jsonb;
