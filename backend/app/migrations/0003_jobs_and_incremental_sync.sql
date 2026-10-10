-- Durable background jobs (sync, webhooks, cleanup), run by the worker process.
CREATE TABLE IF NOT EXISTS jobs (
    id            bigserial PRIMARY KEY,
    kind          text NOT NULL,
    payload       jsonb NOT NULL DEFAULT '{}',
    dedupe_key    text,                       -- unique while waiting: queuing again debounces
    run_at        timestamptz NOT NULL DEFAULT now(),
    attempts      integer NOT NULL DEFAULT 0,
    max_attempts  integer NOT NULL DEFAULT 5,
    locked_until  timestamptz,                -- set while a worker runs it
    failed        boolean NOT NULL DEFAULT false,
    last_error    text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_waiting_key ON jobs (dedupe_key) WHERE dedupe_key IS NOT NULL AND locked_until IS NULL AND NOT failed;
CREATE INDEX IF NOT EXISTS jobs_due ON jobs (run_at) WHERE NOT failed;

-- Incremental sync: each item's ETag, and the collection's change tag (CTag / sync-token) per table.
ALTER TABLE records ADD COLUMN IF NOT EXISTS caldav_etag text;
ALTER TABLE tables ADD COLUMN IF NOT EXISTS sync_state jsonb NOT NULL DEFAULT '{}';
