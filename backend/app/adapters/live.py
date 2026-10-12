"""Live updates for the web app: GET /api/events is a Server-Sent Events stream that says which of the user's
tables just changed, so open pages fetch the changes (GET /api/tables/{id}/changes) instead of polling.

Changes are announced by the database itself (NOTIFY probyte_changes from the record/field triggers), so writes
from the worker (DAV sync), the API, MCP and shared views all reach the browser. One listener thread per web
process relays them to the connected streams of each table's owner.
"""
import asyncio
import logging
import threading
import time

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.identity.auth import current_user
from app.core.db import DATABASE_URL, connection

log = logging.getLogger("probyte.live")
router = APIRouter()

CHANNEL = "probyte_changes"
HEARTBEAT = 25  # seconds; keeps proxies from closing a quiet stream


class Hub:
    def __init__(self):
        self.lock = threading.Lock()
        self.subs: dict[str, set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]]] = {}  # user id -> streams
        self.owners: dict[str, str | None] = {}  # table id -> owner's user id
        self.thread: threading.Thread | None = None

    def subscribe(self, user_id: str, loop, queue):
        with self.lock:
            self.subs.setdefault(user_id, set()).add((loop, queue))
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._listen, name="live-listener", daemon=True)
                self.thread.start()

    def unsubscribe(self, user_id: str, loop, queue):
        with self.lock:
            s = self.subs.get(user_id)
            if s:
                s.discard((loop, queue))
                if not s:
                    del self.subs[user_id]

    def _owner(self, table_id: str) -> str | None:
        if table_id not in self.owners:
            with connection() as conn:
                row = conn.execute("SELECT user_id FROM tables WHERE id = %s", (table_id,)).fetchone()
            self.owners[table_id] = str(row["user_id"]) if row else None
        return self.owners[table_id]

    def _publish(self, payload: str):
        for tid in {t for t in payload.split(",") if t}:
            try:
                owner = self._owner(tid)
            except Exception:
                continue
            with self.lock:
                targets = list(self.subs.get(owner, ())) if owner else []
            for loop, queue in targets:
                loop.call_soon_threadsafe(queue.put_nowait, tid)

    def _listen(self):
        while True:
            try:
                with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
                    conn.execute(f"LISTEN {CHANNEL}")
                    for n in conn.notifies():
                        self._publish(n.payload)
            except Exception as e:
                log.warning("live listener reconnecting: %s", e)
                time.sleep(3)


hub = Hub()


@router.get("/api/events")
async def events(request: Request):
    user = await current_user(request)
    loop, queue = asyncio.get_running_loop(), asyncio.Queue()
    uid = str(user["id"])
    hub.subscribe(uid, loop, queue)

    async def stream():
        try:
            yield "retry: 5000\n\n"
            while True:
                try:
                    tid = await asyncio.wait_for(queue.get(), HEARTBEAT)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield ": ping\n\n"
                    continue
                pending = {tid}
                await asyncio.sleep(0.15)  # a burst of writes (import, bulk edit, sync) becomes one message per table
                while not queue.empty():
                    pending.add(queue.get_nowait())
                for t in pending:
                    yield f"event: change\ndata: {t}\n\n"
        finally:
            hub.unsubscribe(uid, loop, queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
