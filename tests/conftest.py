"""Shared fixtures. Tests run against an isolated test stack (scripts/test.sh), never a real instance."""
import os
import re
import uuid

import pytest
import requests

BASE = os.environ.get("PROBYTE_URL", "http://app:8000").rstrip("/")
API = BASE + "/api"
RADICALE = os.environ.get("RADICALE_URL", "http://radicale:5232").rstrip("/")


class Client(requests.Session):
    """A logged-in test user with small helpers."""

    def __init__(self):
        super().__init__()
        self.username = "t" + uuid.uuid4().hex[:10]
        self.password = "test-pass-123"
        r = self.post(API + "/auth/register", json={"username": self.username, "password": self.password})
        r.raise_for_status()

    def j(self, method, path, **kw):
        r = self.request(method, API + path, **kw)
        assert r.status_code < 400, f"{method} {path} -> {r.status_code} {r.text[:300]}"
        return r.json() if r.content else None

    def workspace(self):
        return self.j("GET", "/workspace")

    @property
    def base_id(self):
        return self.workspace()["bases"][0]["id"]

    @property
    def builtin(self):
        return next(a for a in self.workspace()["accounts"] if a["is_builtin"])

    def dav_auth(self):
        d = self.j("GET", "/dav")
        return d["username"], d["password"]

    def table(self, kind="generic", name=None, **extra):
        body = {"name": name or f"{kind} {uuid.uuid4().hex[:6]}", "kind": kind, "base_id": self.base_id, **extra}
        if kind in ("tasks", "events", "mixed", "contacts") and "account_id" not in extra:
            body["account_id"] = self.builtin["id"]
            body.setdefault("new_calendar_name", body["name"])
        return self.j("POST", "/tables", json=body)

    def fields(self, table_id):
        """{system_key or field name: field id}"""
        out = {}
        for f in self.j("GET", f"/tables/{table_id}")["fields"]:
            out.setdefault(f["name"], f["id"])
            if f.get("system_key"):
                out[f["system_key"]] = f["id"]
        return out

    def records(self, table_id):
        return self.j("GET", f"/tables/{table_id}")["records"]

    def record(self, table_id, record_id):
        return next(r for r in self.records(table_id) if r["id"] == record_id)

    def sync(self, table_id):
        return self.j("POST", f"/tables/{table_id}/sync")

    def server_items(self, collection_url):
        """[(href path, body)] of every item in a DAV collection, read straight from the test Radicale."""
        auth = self.dav_auth()
        listing = requests.request("PROPFIND", collection_url, auth=auth, headers={"Depth": "1"}).text
        hrefs = [h for h in re.findall(r"href>([^<]+\.(?:ics|vcf))<", listing)]
        return [(h, requests.get(RADICALE + h, auth=auth).text) for h in hrefs]

    def server_item(self, collection_url, text):
        return next((h, b) for h, b in self.server_items(collection_url) if text in b)

    def put_item(self, href, body):
        r = requests.put(RADICALE + href, data=body.encode(), auth=self.dav_auth(), headers={"Content-Type": "text/calendar"})
        assert r.status_code in (200, 201, 204), r.status_code


@pytest.fixture
def user():
    return Client()


@pytest.fixture
def other_user():
    return Client()


# ---------------------------------------------------------------- screenshots of failed browser tests

ARTIFACTS = os.path.join(os.path.dirname(__file__), ".artifacts")
pages = []  # browser tests append their pages here; failures save a screenshot to tests/.artifacts/


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    out = yield
    rep = out.get_result()
    if rep.when == "call" and rep.failed:
        os.makedirs(ARTIFACTS, exist_ok=True)
        for i, pg in enumerate(pages):
            try:
                pg.screenshot(path=os.path.join(ARTIFACTS, f"{item.name}-{i}.png"), full_page=True)
            except Exception:
                pass
    if rep.when == "call":
        pages.clear()
