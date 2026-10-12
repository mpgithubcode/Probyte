"""The change feed: one call for every record write, whoever made it (the app, the API, MCP, a public link, an
import, a phone through CalDAV, a sync with another server, a files-table scan).

    events.record_changed(conn, table, "record.updated", record_id, source="app")

It runs inside the writing transaction, so what subscribers do (queue a webhook, render the item a phone will
download, schedule a push to another server) happens exactly when the write is committed, and never when it's
rolled back. The record store doesn't know who listens: subscribers register themselves (app/wiring.py does it for
every program), and each decides from `source` whether the change concerns it. A sync doesn't push back what it
just pulled, for instance.

Sources: "app" (people and API clients), "dav" (a device through Probyte's CalDAV/CardDAV server), "sync" (pulled
from another DAV server), "files" (found in a files table's storage).
"""
import logging
from typing import Callable

log = logging.getLogger("probyte.events")

EVENTS = ("record.created", "record.updated", "record.deleted")

_subscribers: list[Callable] = []
_warned = False


def subscribe(fn: Callable) -> Callable:
    """fn(conn, table, event, record_id, source): called in the writer's transaction. Usable as a decorator."""
    if fn not in _subscribers:
        _subscribers.append(fn)
    return fn


def record_changed(conn, table, event: str, record_id, source: str = "app") -> None:
    global _warned
    if not _subscribers and not _warned:
        _warned = True
        log.error("a record changed but nothing is subscribed to changes: import app.wiring in this program")
    for fn in _subscribers:
        fn(conn, table, event, record_id, source)
