"""Edge cases: broken references, cycles, odd input, concurrency, size, and webhooks beyond the happy path."""
import json
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

from conftest import API, BASE


def add(user, tid, name, type, **options):
    return user.j("POST", f"/tables/{tid}/fields", json={"name": name, "type": type, "options": options})


def new(user, tid, data):
    return user.j("POST", f"/tables/{tid}/records", json={"data": data})


def rows(user, tid):
    return {r["id"]: r["data"] for r in user.records(tid)}


def test_formula_cycles_and_errors_dont_break_the_table(user):
    t = user.table(name="Cycles")
    F = user.fields(t["id"])
    n = add(user, t["id"], "N", "number")["id"]
    a = add(user, t["id"], "A", "formula", expression="{N} + 1")["id"]
    b = add(user, t["id"], "B", "formula", expression="{A} * 2")["id"]
    r = user.patch(API + f"/fields/{a}", json={"options": {"expression": "{B} + 1"}})   # A -> B -> A
    assert r.status_code in (200, 400)
    div = add(user, t["id"], "Div", "formula", expression="{N} / 0")["id"]
    rec = new(user, t["id"], {F["Name"]: "x", n: 5})
    d = rows(user, t["id"])[rec["id"]]               # the table still loads
    assert div in d
    assert user.get(API + f"/tables/{t['id']}").status_code == 200


def test_deleting_what_others_depend_on(user):
    t = user.table(name="Depends")
    other = user.table(name="Target")
    F, O = user.fields(t["id"]), user.fields(other["id"])
    link = add(user, t["id"], "Link", "link", foreignTableId=other["id"])
    look = add(user, t["id"], "Look", "lookup", linkFieldId=link["id"], lookupFieldId=O["Name"])["id"]
    num = add(user, t["id"], "Num", "number")["id"]
    v = user.j("POST", f"/tables/{t['id']}/views", json={"name": "V", "type": "grid", "config": {
        "filter": {"conjunction": "and", "items": [{"field": num, "op": "gt", "value": 1}]},
        "sorts": [{"field": num, "dir": "desc"}], "groups": [{"field": num, "dir": "asc"}]}})
    o = new(user, other["id"], {O["Name"]: "Linked thing"})
    new(user, t["id"], {F["Name"]: "r", num: 5, link["id"]: [o["id"]]})
    user.j("DELETE", f"/fields/{num}")                            # used by a view's filter, sort and group
    assert user.get(API + f"/tables/{t['id']}/records", params={"view": v["id"]}).status_code == 200
    user.j("DELETE", f"/tables/{other['id']}")                    # the linked table goes away
    data = user.j("GET", f"/tables/{t['id']}")
    fields = {f["id"]: f for f in data["fields"]}
    assert len(data["records"]) == 1
    assert fields.get(link["id"], {}).get("options", {}).get("_error") or link["id"] not in fields
    assert fields.get(look, {}).get("options", {}).get("_error") or look not in fields


def test_renaming_a_select_option_renames_the_values(user):
    t = user.table(name="Options")
    F = user.fields(t["id"])
    r = new(user, t["id"], {F["Name"]: "x", F["Status"]: "Todo"})
    f = next(x for x in user.j("GET", f"/tables/{t['id']}")["fields"] if x["id"] == F["Status"])
    choices = [{**c, "name": "To do" if c["name"] == "Todo" else c["name"]} for c in f["options"]["choices"]]
    user.j("PATCH", f"/fields/{F['Status']}", json={"options": {**f["options"], "choices": choices, "renames": {"Todo": "To do"}}})
    assert rows(user, t["id"])[r["id"]][F["Status"]] == "To do"


def test_duplicate_and_odd_field_names(user):
    t = user.table(name="Names")
    r = user.post(API + f"/tables/{t['id']}/fields", json={"name": "Notes", "type": "text", "options": {}})
    assert r.status_code == 400                                   # a second "Notes" would make names ambiguous
    assert user.post(API + f"/tables/{t['id']}/fields", json={"name": "   ", "type": "text", "options": {}}).status_code == 400
    weird = add(user, t["id"], "Price {€} \"quoted\"", "number")["id"]
    f = add(user, t["id"], "Twice", "formula", expression="{" + weird + "} * 2")
    F = user.fields(t["id"])
    rec = new(user, t["id"], {F["Name"]: "émoji 🎉 ünïcode " + "x" * 5000, weird: 2})
    d = rows(user, t["id"])[rec["id"]]
    assert d[f["id"]] == 4 and d[F["Name"]].startswith("émoji 🎉")


def test_concurrent_edits_to_one_record_keep_every_field(user):
    t = user.table(name="Race")
    F = user.fields(t["id"])
    cols = [add(user, t["id"], f"C{i}", "number")["id"] for i in range(8)]
    rec = new(user, t["id"], {F["Name"]: "x"})
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda c: user.j("PATCH", f"/records/{rec['id']}", json={"data": {c: 1}}), cols))
    d = rows(user, t["id"])[rec["id"]]
    assert all(d.get(c) == 1 for c in cols), d


def test_concurrent_creates_get_unique_auto_numbers(user):
    t = user.table(name="Numbers")
    F = user.fields(t["id"])
    auto = add(user, t["id"], "No", "autonumber")["id"]
    with ThreadPoolExecutor(10) as pool:
        list(pool.map(lambda i: new(user, t["id"], {F["Name"]: str(i)}), range(30)))
    nums = sorted(d[auto] for d in rows(user, t["id"]).values())
    assert nums == list(range(1, 31))


def test_large_import_and_bulk(user):
    t = user.table(name="Big")
    F = user.fields(t["id"])
    rows_in = [[f"Row {i}", "Todo" if i % 2 else "Done"] for i in range(3000)]
    t0 = time.time()
    res = user.j("POST", f"/tables/{t['id']}/import", json={"columns": [{"target": F["Name"]}, {"target": F["Status"]}], "rows": rows_in})
    assert res["created"] == 3000 and time.time() - t0 < 60
    recs = user.records(t["id"])
    assert len(recs) == 3000
    items = [{"id": r["id"], "table_id": t["id"]} for r in recs[:2000]]
    assert user.j("POST", "/records/bulk", json={"action": "update", "items": items, "data": {F["Notes"]: "bulk"}})["done"] == 2000
    assert user.post(API + "/records/bulk", json={"action": "delete", "items": [{"id": "x", "table_id": t["id"]}] * 6000}).status_code == 400


def test_malformed_ids_and_bodies(user):
    t = user.table(name="Malformed")
    for method, path, body in (("GET", "/records/not-a-uuid", None), ("PATCH", "/records/123", {"data": {}}),
                               ("DELETE", "/tables/zzz", None), ("GET", "/combos/abc", None), ("PATCH", "/views/abc", {"name": "x"}),
                               ("POST", f"/tables/{t['id']}/records", {"data": "not an object"}),
                               ("POST", f"/tables/{t['id']}/records", {"data": {user.fields(t["id"])["Name"]: "x", add(user, t["id"], "Num", "number")["id"]: "abc"}})):
        code = user.request(method, API + path, json=body).status_code
        assert code in (400, 404, 422), (method, path, code)
    r = user.post(API + f"/tables/{t['id']}/records", data="{", headers={"Content-Type": "application/json"})
    assert r.status_code in (400, 422)


class Receiver:
    def __init__(self, fail_first=0):
        self.got, self.fail_first = [], fail_first
        rec = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                rec.got.append(body)
                self.send_response(500 if len(rec.got) <= rec.fail_first else 200)
                self.end_headers()

            def log_message(self, *a):
                pass
        self.srv = HTTPServer(("0.0.0.0", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://{socket.gethostbyname(socket.gethostname())}:{self.srv.server_port}/hook"

    def wait(self, n, timeout=15):
        end = time.time() + timeout
        while time.time() < end and len(self.got) < n:
            time.sleep(0.2)
        return self.got


def test_webhook_events_filters_and_coalescing(user):
    rx = Receiver()
    t, other = user.table(name="Hooked"), user.table(name="Ignored")
    F, O = user.fields(t["id"]), user.fields(other["id"])
    user.j("POST", "/v1/webhooks", json={"url": rx.url, "tables": [t["id"]], "events": ["record.updated", "record.deleted"]})
    time.sleep(1.2)
    r = new(user, t["id"], {F["Name"]: "a"})                              # created: not subscribed
    new(user, other["id"], {O["Name"]: "b"})                               # other table
    time.sleep(1.5)
    for i in range(5):                                                     # five quick edits -> one event
        user.j("PATCH", f"/records/{r['id']}", json={"data": {F["Notes"]: str(i)}})
    got = rx.wait(1)
    time.sleep(2)
    events = [g["event"] for g in rx.got]
    assert set(events) == {"record.updated"} and len(events) <= 2, events   # coalesced (edits within a second)
    assert rx.got[-1]["record"]["fields"]["Notes"] == "4"
    user.j("DELETE", f"/records/{r['id']}")
    rx.wait(2)
    assert rx.got[-1]["event"] == "record.deleted" and rx.got[-1]["record"]["id"] == r["id"]


def test_webhook_retries_after_server_errors(user):
    rx = Receiver(fail_first=1)
    t = user.table(name="Retried")
    F = user.fields(t["id"])
    h = user.j("POST", "/v1/webhooks", json={"url": rx.url, "tables": [t["id"]]})
    time.sleep(1.2)
    new(user, t["id"], {F["Name"]: "again"})
    got = rx.wait(2, timeout=30)                                          # first answer 500, retried after ~5 s
    assert len(got) >= 2 and got[0]["id"] == got[1]["id"]                 # same delivery id
    hooks = user.j("GET", "/v1/webhooks")["webhooks"]
    assert next(x for x in hooks if x["id"] == h["id"])["last_status"] == 200


def test_disabled_webhook_sends_nothing(user):
    rx = Receiver()
    t = user.table(name="Quiet")
    F = user.fields(t["id"])
    h = user.j("POST", "/v1/webhooks", json={"url": rx.url, "enabled": False})
    time.sleep(1.2)
    new(user, t["id"], {F["Name"]: "x"})
    time.sleep(3)
    assert rx.got == []


def test_renaming_a_multi_select_option(user):
    t = user.table(name="Multi")
    F = user.fields(t["id"])
    tags = add(user, t["id"], "Tags", "multi_select", choices=[{"name": "red"}, {"name": "blue"}])
    r = new(user, t["id"], {F["Name"]: "x", tags["id"]: ["red", "blue"]})
    choices = [{"name": "crimson" if c["name"] == "red" else c["name"], "color": c.get("color")} for c in tags["options"]["choices"]]
    user.j("PATCH", f"/fields/{tags['id']}", json={"options": {"choices": choices, "renames": {"red": "crimson"}}})
    assert sorted(rows(user, t["id"])[r["id"]][tags["id"]]) == ["blue", "crimson"]
    stored = next(f for f in user.j("GET", f"/tables/{t['id']}")["fields"] if f["id"] == tags["id"])
    assert "renames" not in stored["options"]


def test_link_counterpart_gets_a_free_name(user):
    a, b = user.table(name="Orders"), user.table(name="Items")
    add(user, b["id"], "Orders", "text")                     # the name the counterpart would take
    link = add(user, a["id"], "Items", "link", foreignTableId=b["id"])
    names = [f["name"] for f in user.j("GET", f"/tables/{b['id']}")["fields"]]
    assert names.count("Orders") == 1 and "Orders 2" in names
    assert user.patch(API + f"/fields/{link['id']}", json={"name": "Name"}).status_code == 400   # rename onto an existing name


def test_created_then_edited_quickly_reaches_update_only_hooks(user):
    rx_updates, rx_all = Receiver(), Receiver()
    t = user.table(name="Quick")
    F = user.fields(t["id"])
    user.j("POST", "/v1/webhooks", json={"url": rx_updates.url, "events": ["record.updated"]})
    user.j("POST", "/v1/webhooks", json={"url": rx_all.url})
    time.sleep(1.2)
    r = new(user, t["id"], {F["Name"]: "a"})
    user.j("PATCH", f"/records/{r['id']}", json={"data": {F["Notes"]: "edited"}})   # within the coalescing second
    assert [g["event"] for g in rx_updates.wait(1)] == ["record.updated"]
    got = rx_all.wait(1)
    time.sleep(1.5)
    assert [g["event"] for g in rx_all.got] == ["record.created"] and got[0]["record"]["fields"]["Notes"] == "edited"
