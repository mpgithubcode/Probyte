"""The worker program: runs queued jobs (syncs with other servers, webhook deliveries, previews, cleanup) and the
periodic schedule.

    python -m app.worker        (the `worker` service in docker-compose.yml)

Set RUN_WORKER=embedded on the web app instead to run all of this inside it (single-container setups).
"""
import logging
import os
import signal
import threading

from app import wiring  # noqa: F401  (connects the components: before anything writes)
from app.adapters import webhooks  # noqa: F401  (importing registers the job handlers)
from app.automations import engine as automations
from app.blobs import attachments
from app.core import jobs
from app.core.db import connection, init_db
from app.data import audit
from app.dav import radicale_import, sync
from app.files import catalog as files  # noqa: F401
from app.ops import backup
from app.web.tables import purge_expired

log = logging.getLogger("probyte.worker")
THREADS = int(os.environ.get("WORKER_THREADS", "4"))
GC_INTERVAL = int(os.environ.get("ATTACHMENT_GC_INTERVAL", "3600"))

jobs.handler("attachments_gc")(attachments.collect_garbage_job)


def prune():
    """Hourly cleanup: old failed jobs, and deletion tombstones no client can still need. The web app reloads fully
    at least every few hours; phones are told "deleted" through sync-collection, and one that hasn't synced for a
    month is told to start over (a sync-token older than tombstone_floor)."""
    jobs.prune()
    with connection() as conn:
        conn.execute("DELETE FROM record_tombstones WHERE href IS NULL AND deleted_at < now() - interval '2 days'")
        gone = conn.execute("WITH gone AS (DELETE FROM record_tombstones WHERE href IS NOT NULL AND deleted_at < now() - interval '30 days' "
                            "RETURNING row_version) SELECT max(row_version) AS v FROM gone").fetchone()["v"]
        if gone:
            conn.execute("INSERT INTO app_settings (key, value) VALUES ('tombstone_floor', %s) ON CONFLICT (key) DO UPDATE SET "
                         "value = GREATEST(app_settings.value::bigint, EXCLUDED.value::bigint)::text", (str(gone),))
    purge_expired()  # the trash keeps things for 30 days
    with connection() as conn:
        audit.prune(conn)  # record history: 180 days
        automations.prune(conn)  # automation runs: 30 days


def start(stop: threading.Event):
    jobs.start(stop, THREADS)
    radicale_import.schedule_if_pending()
    periodic = [
        (sync.SYNC_INTERVAL, sync.schedule_all, 10),
        (GC_INTERVAL, lambda: jobs.enqueue(None, "attachments_gc", key="attachments_gc"), 60),
        (3600, prune, 120),
        (3600, backup.backup_if_due, 300),  # daily backups (BACKUP_INTERVAL); checked hourly, so restarts don't skip one
        (60, automations.run_due, 30),  # scheduled automations
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
