"""Computed values are cached between requests (per table version, refreshed incrementally); they must never go stale."""


def _data(user, tid):
    return {r["id"]: r["data"] for r in user.records(tid)}


def test_cached_values_follow_every_change(user):
    clients = user.table(name="Clients")
    C = user.fields(clients["id"])
    acme = user.j("POST", f"/tables/{clients['id']}/records", json={"data": {C["Name"]: "Acme"}})
    t = user.table(name="Jobs")
    F = user.fields(t["id"])
    mk = lambda name, type, options: user.j("POST", f"/tables/{t['id']}/fields", json={"name": name, "type": type, "options": options})["id"]
    amount = mk("Amount", "number", {})
    double = mk("Double", "formula", {"expression": "{Amount} * 2"})
    link = mk("Client", "link", {"foreignTableId": clients["id"]})
    client_name = mk("Client name", "lookup", {"linkFieldId": link, "lookupFieldId": C["Name"]})
    ids = [user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: f"Job {i}", amount: i, link: [acme["id"]]}})["id"]
           for i in range(1, 4)]
    d = _data(user, t["id"])  # now cached
    assert d[ids[1]][double] == 4 and d[ids[1]][client_name] == ["Acme"]

    user.j("PATCH", f"/records/{ids[1]}", json={"data": {amount: 10}})            # own record changed: incremental
    assert _data(user, t["id"])[ids[1]][double] == 20

    user.j("PATCH", f"/records/{acme['id']}", json={"data": {C["Name"]: "Acme Ltd"}})  # a table it depends on
    assert all(v[client_name] == ["Acme Ltd"] for v in _data(user, t["id"]).values())

    user.j("DELETE", f"/records/{ids[0]}")                                      # deleted outright
    assert ids[0] not in _data(user, t["id"])

    new = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Job 9", amount: 9}})["id"]
    d = _data(user, t["id"])
    assert d[new][double] == 18 and list(d)[-1] == new                          # new rows keep table order

    user.j("PATCH", f"/fields/{double}", json={"options": {"expression": "{Amount} * 3"}})  # a field changed
    assert _data(user, t["id"])[new][double] == 27

    user.j("POST", "/records/bulk", json={"action": "update", "items": [{"table_id": t["id"], "id": i} for i in ids[1:]],
                                         "data": {amount: 1}})
    assert {v[double] for k, v in _data(user, t["id"]).items() if k in ids} == {3}
