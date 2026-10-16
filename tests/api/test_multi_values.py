"""Fields with several values (options.multi "list" / "labeled"): text, email, URL and phone fields that hold a list
of {value, label}, the first the primary one; every value checked as the field's type checks one; filters see them
all, a sort uses the primary; switching to one value and back loses nothing; contacts keep every number a phone has."""
import requests

from conftest import API, BASE


def _key(u):
    return {"Authorization": "Bearer " + u.j("POST", "/keys", json={"name": "k", "scope": "write"})["key"]}


def test_several_values_are_written_checked_and_found(user):
    t = user.table(name="Companies")
    F = user.fields(t["id"])
    site = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Websites", "type": "url", "options": {"multi": "list"}})
    mail = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Emails", "type": "email", "options": {"multi": "labeled"}})
    assert site["options"]["multi"] == "list" and mail["options"]["multi"] == "labeled"
    a = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Acme", site["id"]: ["acme.com", "shop.acme.com"],
        mail["id"]: [{"value": "Sales@Acme.com", "label": "work"}, {"value": "boss@acme.com", "label": "  ceo  "}]}})
    assert a["data"][site["id"]] == [{"value": "https://acme.com"}, {"value": "https://shop.acme.com"}]          # each a URL
    assert a["data"][mail["id"]] == [{"value": "Sales@acme.com", "label": "work"}, {"value": "boss@acme.com", "label": "ceo"}]
    b = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Beta", site["id"]: "beta.io"}})   # one, as a string
    assert b["data"][site["id"]] == [{"value": "https://beta.io"}]
    bad = user.post(API + f"/tables/{t['id']}/records", json={"data": {mail["id"]: ["ok@x.org", "not an email"]}})
    assert bad.status_code == 400 and "not an email" in bad.text
    # the REST API: by name, as a string with "; " or a list; text output lists them with labels
    H = _key(user)
    r = requests.post(BASE + "/api/v1/tables/Companies/records", headers=H, json={"fields": {"Name": "Gamma", "Emails": "work: g@gamma.dev; info@gamma.dev"}})
    assert r.status_code == 201, r.text
    assert r.json()["fields"]["Emails"] == [{"value": "g@gamma.dev", "label": "work"}, {"value": "info@gamma.dev"}]
    txt = requests.get(BASE + "/api/v1/tables/Companies/records", headers=H, params={"cell_format": "text", "filter": '{"Name": "Acme"}'}).json()
    assert txt["records"][0]["fields"]["Emails"] == "work: Sales@acme.com; ceo: boss@acme.com"
    # a filter sees every value; a sort uses the first
    found = requests.get(BASE + "/api/v1/tables/Companies/records", headers=H,
                         params={"filter": '[{"field": "Websites", "op": "contains", "value": "shop."}]'}).json()
    assert [x["fields"]["Name"] for x in found["records"]] == ["Acme"]
    srt = requests.get(BASE + "/api/v1/tables/Companies/records", headers=H, params={"sort": "Websites"}).json()
    assert [x["fields"]["Name"] for x in srt["records"] if x["fields"].get("Websites")][:2] == ["Acme", "Beta"]


def test_one_value_and_back_loses_nothing(user):
    t = user.table(name="People")
    F = user.fields(t["id"])
    ph = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Phone", "type": "phone"})
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Ada", ph["id"]: "555-123-4567"}})
    f = user.j("PATCH", f"/fields/{ph['id']}", json={"options": {"multi": "labeled"}})
    assert f["options"]["multi"] == "labeled"
    assert user.record(t["id"], r["id"])["data"][ph["id"]] == [{"value": "5551234567"}]      # the one value, now the first
    user.j("PATCH", f"/records/{r['id']}", json={"data": {ph["id"]: [{"value": "5551234567", "label": "work"}, {"value": "555 999 0000", "label": "mobile"}]}})
    p = user.j("POST", f"/fields/{ph['id']}/conversion", json={"options": {}})
    assert p["kept"] == 1                                                                       # the dialog says one is kept aside
    user.j("PATCH", f"/fields/{ph['id']}", json={"options": {}})                               # back to one value: the first
    assert user.record(t["id"], r["id"])["data"][ph["id"]] == "5551234567"
    user.j("PATCH", f"/fields/{ph['id']}", json={"options": {"multi": "labeled"}})             # several again: both back
    assert user.record(t["id"], r["id"])["data"][ph["id"]] == [{"value": "5551234567", "label": "work"}, {"value": "5559990000", "label": "mobile"}]
    # not for the title field
    assert user.patch(API + f"/fields/{F['Name']}", json={"options": {"multi": "list"}}).status_code == 400


def test_unique_looks_at_every_value(user):
    t = user.table(name="Leads")
    F = user.fields(t["id"])
    mail = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Emails", "type": "email", "options": {"multi": "list"}, "unique": True})
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "A", mail["id"]: ["a@x.org", "shared@x.org"]}})
    r = user.post(API + f"/tables/{t['id']}/records", json={"data": {F["Name"]: "B", mail["id"]: ["b@x.org", "SHARED@x.org"]}})
    assert r.status_code == 400 and "unique" in r.text


IPHONE = ("BEGIN:VCARD\r\nVERSION:3.0\r\nPRODID:-//Apple Inc.//iPhone OS 18.0//EN\r\nUID:{uid}\r\nN:Rivera;Sam;;;\r\nFN:Sam Rivera\r\n"
          "item1.TEL;type=pref:+1 (617) 555-0100\r\nitem1.X-ABLabel:_$!<Mobile>!$_\r\nTEL;type=WORK;type=VOICE:(617) 555-0199\r\n"
          "item2.TEL:617 555 0123\r\nitem2.X-ABLabel:Site trailer\r\nEMAIL;type=INTERNET;type=WORK;type=pref:sam@rivera.build\r\n"
          "EMAIL;type=INTERNET;type=HOME:sam@home.net\r\nEND:VCARD\r\n")


def test_a_phones_contact_keeps_every_number_both_ways(user):
    t = user.table("contacts", "Phone contacts")
    F = user.fields(t["id"])
    url = t["calendar_url"].rstrip("/") + "/sam.vcf"
    r = requests.put(url, data=IPHONE.format(uid="sam-1"), auth=user.dav_auth(), headers={"Content-Type": "text/vcard"})
    assert r.status_code in (201, 204), r.text
    rec = next(x for x in user.records(t["id"]) if x["data"][F["fn"]] == "Sam Rivera")
    assert rec["data"][F["phone"]] == [{"value": "+16175550100", "label": "mobile"}, {"value": "6175550199", "label": "work"},
                                       {"value": "6175550123", "label": "Site trailer"}]
    assert [x["value"] for x in rec["data"][F["email"]]] == ["sam@rivera.build", "sam@home.net"]
    # one more number added here: the phone gets all four, with their labels
    user.j("PATCH", f"/records/{rec['id']}", json={"data": {F["phone"]: rec["data"][F["phone"]] + [{"value": "6175550111", "label": "home"}]}})
    card = requests.get(url, auth=user.dav_auth()).text
    assert card.count("TEL") >= 4 and "X-ABLabel:Site trailer" in card and "TYPE=VOICE,HOME:6175550111" in card.replace(";", ",", 0)
    assert "sam@home.net" in card                                                  # what wasn't changed is as it was


def test_imports_read_several_values(user):
    t = user.table(name="Imported")
    F = user.fields(t["id"])
    mail = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Emails", "type": "email", "options": {"multi": "labeled"}})
    user.j("POST", f"/tables/{t['id']}/import", json={"columns": [{"target": F["Name"]}, {"target": mail["id"]}],
                                                       "rows": [["Ada", "work: ada@x.org; home: ada@home.org"]]})
    rec = next(x for x in user.records(t["id"]) if x["data"].get(F["Name"]) == "Ada")
    assert rec["data"][mail["id"]] == [{"value": "ada@x.org", "label": "work"}, {"value": "ada@home.org", "label": "home"}]
