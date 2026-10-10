"""The worker process: runs queued jobs (sync, webhooks, cleanup) and the periodic schedule.

    python -m app.worker        (the `worker` service in docker-compose.yml)

Set RUN_WORKER=embedded on the web app instead to run all of this inside it (single-container setups).
"""
import logging
import os
import signal
import threading

from . import attachments, jobs, sync, webhooks  # noqa: F401  (importing registers the job handlers)
from .db import init_db

log = logging.getLogger("probyte.worker")
THREADS = int(os.environ.get("WORKER_THREADS", "4"))
GC_INTERVAL = int(os.environ.get("ATTACHMENT_GC_INTERVAL", "3600"))

jobs.handler("attachments_gc")(attachments.collect_garbage_job)


def start(stop: threading.Event):
    jobs.start(stop, THREADS)
    periodic = [
        (sync.SYNC_INTERVAL, sync.schedule_all, 10),
        (GC_INTERVAL, lambda: jobs.enqueue(None, "attachments_gc", key="attachments_gc"), 60),
        (3600, jobs.prune, 120),
    ]
    for seconds, fn, first in periodic:
        threading.Thread(target=jobs.every, args=(stop, seconds, fn, first), daemon=True).start()
    log.info("worker started with %d threads", THREADS)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    init_db()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    start(stop)
    stop.wait()
    log.info("worker stopping")


if __name__ == "__main__":
    main()
