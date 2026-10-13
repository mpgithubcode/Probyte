-- Automations: when something happens in a table (a record is created, changed, starts matching some conditions, is
-- deleted; or on a schedule), and the record matches the conditions, do the actions (change it, create a record
-- elsewhere, call a webhook, send an email, move it to the trash). See app/automations/.
CREATE TABLE IF NOT EXISTS automations (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    base_id      uuid NOT NULL REFERENCES bases(id) ON DELETE CASCADE,
    table_id     uuid NOT NULL REFERENCES tables(id) ON DELETE CASCADE,   -- the table it watches
    created_by   uuid REFERENCES users(id) ON DELETE SET NULL,              -- it acts with their rights
    name         text NOT NULL,
    enabled      boolean NOT NULL DEFAULT true,
    trigger      jsonb NOT NULL,                  -- {"type": created|updated|matches|deleted|schedule, "fields": [...], "every": ..., "at": ...}
    conditions   jsonb NOT NULL DEFAULT '{"conjunction": "and", "items": []}',   -- a view filter
    actions      jsonb NOT NULL DEFAULT '[]',
    next_run_at  timestamptz,                     -- schedules
    last_run_at  timestamptz,
    last_ok      boolean,
    last_message text,
    runs         bigint NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS automations_table ON automations (table_id) WHERE enabled;
CREATE INDEX IF NOT EXISTS automations_due ON automations (next_run_at) WHERE enabled AND next_run_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS automation_runs (
    id            bigserial PRIMARY KEY,
    automation_id uuid NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    at            timestamptz NOT NULL DEFAULT now(),
    record_id     uuid,
    ok            boolean NOT NULL,
    message       text,
    ms            integer
);
CREATE INDEX IF NOT EXISTS automation_runs_of ON automation_runs (automation_id, at DESC);
