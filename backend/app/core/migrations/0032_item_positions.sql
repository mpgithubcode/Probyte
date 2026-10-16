-- The order of a base's tables and combined databases in the sidebar (one list: a combined database can sit between
-- tables). Not set yet: in the order they were made, tables first.
ALTER TABLE tables ADD COLUMN IF NOT EXISTS position integer;
ALTER TABLE combos ADD COLUMN IF NOT EXISTS position integer;
