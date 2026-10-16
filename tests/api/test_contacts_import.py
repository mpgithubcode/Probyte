"""Contacts tables: import vCard files (.vcf) and download all contacts as one."""
from conftest import API

V3 = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:card-ada\r\nFN:Ada Lovelace\r\nN:Lovelace;Ada;;;\r\nORG:Analytical Engines\r\n"
    "EMAIL;TYPE=INTERNET:ada@example.com\r\nTEL;TYPE=CELL:+44 20 7946 0000\r\nCATEGORIES:Clients,VIP\r\n"
    "PHOTO;ENCODING=b;TYPE=PNG:iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==\r\n"
    "END:VCARD\r\n"
    "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:card-alan\r\nFN:Alan Turing\r\nEMAIL:alan@example.com\r\nBDAY:1912-06-23\r\nEND:VCARD\r\n"
)
# an old Android export: vCard 2.1, quoted-printable UTF-8, no UID
V21 = (
    "BEGIN:VCARD\r\nVERSION:2.1\r\nN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:M=C3=BCller;J=C3=BCrgen;;;\r\n"
    "FN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:J=C3=BCrgen M=C3=BCller\r\nTEL;CELL:+49 30 123456\r\n"
    "NOTE;ENCODING=QUOTED-PRINTABLE:First line=0D=0Asecond line, a long note that wraps on=\r\nto the next line\r\nEND:VCARD\r\n"
    "BEGIN:VCARD\r\nVERSION:2.1\r\nEND:VCARD\r\n"
)


def upload(user, t, *files, existing="skip"):
    r = user.post(API + f"/tables/{t['id']}/vcards", files=[("files", f) for f in files], data={"existing": existing})
    assert r.status_code == 200, r.text
    return r.json()


def test_import_vcards(user):
    t = user.table("contacts", "People")
    F = user.fields(t["id"])
    res = upload(user, t, ("contacts.vcf", V3.encode(), "text/vcard"), ("android.vcf", V21.encode(), "text/x-vcard"))
    assert res["created"] == 3 and res["error_count"] == 1, res                      # the empty card can't be read
    recs = {r["data"][F["fn"]]: r["data"] for r in user.records(t["id"])}
    ada, jurgen = recs["Ada Lovelace"], recs["Jürgen Müller"]
    assert ada[F["email"]] == [{"value": "ada@example.com"}] and ada[F["org"]] == "Analytical Engines" and set(ada[F["categories"]]) == {"Clients", "VIP"}
    assert recs["Alan Turing"][F["birthday"]] == "1912-06-23"
    assert jurgen[F["last"]] == "Müller" and jurgen[F["phone"]] == [{"value": "+4930123456", "label": "mobile"}]
    assert jurgen[F["note"]].startswith("First line") and "wraps onto the next" in jurgen[F["note"]]
    # the address book gets them, with what Probyte doesn't show (the photo)
    user.sync(t["id"])
    _, body = user.server_item(t["calendar_url"], "Ada Lovelace")
    assert "PHOTO" in body and "iVBORw0KGgo" in body and "EMAIL" in body
    assert any("Müller" in b for _, b in user.server_items(t["calendar_url"]))
    # the same contacts again: skipped (by UID, by email), or updated
    again = "BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Alan M. Turing\r\nEMAIL:ALAN@example.com\r\nTITLE:Mathematician\r\nEND:VCARD\r\n"
    res = upload(user, t, ("again.vcf", (V3 + again).encode(), "text/vcard"))
    assert res == {**res, "created": 0, "skipped": 3}
    res = upload(user, t, ("again.vcf", again.encode(), "text/vcard"), existing="update")
    assert res["updated"] == 1
    alan = next(r["data"] for r in user.records(t["id"]) if [x["value"].lower() for x in r["data"].get(F["email"]) or []] == ["alan@example.com"])
    assert alan[F["title"]] == "Mathematician"
    # download them all as one .vcf
    r = user.get(API + f"/tables/{t['id']}/vcards")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/vcard")
    assert r.text.count("BEGIN:VCARD") == 3 and "Jürgen" in r.text and "PHOTO" in r.text
    assert 'filename="People.vcf"' in r.headers["content-disposition"]


def test_vcard_import_errors(user):
    t = user.table(name="Not contacts")
    r = user.post(API + f"/tables/{t['id']}/vcards", files=[("files", ("a.vcf", V3.encode(), "text/vcard"))])
    assert r.status_code == 400 and "contacts tables" in r.text
    c = user.table("contacts", "Empty")
    r = user.post(API + f"/tables/{c['id']}/vcards", files=[("files", ("a.vcf", b"hello", "text/plain"))])
    assert r.status_code == 400 and "No contacts found" in r.text
