-- Table settings: a description of what a table (or combined database) is for.
ALTER TABLE tables ADD COLUMN IF NOT EXISTS description text;
ALTER TABLE combos ADD COLUMN IF NOT EXISTS description text;
