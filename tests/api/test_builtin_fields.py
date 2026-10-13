"""Every table has Created time, Last modified time, Created by and Last modified by: built in, not deletable."""
from conftest import API, Client


def test_every_table_has_the_built_in_fields(user):
    for kind in ("generic", "tasks", "contacts"):
        t = user.table(kind)
        fields = {f.get("system_key"): f for f in user.j("GET", f"/tables/{t['id']}")["fields"]}
        for key, name, ftype in (("created_time", "Created time", "created_time"), ("modified_time", "Last modified time", "last_modified_time"),
                                 ("created_by", "Created by", "created_by"), ("modified_by", "Last modified by", "last_modified_by")):
            assert fields[key]["name"] == name and fields[key]["type"] == ftype, (kind, key)
            assert user.delete(API + f"/fields/{fields[key]['id']}").status_code == 400
            assert user.patch(API + f"/fields/{fields[key]['id']}", json={"name": "Mine"}).status_code == 400
    t = user.table(name="Stamped")
    F = user.fields(t["id"])
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "one"}})
    other = Client()
    user.j("POST", f"/bases/{user.base_id}/members", json={"username": other.username, "role": "editor"})
    other.j("PATCH", f"/records/{r['id']}", json={"data": {F["Name"]: "two"}})
    d = user.record(t["id"], r["id"])["data"]
    me, them = user.j("GET", "/auth/me")["id"], other.j("GET", "/auth/me")["id"]
    assert d[F["created_by"]] == me and d[F["modified_by"]] == them
    assert d[F["created_time"]] and d[F["modified_time"]] >= d[F["created_time"]]

