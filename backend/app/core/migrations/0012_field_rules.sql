-- Field settings from the field editor: a description, validation rules and a default value for new records.
ALTER TABLE fields ADD COLUMN IF NOT EXISTS description text;
ALTER TABLE fields ADD COLUMN IF NOT EXISTS required boolean NOT NULL DEFAULT false;
ALTER TABLE fields ADD COLUMN IF NOT EXISTS unique_values boolean NOT NULL DEFAULT false;
ALTER TABLE fields ADD COLUMN IF NOT EXISTS default_value jsonb;
