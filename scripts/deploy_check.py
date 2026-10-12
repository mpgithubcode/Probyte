"""Checks run against a fresh Docker Hub-style stack (see scripts/test-deploy.sh)."""
import time

import requests

B = "http://probyte:8000"
for _ in range(90):
    try:
        if requests.get(B + "/api/auth/config", timeout=2).ok:
            break
    except requests.RequestException:
        pass
    time.sleep(2)
else:
    raise SystemExit("Probyte did not start")

s = requests.Session()
s.post(B + "/api/auth/register", json={"username": "deploycheck", "password": "deploy-check-1"}).raise_for_status()
assert s.get(B + "/api/auth/me").json()["is_admin"]
ws = s.get(B + "/api/workspace").json()
acct = next(a for a in ws["accounts"] if a["is_builtin"])
dav = s.get(B + "/api/dav").json()
assert dav["enabled"] and dav["public_url"] == B + "/", dav             # phones use Probyte's own address

# a calendar table on the built-in server (inside the container)
t = s.post(B + "/api/tables", json={"name": "Agenda", "kind": "mixed", "base_id": ws["bases"][0]["id"],
                                     "account_id": acct["id"], "new_calendar_name": "Agenda"}).json()
F = {}
for f in s.get(B + f"/api/tables/{t['id']}").json()["fields"]:
    F[f.get("system_key") or f["name"]] = f["id"]
s.post(B + f"/api/tables/{t['id']}/records", json={"data": {F["summary"]: "Dentist", F["component"]: "Event",
       F["dtstart"]: "2026-11-02T09:00:00Z", F["dtend"]: "2026-11-02T10:00:00Z"}}).raise_for_status()

# what a phone does: discovery and the calendar's items, from Probyte's own CalDAV server, with the device login
auth = (dav["username"], dav["password"])
r = requests.request("PROPFIND", B + "/.well-known/caldav", auth=auth, headers={"Depth": "0"})
assert r.status_code in (207, 301, 302, 308), r.status_code
path = s.get(B + f"/api/tables/{t['id']}").json()["dav"]["path"]
items = requests.request("PROPFIND", B + path, auth=auth, headers={"Depth": "1"})
assert items.status_code == 207 and b".ics" in items.content, (items.status_code, items.text[:200])
assert requests.request("PROPFIND", B + path, auth=(dav["username"], "wrong"), headers={"Depth": "1"}).status_code == 401
check = s.post(B + "/api/dav/check").json()
assert check["ok"], check

# a backup (made by the worker running inside the same container)
assert s.post(B + "/api/admin/backups").json()["ok"]
for _ in range(60):
    if s.get(B + "/api/admin/backups").json()["backups"]:
        break
    time.sleep(2)
else:
    raise SystemExit("no backup was made")
b = s.get(B + "/api/admin/backups").json()["backups"][0]
assert b["calendars_included"] and b["counts"]["records"] == 1, b
print("deploy checks passed")
