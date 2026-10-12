"""Connects the components through the change feed (app/core/events.py). Every program (main, worker, admin)
imports this first; it's the one place that says who reacts to a record change.

    record write ──► events.record_changed ──┬──► dav.server     the item a phone downloads is rendered now
                                             ├──► dav.sync       a push to the linked CalDAV/CardDAV server is queued
                                             └──► webhooks       a delivery is queued

and that the computed-values engine reads linked tables through sharing.linked (calc.compute.READERS).
"""
from app.adapters import webhooks
from app.core import events
from app.dav import server as dav_server, sync as dav_sync
from app.sharing import linked  # noqa: F401  (registers how linked tables' records are read)

events.subscribe(dav_server.on_record_change)   # first: the others may read the rendered item
events.subscribe(dav_sync.on_record_change)
events.subscribe(webhooks.on_record_change)
