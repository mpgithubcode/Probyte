"""Email, web address and phone fields: the same validation and storage everywhere."""
import requests

from conftest import API

ROOT = API.replace("/api", "")


def make(user, ftype, name):
    return user.j("POST", f"/tables/{user._t['id']}/fields", json={"name": name, "type": ftype})


def test_rules_in_every_way_values_arrive(user):
    t = user.table(name="Formats")
    user._t = t
    F = user.fields(t["id"])
    email, url, phone = make(user, "email", "Email"), make(user, "url", "Site"), make(user, "phone", "Phone")
    ok = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "A", email["id"]: " Ada@Example.COM ",
                url["id"]: "example.com/about", phone["id"]: "+1 (555)  010-0100 ext 12"}})
    d = ok["data"]
    assert d[email["id"]] == "Ada@example.com" and d[url["id"]] == "https://example.com/about" and d[phone["id"]] == "+1 (555) 010-0100 ext 12"
    for fid, bad_value, msg in ((email["id"], "not an email", "email"), (url["id"], "javascript:alert(1)", "web address"),
                                (url["id"], "has spaces .com", "spaces"), (phone["id"], "call me", "phone"), (phone["id"], "12", "phone")):
        r = user.request("POST", API + f"/tables/{t['id']}/records", json={"data": {fid: bad_value}})
        assert r.status_code == 400 and msg in r.text, (bad_value, r.text)
        r = user.request("PATCH", API + f"/records/{ok['id']}", json={"data": {fid: bad_value}})
        assert r.status_code == 400, bad_value
    # the public API
    key = user.j("POST", "/keys", json={"name": "k", "scope": "write"})["key"]
    r = requests.post(ROOT + f"/api/v1/tables/{t['id']}/records", headers={"Authorization": "Bearer " + key}, json={"fields": {"Email": "nope"}})
    assert r.status_code == 400 and "email" in r.text
    # imports: a value that doesn't fit is reported, the rest of the row isn't lost
    r = user.j("POST", f"/tables/{t['id']}/import", json={"columns": [{"target": F["Name"]}, {"target": email["id"]}],
                                                         "rows": [["B", "b@example.org"], ["C", "bad"]]})
    assert r["created"] == 1 and r["error_count"] == 1
    # a type change keeps only values that fit
    notes = F["Notes"]
    user.j("PATCH", f"/records/{ok['id']}", json={"data": {notes: "+44 20 7946 0000"}})
    other = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "D", notes: "no number here"}})
    user.j("PATCH", f"/fields/{notes}", json={"type": "phone"})
    got = {r["id"]: r["data"].get(notes) for r in user.records(t["id"])}
    assert got[ok["id"]] == "+44 20 7946 0000" and got[other["id"]] is None


def test_contacts_phone_is_a_phone_field(user):
    t = user.table("contacts", "People")
    f = next(f for f in user.j("GET", f"/tables/{t['id']}")["fields"] if f.get("system_key") == "phone")
    assert f["type"] == "phone"
    F = user.fields(t["id"])
    assert user.request("POST", API + f"/tables/{t['id']}/records", json={"data": {F["fn"]: "X", F["phone"]: "phone?"}}).status_code == 400
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["fn"]: "Y", F["phone"]: "+1 555 0100", F["email"]: "y@Example.com"}})
    assert r["data"][F["email"]] == "y@example.com"
