-- Sharing a base with other users of this server, each with a role:
--   viewer  sees everything in the base           editor  also adds, changes and deletes records, and changes views
--   admin   also changes fields, tables and who the base is shared with (the owner alone can delete the base)
-- A member sees the base as its owner does. follow_views: their views stay those of the base (true), or are their own
-- copies, which later changes to the base's views don't touch (false).
CREATE TABLE IF NOT EXISTS base_members (
    base_id      uuid NOT NULL REFERENCES bases(id) ON DELETE CASCADE,
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role         text NOT NULL CHECK (role IN ('viewer', 'editor', 'admin')),
    follow_views boolean NOT NULL DEFAULT true,
    position     integer NOT NULL DEFAULT 1000,       -- where it sits in the member's sidebar
    added_by     uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (base_id, user_id)
);
CREATE INDEX IF NOT EXISTS base_members_user ON base_members (user_id);

-- A member's own view of a shared table (null: the base's view, seen by everyone)
ALTER TABLE views ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE views ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
-- A member's own settings for one of the base's views (filters, sort, hidden fields…): always used when they keep
-- their own copies; while they follow the base's views, only until the view is changed for everyone.
CREATE TABLE IF NOT EXISTS view_overrides (
    view_id    uuid NOT NULL REFERENCES views(id) ON DELETE CASCADE,
    user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    config     jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (view_id, user_id)
);

-- A member's own sidebar: tables and combined databases they hid (or showed) for themselves ({id: true|false})
ALTER TABLE base_members ADD COLUMN IF NOT EXISTS hidden jsonb NOT NULL DEFAULT '{}';
