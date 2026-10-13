"""Repeating tasks and records: completing one moves it to its next date and keeps a done copy; a later date can be
done ahead of time."""
from conftest import API


def rows(user, t, F, *keys):
    return sorted((tuple(r["data"].get(F[k]) for k in keys) for r in user.j("GET", f"/tables/{t['id']}")["records"]), key=str)


def test_a_task_moves_on_and_a_later_date_can_be_done_early(user):
    t = user.table("tasks", "Chores")
    F = user.fields(t["id"])
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Trash", F["due"]: "2026-10-13T14:00:00Z", F["rrule"]: "FREQ=WEEKLY"}})
    # the 27th, ahead of time: a done copy on that date, skipped in the series
    user.j("POST", f"/records/{r['id']}/complete", json={"occurrence": "2026-10-27T14:00:00Z"})
    assert rows(user, t, F, "done", "due") == sorted([(False, "2026-10-13T14:00:00Z"), (True, "2026-10-27T14:00:00Z")], key=str)
    # ticking the current one: it moves to the 20th; the next after that skips the 27th
    user.j("PATCH", f"/records/{r['id']}", json={"data": {F["done"]: True}})
    assert user.record(t["id"], r["id"])["data"][F["due"]] == "2026-10-20T14:00:00Z"
    user.j("PATCH", f"/records/{r['id']}", json={"data": {F["status"]: "Completed"}})
    cur = user.record(t["id"], r["id"])["data"]
    # (the same time of day where the server is: across a clock change, that's another hour in UTC)
    assert cur[F["due"]].startswith("2026-11-03T") and cur[F["done"]] is False and cur[F["status"]] == "To Do"
    done = [x for x in rows(user, t, F, "done", "status", "rrule") if x[0]]
    assert done == [(True, "Completed", None)] * 3   # each done copy says so, and doesn't repeat


def test_a_task_without_a_date_comes_back(user):
    t = user.table("tasks", "Daily")
    F = user.fields(t["id"])
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Stretch", F["rrule"]: "FREQ=DAILY"}})
    user.j("PATCH", f"/records/{r['id']}", json={"data": {F["done"]: True}})
    cur = user.record(t["id"], r["id"])["data"]
    assert cur[F["done"]] is False and cur[F["due"]] and cur[F["rrule"]] == "FREQ=DAILY"


def test_a_repeating_record_in_an_ordinary_table(user):
    t = user.table(name="Plants")
    F = user.fields(t["id"])
    rep = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Repeat", "type": "recurrence"})
    due = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Due", "type": "date"})
    box = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Watered", "type": "checkbox"})
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Fern", rep["id"]: "FREQ=WEEKLY", due["id"]: "2026-10-13"}})
    user.j("PATCH", f"/records/{r['id']}", json={"data": {box["id"]: True}})
    got = sorted((x["data"].get(box["id"]), x["data"].get(due["id"]), x["data"].get(rep["id"])) for x in user.j("GET", f"/tables/{t['id']}")["records"])
    assert got == [(False, "2026-10-20", "FREQ=WEEKLY"), (True, "2026-10-13", None)]
    # a second checkbox: the repeat needs to be told which one completes it; "none" turns it off
    other = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Fed", "type": "checkbox"})
    bad = user.patch(API + f"/fields/{rep['id']}", json={"options": {"doneField": due["id"]}})
    assert bad.status_code == 400
    user.j("PATCH", f"/fields/{rep['id']}", json={"options": {"doneField": other["id"]}})
    user.j("PATCH", f"/records/{r['id']}", json={"data": {box["id"]: True}})
    assert user.record(t["id"], r["id"])["data"][due["id"]] == "2026-10-20"   # not this box
    user.j("PATCH", f"/records/{r['id']}", json={"data": {other["id"]: True}})
    assert user.record(t["id"], r["id"])["data"][due["id"]] == "2026-10-27"
