-- Devices using Probyte's calendar server (Settings → Phone sync → Connected devices): one row per user and app
-- (its User-Agent), with when it last synced and when it last sent a change.
CREATE TABLE IF NOT EXISTS dav_devices (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent        text NOT NULL,              -- the app's User-Agent
    label        text NOT NULL,              -- "iPhone / iPad · Calendar (iOS 18.0)"
    first_seen   timestamptz NOT NULL DEFAULT now(),
    last_seen    timestamptz NOT NULL DEFAULT now(),
    last_change  timestamptz,                -- it last created, changed or deleted something
    last_address text,
    requests     bigint NOT NULL DEFAULT 0,
    UNIQUE (user_id, agent)
);
