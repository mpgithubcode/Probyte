"""Automations: a trigger, conditions (a view filter) and actions, run by the worker with their maker's rights."""
import time

from conftest import API, Client
from test_edge_cases import Receiver


def wait(check, timeout=15.0):
    end = time.time() + timeout
    while True:
        out = check()
        if out:
            return out
        if time.time() > end:
            raise AssertionError("timed out")
        time.sleep(0.3)


def cond(field, op, value=None):
    return {"conjunction": "and", "items": [{"field": field, "op": op, "value": value}]}


def make(user, t, trigger, actions, conditions=None, name="Auto"):
    return user.j("POST", f"/bases/{user.base_id}/automations", json={
        "name": name, "table_id": t["id"], "trigger": trigger, "actions": actions,
        "conditions": conditions or {"conjunction": "and", "items": []}})


def runs(user, a):
    return user.j("GET", f"/automations/{a['id']}/runs")["runs"]


def test_a_new_record_that_matches_gets_its_fields_set(user):
    t = user.table(name="Leads")
    F = user.fields(t["id"])
    a = make(user, t, {"type": "created"}, [{"type": "update", "fields": {"Notes": "Call {Name} today ({today})"}}],
             cond(F["Status"], "is", "Todo"), "New leads")
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Ada", F["Status"]: "Todo"}})
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Bob", F["Status"]: "Done"}})   # doesn't match
    got = wait(lambda: (user.record(t["id"], r["id"])["data"].get(F["Notes"]) or "").startswith("Call Ada today") and user.record(t["id"], r["id"]))
    assert got["data"][F["Notes"]].startswith("Call Ada today (20")
    time.sleep(1)
    assert [x["ok"] for x in runs(user, a)] == [True]                       # its own change didn't set it off again
    hist = user.j("GET", f"/records/{r['id']}/history")["entries"]
    assert hist[0]["actor"] == "Automation “New leads”"


def test_a_changed_field_creates_a_record_elsewhere(user):
    jobs_t, follow = user.table(name="Jobs"), user.table(name="Follow-ups")
    F = user.fields(jobs_t["id"])
    make(user, jobs_t, {"type": "updated", "fields": [F["Status"]]},
         [{"type": "create", "table_id": follow["id"], "fields": {"Name": "Invoice {Name}", "Status": "Todo"}}])
    r = user.j("POST", f"/tables/{jobs_t['id']}/records", json={"data": {F["Name"]: "Panel swap"}})
    user.j("PATCH", f"/records/{r['id']}", json={"data": {F["Notes"]: "not the watched field"}})
    time.sleep(1.5)
    assert user.records(follow["id"]) == []
    user.j("PATCH", f"/records/{r['id']}", json={"data": {F["Status"]: "Done"}})
    FF = user.fields(follow["id"])
    made = wait(lambda: user.records(follow["id"]))
    assert made[0]["data"][FF["Name"]] == "Invoice Panel swap" and made[0]["data"][FF["Status"]] == "Todo"


def test_starts_matching_runs_once_and_a_webhook_gets_the_record(user):
    rx = Receiver()
    t = user.table(name="Done hook")
    F = user.fields(t["id"])
    make(user, t, {"type": "matches"}, [{"type": "webhook", "url": rx.url}], cond(F["Status"], "is", "Done"))
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Wire garage", F["Status"]: "Doing"}})
    user.j("PATCH", f"/records/{r['id']}", json={"data": {F["Status"]: "Done"}})
    got = rx.wait(1)
    assert got[0]["record"]["fields"]["Name"] == "Wire garage" and got[0]["automation"] == "Auto"
    user.j("PATCH", f"/records/{r['id']}", json={"data": {F["Notes"]: "still done"}})      # still matching: not again
    time.sleep(2)
    assert len(rx.got) == 1


def test_schedules_and_trying_one_now(user):
    t = user.table(name="Daily")
    F = user.fields(t["id"])
    for n, s in (("a", "Todo"), ("b", "Todo"), ("c", "Done")):
        user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: n, F["Status"]: s}})
    a = make(user, t, {"type": "schedule", "every": "day", "at": "07:30"}, [{"type": "update", "fields": {"Notes": "nudged"}}],
             cond(F["Status"], "is", "Todo"))
    assert a["next_run_at"]
    res = user.j("POST", f"/automations/{a['id']}/test", json={})
    assert res["ok"] and res["message"] == "ran for 2 record(s)"
    assert sorted(r["data"].get(F["Notes"]) or "" for r in user.records(t["id"])) == ["", "nudged", "nudged"]


def test_who_may_make_them_and_what_is_refused(user, other_user):
    t = user.table(name="Rules")
    assert user.post(API + f"/bases/{user.base_id}/automations", json={"table_id": t["id"], "trigger": {"type": "created"},
                                                                      "actions": []}).status_code == 400
    assert user.post(API + f"/bases/{user.base_id}/automations", json={"table_id": t["id"], "trigger": {"type": "deleted"},
                                                                      "actions": [{"type": "trash"}]}).status_code == 400
    user.j("POST", f"/bases/{user.base_id}/members", json={"username": other_user.username, "role": "editor"})
    assert other_user.post(API + f"/bases/{user.base_id}/automations", json={
        "table_id": t["id"], "trigger": {"type": "created"}, "actions": [{"type": "trash"}]}).status_code == 403
    assert other_user.get(API + f"/bases/{user.base_id}/automations").status_code == 200
    assert Client().get(API + f"/bases/{user.base_id}/automations").status_code == 404
