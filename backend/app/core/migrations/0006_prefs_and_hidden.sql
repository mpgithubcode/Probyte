-- Per-user settings (e.g. the first day of the week) and tables / combined databases hidden from the sidebar.
ALTER TABLE users ADD COLUMN IF NOT EXISTS prefs jsonb NOT NULL DEFAULT '{}';
ALTER TABLE tables ADD COLUMN IF NOT EXISTS hidden boolean NOT NULL DEFAULT false;
ALTER TABLE combos ADD COLUMN IF NOT EXISTS hidden boolean NOT NULL DEFAULT false;
