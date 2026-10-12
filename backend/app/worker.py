"""The worker process: runs queued jobs (sync, webhooks, cleanup) and the periodic schedule.

    python -m app.worker        (the `worker` service in docker-compose.yml)

Set RUN_WORKER=embedded on the web app instead to run all of this inside it (single-container setups).
"""
import logging
import os
import signal
import threading

from app.blobs import attachments  # noqa: F401  (importing registers the job handlers)
from app.data import audit
from app.ops import backup
from app.files import catalog as files
from app.core import jobs
from app.dav import sync
from app.adapters import webhooks
from app.core.db import connection, init_db
from app.web.tables import purge_expired

log = logging.getLogger("probyte.worker")
THREADS = int(os.environ.get("WORKER_THREADS", "4"))
GC_INTERVAL = int(os.environ.get("ATTACHMENT_GC_INTERVAL", "3600"))

jobs.handler("attachments_gc")(attachments.collect_garbage_job)


def prune():
    """Hourly cleanup: old failed jobs, and deletion tombstones no client can still need (clients reload
    fully at least every few hours)."""
    jobs.prune()
    with connection() as conn:
        conn.execute("DELETE FROM record_tombstones WHERE deleted_at < now() - interval '2 days'")
    purge_expired()  # the trash keeps things for 30 days
    with connection() as conn:
        audit.prune(conn)  # record history: 180 days


def start(stop: threading.Event):
    jobs.start(stop, THREADS)
    periodic = [
        (sync.SYNC_INTERVAL, sync.schedule_all, 10),
        (GC_INTERVAL, lambda: jobs.enqueue(None, "attachments_gc", key="attachments_gc"), 60),
        (3600, prune, 120),
        (3600, backup.backup_if_due, 300),  # daily backups (BACKUP_INTERVAL); checked hourly, so restarts don't skip one
    ]
    for seconds, fn, first in periodic:
        threading.Thread(target=jobs.every, args=(stop, seconds, fn, first), daemon=True).start()
    log.info("worker started with %d threads", THREADS)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    init_db()
    from app.core.db import pool
    pool.resize(pool.min_size, max(pool.max_size, THREADS * 2 + 4))  # every thread may be syncing (2 connections each)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    start(stop)
    stop.wait()
    log.info("worker stopping")


if __name__ == "__main__":
    main()
