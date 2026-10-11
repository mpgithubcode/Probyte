"""Timing of the main read/write paths at different table sizes, on the isolated test stack.

    scripts/test.sh bench            # 1k, 10k and 50k rows
    scripts/test.sh bench 10000      # chosen sizes

Seeds rows straight into Postgres (fast), then times the HTTP endpoints the web app uses.
"""
import json
import os
import random
import statistics
import sys
import time
import uuid

import psycopg
import requests

BASE = os.environ.get("PROBYTE_URL", "http://app:8000").rstrip("/")
API = BASE + "/api"
DB = os.environ.get("BENCH_DB", "postgresql://probyte:probyte-test@db:5432/probyte")


def timed(fn, n=3):
    out = []
    for _ in range(n):
        t = time.perf_counter()
        r = fn()
        out.append(time.perf_counter() - t)
        r.raise_for_status()
    return statistics.median(out), r


def main(sizes):
    s = requests.Session()
    name = "bench" + uuid.uuid4().hex[:8]
    s.post(API + "/auth/register", json={"username": name, "password": "bench-password-1"}).raise_for_status()
    base = s.get(API + "/workspace").json()["bases"][0]["id"]
    clients = s.post(API + "/tables", json={"base_id": base, "name": "Clients"}).json()
    cf = {f["name"]: f["id"] for f in s.get(f"{API}/tables/{clients['id']}").json()["fields"]}
    with psycopg.connect(DB) as db:
        db.execute("INSERT INTO records (table_id, data, seq) SELECT %s, jsonb_build_object(%s::text, 'Client ' || i), i FROM generate_series(1, 200) i",
                   (clients["id"], cf["Name"]))
    client_ids = [r["id"] for r in s.get(f"{API}/tables/{clients['id']}").json()["records"]]
    print(f"{'rows':>7} {'open (cached)':>14} {'open after edit':>16} {'MB raw':>7} {'MB sent':>8} {'records?view':>13} {'edit 1 record':>14} {'create':>8}")
    for n in sizes:
        t = s.post(API + "/tables", json={"base_id": base, "name": f"Projects {n}"}).json()
        tid = t["id"]
        F = {f["name"]: f["id"] for f in s.get(f"{API}/tables/{tid}").json()["fields"]}
        mk = lambda name, type, options=None: s.post(f"{API}/tables/{tid}/fields", json={"name": name, "type": type, "options": options or {}}).json()["id"]
        F["Amount"] = mk("Amount", "number")
        F["Status"] = mk("Status", "single_select", {"choices": [{"name": c} for c in ("New", "Active", "Done")]})
        F["Due"] = mk("Due", "date")
        F["Double"] = mk("Double", "formula", {"expression": "{Amount} * 2"})
        F["Client"] = mk("Client", "link", {"foreignTableId": clients["id"]})
        F["Client name"] = mk("Client name", "lookup", {"linkFieldId": F["Client"], "lookupFieldId": cf["Name"]})
        rows = []
        for i in range(n):
            rows.append((tid, json.dumps({F["Name"]: f"Project {i}", F["Amount"]: random.randint(1, 1000),
                                          F["Status"]: random.choice(["New", "Active", "Done"]), F["Due"]: f"2026-{random.randint(1, 12):02d}-15",
                                          F["Client"]: [random.choice(client_ids)]}), i + 1))
        with psycopg.connect(DB) as db, db.cursor() as cur:
            with cur.copy("COPY records (table_id, data, seq) FROM STDIN") as cp:
                for row in rows:
                    cp.write_row(row)
        view = s.post(f"{API}/tables/{tid}/views", json={"name": "Active", "type": "grid", "config": {
            "filter": {"conjunction": "and", "items": [{"field": F["Status"], "op": "is", "value": "Active"}]},
            "sorts": [{"field": F["Amount"], "dir": "desc"}]}}).json()
        open_t, r = timed(lambda: s.get(f"{API}/tables/{tid}"))
        size = len(r.content) / 1e6
        rid = r.json()["records"][n // 2]["id"]
        view_t, _ = timed(lambda: s.get(f"{API}/tables/{tid}/records", params={"view": view["id"]}))
        edit_t, _ = timed(lambda: s.patch(f"{API}/records/{rid}", json={"data": {F["Amount"]: random.randint(1, 1000)}}))
        create_t, _ = timed(lambda: s.post(f"{API}/tables/{tid}/records", json={"data": {F["Name"]: "New one"}}))
        cold = []
        for _ in range(3):
            s.patch(f"{API}/records/{rid}", json={"data": {F["Amount"]: random.randint(1, 1000)}}).raise_for_status()
            t0 = time.perf_counter()
            s.get(f"{API}/tables/{tid}").raise_for_status()
            cold.append(time.perf_counter() - t0)
        raw = s.get(f"{API}/tables/{tid}", stream=True)
        sent = len(raw.raw.read(decode_content=False)) / 1e6
        print(f"{n:>7} {open_t * 1000:>12.0f}ms {statistics.median(cold) * 1000:>14.0f}ms {size:>7.1f} {sent:>8.1f} "
              f"{view_t * 1000:>11.0f}ms {edit_t * 1000:>12.0f}ms {create_t * 1000:>6.0f}ms", flush=True)


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]] or [1000, 10000, 50000])
