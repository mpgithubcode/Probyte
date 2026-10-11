"""Live updates: the change feed (/tables/{id}/changes) and the event stream (/events)."""
import queue
import threading
import time

from conftest import API


def test_change_feed(user):
    t = user.table(name="Feed")
    F = user.fields(t["id"])
    tok = user.j("GET", f"/tables/{t['id']}")["token"]
    a = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "A"}})
    b = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "B"}})
    ch = user.j("GET", f"/tables/{t['id']}/changes", params={"since": tok})
    assert {r["id"] for r in ch["records"]} == {a["id"], b["id"]} and ch["deleted"] == []
    tok = ch["token"]
    assert user.j("GET", f"/tables/{t['id']}/changes", params={"since": tok})["records"] == []   # nothing new
    user.j("PATCH", f"/records/{a['id']}", json={"data": {F["Name"]: "A2"}})
    user.j("DELETE", f"/records/{b['id']}")
    ch = user.j("GET", f"/tables/{t['id']}/changes", params={"since": tok})
    assert [r["data"][F["Name"]] for r in ch["records"]] == ["A2"] and ch["deleted"] == [b["id"]]
    user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Extra", "type": "text", "options": {}})
    assert user.j("GET", f"/tables/{t['id']}/changes", params={"since": ch["token"]})["reload"] is True
    assert user.j("GET", f"/tables/{t['id']}/changes", params={"since": "junk"})["reload"] is True


def _listen(session, out: queue.Queue, stop: threading.Event):
    with session.get(API + "/events", stream=True, timeout=30) as r:
        for line in r.iter_lines(decode_unicode=True):
            if stop.is_set():
                return
            if line and line.startswith("data: "):
                out.put(line[6:])


def test_event_stream_tells_only_the_owner(user, other_user):
    t = user.table(name="Live")
    F = user.fields(t["id"])
    mine, theirs, stop = queue.Queue(), queue.Queue(), threading.Event()
    for s, q in ((user, mine), (other_user, theirs)):
        threading.Thread(target=_listen, args=(s, q, stop), daemon=True).start()
    time.sleep(1)
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Ping"}})
    try:
        assert mine.get(timeout=10) == t["id"]
        time.sleep(1)
        assert theirs.empty()
    finally:
        stop.set()
