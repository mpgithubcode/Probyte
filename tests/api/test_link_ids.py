"""Links set through the API: a record ID identifies a record whatever its title; a title is used only when one record
has it (several: refused, asking for an ID)."""
import requests

from conftest import BASE

V1 = BASE + "/api/v1"


def test_links_by_id_with_duplicate_titles(user):
    W = {"Authorization": "Bearer " + user.j("POST", "/keys", json={"name": "k", "scope": "write"})["key"]}
    people = user.table(name="People")
    jobs = user.table(name="Jobs")
    P = user.fields(people["id"])
    link = user.j("POST", f"/tables/{jobs['id']}/fields", json={"name": "Contact", "type": "link", "options": {"foreignTableId": people["id"]}})
    greg = [user.j("POST", f"/tables/{people['id']}/records", json={"data": {P["Name"]: "Gregory Caswell", P["Notes"]: f"#{i}"}})["id"]
            for i in range(4)]
    solo = user.j("POST", f"/tables/{people['id']}/records", json={"data": {P["Name"]: "Ada Lovelace"}})["id"]
    url = f"{V1}/tables/Jobs/records"

    def link_to(value):
        return requests.post(url, headers=W, json={"fields": {"Name": "job", "Contact": value}})

    def check_each_id():
        for g in greg:
            r = link_to([g])
            assert r.status_code == 201, r.text
            assert r.json()["fields"]["Contact"] == [g]
        r = link_to(greg)                                                   # all four at once, in order
        assert r.json()["fields"]["Contact"] == greg

    check_each_id()
    # a title several records have: refused, asking for an ID; one only one record has: fine
    r = link_to(["Gregory Caswell"])
    assert r.status_code == 400 and "4 records" in r.json()["detail"] and "record ID" in r.json()["detail"]
    assert link_to("ada lovelace").json()["fields"]["Contact"] == [solo]
    assert link_to([solo, solo]).json()["fields"]["Contact"] == [solo]     # (once)
    assert link_to(["Nobody"]).status_code == 400
    # rename one, change another: every id still links
    user.j("PATCH", f"/records/{greg[1]}", json={"data": {P["Name"]: "Greg Caswell"}})
    user.j("PATCH", f"/records/{greg[2]}", json={"data": {P["Notes"]: "changed"}})
    check_each_id()
    r = link_to(["Gregory Caswell"])
    assert r.status_code == 400 and "3 records" in r.json()["detail"]
    assert link_to(["Greg Caswell"]).json()["fields"]["Contact"] == [greg[1]]
    # updating a record's link by id, too
    rec = link_to([greg[0]]).json()
    r = requests.patch(f"{url}/{rec['id']}", headers=W, json={"fields": {"Contact": [greg[3], greg[0]]}})
    assert r.status_code == 200 and r.json()["fields"]["Contact"] == [greg[3], greg[0]]
