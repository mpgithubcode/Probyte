-- Sharing a base with a link: whoever opens it while signed in on this server joins the base with the link's role.
-- follow_views: whether joiners' views stay the base's (each can change it later in the base's sharing settings).
CREATE TABLE IF NOT EXISTS base_invites (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    base_id      uuid NOT NULL REFERENCES bases(id) ON DELETE CASCADE,
    token        text NOT NULL UNIQUE,
    role         text NOT NULL CHECK (role IN ('viewer', 'editor', 'admin')),
    follow_views boolean NOT NULL DEFAULT true,
    enabled      boolean NOT NULL DEFAULT true,
    expires_at   timestamptz,
    uses         integer NOT NULL DEFAULT 0,
    created_by   uuid REFERENCES users(id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS base_invites_base ON base_invites (base_id);
