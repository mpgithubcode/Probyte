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
    assert d[email["id"]] == "Ada@example.com" and d[url["id"]] == "https://example.com/about" and d[phone["id"]] == "+15550100100 x12"
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
    assert got[ok["id"]] == "+442079460000" and got[other["id"]] is None


def test_contacts_phone_is_a_phone_field(user):
    t = user.table("contacts", "People")
    f = next(f for f in user.j("GET", f"/tables/{t['id']}")["fields"] if f.get("system_key") == "phone")
    assert f["type"] == "phone"
    F = user.fields(t["id"])
    assert user.request("POST", API + f"/tables/{t['id']}/records", json={"data": {F["fn"]: "X", F["phone"]: "phone?"}}).status_code == 400
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["fn"]: "Y", F["phone"]: "+1 555 0100", F["email"]: "y@Example.com"}})
    assert r["data"][F["email"]] == "y@example.com"


def test_phone_numbers_are_stored_one_way(user):
    """Whatever is around a number goes; the digits, a leading + and an extension stay (shown the same way everywhere)."""
    t = user.table(name="Calls")
    user._t = t
    phone = make(user, "phone", "Phone")
    cases = {"Mobile: 555.010.0199": "5550100199", "(555) 010-0199": "5550100199", "1-555-010-0199": "+15550100199",
             "tel:+1 555 010 0199": "+15550100199", "555-010-0199 ext. 7": "5550100199 x7", "5550100199x7": "5550100199 x7",
             "0044 20 7946 0000": "+442079460000", "1-800-FLOWERS": "+18003569377", "Work 555 0101 (office)": "5550101"}
    for typed, stored in cases.items():
        r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {phone["id"]: typed}})
        assert r["data"][phone["id"]] == stored, typed
    # from an address book (vCard import): the same
    c = user.table("contacts", "Book")
    vcf = "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Grace Hopper\r\nTEL;TYPE=CELL:tel:+1-555-010-0123\r\nEND:VCARD\r\n"
    user.post(API + f"/tables/{c['id']}/vcards", files=[("files", ("g.vcf", vcf.encode(), "text/vcard"))]).raise_for_status()
    F = user.fields(c["id"])
    assert user.records(c["id"])[0]["data"][F["phone"]] == "+15550100123"
    # a public link's filter sees numbers as they're shown: (555) 010-0199
    view = user.j("GET", f"/tables/{t['id']}")["views"][0]
    user.j("PATCH", f"/views/{view['id']}", json={"config": {"filter": {"conjunction": "and", "items": [
        {"field": phone["id"], "op": "contains", "value": "(555) 010-0199"}]}}})
    s = user.j("POST", "/shares", json={"name": "L", "base_id": user.base_id, "items": [{"kind": "table", "id": t["id"], "views": [view["id"]]}]})
    shown = requests.get(API + f"/public/{s['token']}/views/{view['id']}").json()["records"]
    assert sorted(r["data"][phone["id"]] for r in shown) == ["+15550100199", "+15550100199", "5550100199", "5550100199",
                                                              "5550100199 x7", "5550100199 x7"]
