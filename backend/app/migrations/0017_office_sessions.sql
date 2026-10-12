-- Editing files of files tables in an ONLYOFFICE Document Server (office.py). One row per editor opened: its secret
-- token (the Document Server fetches the file and reports saves with it) and what happened to it.
CREATE TABLE IF NOT EXISTS office_sessions (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token        text NOT NULL UNIQUE,
    record_id    uuid NOT NULL,
    table_id     uuid NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    user_id      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    doc_key      text NOT NULL,              -- the Document Server's key for this version (co-editors share it)
    mode         text NOT NULL DEFAULT 'edit',
    saves        integer NOT NULL DEFAULT 0, -- versions written to the storage from this key
    last_saved_at timestamptz,
    discarded    boolean NOT NULL DEFAULT false,
    closed_at    timestamptz,
    last_error   text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS office_sessions_key ON office_sessions (doc_key);
CREATE INDEX IF NOT EXISTS office_sessions_record ON office_sessions (record_id);
