"""A small durable job queue in Postgres, run by the worker process (app.worker).

Jobs are rows in `jobs`. enqueue() can be called inside the transaction that caused the job, so a job only
exists if that change was committed. A `key` makes a job unique while it's waiting: queuing it again pushes
its start back (debouncing), but never more than DEBOUNCE_CAP seconds past the first request. Workers claim
due jobs with FOR UPDATE SKIP LOCKED, so several worker threads or processes can share the queue.
Failed jobs are retried with exponential backoff, then kept as failed for a while for inspection.
"""
import logging
import threading
import traceback
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.types.json import Jsonb

from .db import DATABASE_URL, connection

log = logging.getLogger("probyte.jobs")

CHANNEL = "probyte_jobs"
DEBOUNCE_CAP = 30          # seconds a debounced job may be pushed back in total
LOCK_SECONDS = 600         # a claimed job is retried if its worker hasn't finished by then (crashed)
KEEP_FAILED_DAYS = 7

_handlers: dict = {}


class Retry(Exception):
    """Raise from a handler to run the job again later without counting it as a failure."""

    def __init__(self, seconds: float = 5):
        super().__init__(f"retry in {seconds}s")
        self.seconds = seconds


def handler(kind: str):
    def wrap(fn):
        _handlers[kind] = fn
        return fn
    return wrap


def enqueue(conn, kind: str, payload: dict | None = None, *, key: str | None = None, delay: float = 0, max_attempts: int = 5):
    """Queue a job (in the caller's transaction when `conn` is given)."""
    def run(c):
        c.execute(
            "INSERT INTO jobs (kind, payload, dedupe_key, run_at, max_attempts) VALUES (%s, %s, %s, now() + make_interval(secs => %s), %s) "
            "ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL AND locked_until IS NULL AND NOT failed DO UPDATE SET "
            "run_at = LEAST(EXCLUDED.run_at, jobs.created_at + make_interval(secs => %s)), payload = jobs.payload || EXCLUDED.payload",
            (kind, Jsonb(payload or {}), key, delay, max_attempts, DEBOUNCE_CAP))
        c.execute(f"NOTIFY {CHANNEL}")
    if conn is not None:
        run(conn)
    else:
        with connection() as c:
            run(c)


SLOW_KINDS = ["sync_table"]  # jobs that talk to other servers for a while; one worker thread never takes them


def _claim(fast_only=False):
    """The next due job. Quick jobs (webhooks) go first, so a slow calendar server doesn't hold them up."""
    with connection() as conn:
        return conn.execute(
            "UPDATE jobs SET locked_until = now() + make_interval(secs => %s), attempts = attempts + 1 WHERE id = ("
            "  SELECT id FROM jobs WHERE NOT failed AND run_at <= now() AND (locked_until IS NULL OR locked_until < now())"
            "  AND NOT (%s AND kind = ANY(%s))"
            "  ORDER BY kind = ANY(%s), run_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *",
            (LOCK_SECONDS, fast_only, SLOW_KINDS, SLOW_KINDS)).fetchone()


def _finish(job, error: str | None = None, retry_in: float | None = None):
    with connection() as conn:
        if error is None and retry_in is None:
            conn.execute("DELETE FROM jobs WHERE id = %s", (job["id"],))
        elif retry_in is not None:  # Retry: not a failure
            conn.execute("UPDATE jobs SET locked_until = NULL, attempts = attempts - 1, run_at = now() + make_interval(secs => %s) WHERE id = %s",
                         (retry_in, job["id"]))
        elif job["attempts"] >= job["max_attempts"]:
            conn.execute("UPDATE jobs SET failed = true, locked_until = NULL, last_error = %s WHERE id = %s", (error[:2000], job["id"]))
            log.warning("job %s %s failed for good: %s", job["id"], job["kind"], error.splitlines()[-1] if error else "")
        else:
            backoff = min(3600, 5 * 3 ** (job["attempts"] - 1))  # 5 s, 15 s, 45 s, 2 min, 7 min…
            conn.execute("UPDATE jobs SET locked_until = NULL, run_at = now() + make_interval(secs => %s), last_error = %s WHERE id = %s",
                         (backoff, error[:2000], job["id"]))


def run_one(fast_only=False) -> bool:
    """Run one due job. Returns False when there was nothing to do."""
    job = _claim(fast_only)
    if not job:
        return False
    fn = _handlers.get(job["kind"])
    try:
        if fn is None:
            raise RuntimeError(f"no handler for job kind {job['kind']}")
        fn(job["payload"])
        _finish(job)
    except Retry as r:
        _finish(job, retry_in=r.seconds)
    except Exception:
        _finish(job, error=traceback.format_exc())
    return True


_listeners = threading.local()


def _wait_for_notify(stop: threading.Event, timeout: float):
    """Sleep until a job is queued (NOTIFY) or `timeout` passes. Each worker thread listens on a connection of its
    own (not one borrowed from the pool, which jobs need)."""
    try:
        conn = getattr(_listeners, "conn", None)
        if conn is None or conn.closed:
            conn = _listeners.conn = psycopg.connect(DATABASE_URL, autocommit=True)
            conn.execute(f"LISTEN {CHANNEL}")
        for _ in conn.notifies(timeout=timeout, stop_after=1):
            break
    except Exception:
        _listeners.conn = None
        stop.wait(timeout)


def worker_thread(stop: threading.Event, fast_only=False):
    while not stop.is_set():
        try:
            if not run_one(fast_only):
                _wait_for_notify(stop, 2.0)
        except Exception:
            log.exception("worker error")
            stop.wait(2)


def every(stop: threading.Event, seconds: float, fn, first_delay: float = 10):
    """Run fn() periodically until stop is set (seconds <= 0 disables)."""
    if seconds <= 0:
        return
    delay = first_delay
    while not stop.wait(delay):
        try:
            fn()
        except Exception:
            log.exception("periodic task failed")
        delay = seconds


def prune():
    with connection() as conn:
        conn.execute("DELETE FROM jobs WHERE failed AND created_at < %s", (datetime.now(timezone.utc) - timedelta(days=KEEP_FAILED_DAYS),))


def stats() -> dict:
    with connection() as conn:
        r = conn.execute("SELECT count(*) FILTER (WHERE NOT failed) AS waiting, count(*) FILTER (WHERE failed) AS failed FROM jobs").fetchone()
    return dict(r)


def start(stop: threading.Event, threads: int = 4) -> list[threading.Thread]:
    out = []
    for i in range(threads):
        # thread 0 only runs quick jobs: webhooks never wait behind slow syncs
        t = threading.Thread(target=worker_thread, args=(stop, i == 0 and threads > 1), name=f"job-worker-{i}", daemon=True)
        t.start()
        out.append(t)
    return out


__all__ = ["enqueue", "handler", "Retry", "start", "every", "prune", "stats", "run_one"]
