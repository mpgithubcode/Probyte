"""The trash: deleted records and tables come back for 30 days; synced ones go back to the server."""
import requests

from conftest import API


def test_records_restore_with_their_links(user):
    a, b = user.table(name="Orders"), user.table(name="Items")
    A, B = user.fields(a["id"]), user.fields(b["id"])
    link = user.j("POST", f"/tables/{a['id']}/fields", json={"name": "Items", "type": "link", "options": {"foreignTableId": b["id"]}})
    item = user.j("POST", f"/tables/{b['id']}/records", json={"data": {B["Name"]: "Pen"}})
    order = user.j("POST", f"/tables/{a['id']}/records", json={"data": {A["Name"]: "#1", A["Notes"]: "keep me", link["id"]: [item["id"]]}})
    user.j("DELETE", f"/records/{order['id']}")
    assert user.records(a["id"]) == []
    tr = user.j("GET", "/trash")
    assert [r["id"] for r in tr["records"]] == [order["id"]] and tr["records"][0]["title"] == "#1"
    assert user.j("POST", "/trash/records/restore", json={"ids": [order["id"]]})["restored"] == [order["id"]]
    back = user.record(a["id"], order["id"])["data"]
    assert back[A["Notes"]] == "keep me" and back[link["id"]] == [item["id"]]
    assert user.record(b["id"], item["id"])["data"][link["options"]["symmetricFieldId"]] == [order["id"]]   # the other side too
    assert user.j("GET", "/trash")["records"] == []


def test_bulk_delete_and_undo(user, other_user):
    t = user.table(name="Bulk")
    F = user.fields(t["id"])
    ids = [user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: n}})["id"] for n in "abc"]
    user.j("POST", "/records/bulk", json={"action": "delete", "items": [{"id": i, "table_id": t["id"]} for i in ids]})
    assert other_user.j("POST", "/trash/records/restore", json={"ids": ids})["restored"] == []     # not theirs
    assert sorted(user.j("POST", "/trash/records/restore", json={"ids": ids})["restored"]) == sorted(ids)
    assert len(user.records(t["id"])) == 3
    user.j("DELETE", f"/records/{ids[0]}")
    assert user.j("POST", "/trash/records/delete", json={"ids": [ids[0]]})["deleted"] == 1        # emptied for good
    assert user.j("POST", "/trash/records/restore", json={"ids": [ids[0]]})["restored"] == []


def test_synced_record_goes_back_to_the_server(user):
    t = user.table("tasks", "Chores")
    F = user.fields(t["id"])
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Hoover"}})
    user.sync(t["id"])
    user.j("DELETE", f"/records/{r['id']}")
    user.sync(t["id"])
    assert "Hoover" not in " ".join(b for _, b in user.server_items(t["calendar_url"]))
    user.j("POST", "/trash/records/restore", json={"ids": [r["id"]]})
    user.sync(t["id"])
    assert "SUMMARY:Hoover" in user.server_item(t["calendar_url"], "Hoover")[1]


def test_tables_go_to_the_trash_and_come_back(user):
    t = user.table("tasks", "Errands")
    F = user.fields(t["id"])
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["summary"]: "Post letter"}})
    user.sync(t["id"])
    res = user.j("DELETE", f"/tables/{t['id']}")
    assert res["trashed"] is True
    assert user.get(API + f"/tables/{t['id']}").status_code == 404
    assert all(x["id"] != t["id"] for x in user.workspace()["tables"])
    # in the trash, the calendar is gone from phones too; restored, it's back
    assert requests.request("PROPFIND", t["calendar_url"], auth=user.dav_auth(), headers={"Depth": "0"}).status_code == 404
    tr = user.j("GET", "/trash")["tables"]
    assert tr[0]["id"] == t["id"] and tr[0]["records"] == 1 and tr[0]["trash_delete_on_server"] is True
    user.j("POST", f"/trash/tables/{t['id']}/restore")
    assert [r["data"][F["summary"]] for r in user.records(t["id"])] == ["Post letter"]
    assert requests.request("PROPFIND", t["calendar_url"], auth=user.dav_auth(), headers={"Depth": "0"}).status_code == 207
    # deleting for good from the trash removes the calendar too
    user.j("DELETE", f"/tables/{t['id']}")
    assert user.j("DELETE", f"/trash/tables/{t['id']}")["server"] == "deleted the calendar"
    assert requests.request("PROPFIND", t["calendar_url"], auth=user.dav_auth(), headers={"Depth": "0"}).status_code == 404
    assert user.j("GET", "/trash")["tables"] == []


def test_change_feed_sees_a_restored_record_as_present(user):
    t = user.table(name="Feed trash")
    F = user.fields(t["id"])
    r = user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "x"}})
    tok = user.j("GET", f"/tables/{t['id']}")["token"]
    user.j("DELETE", f"/records/{r['id']}")
    user.j("POST", "/trash/records/restore", json={"ids": [r["id"]]})
    ch = user.j("GET", f"/tables/{t['id']}/changes", params={"since": tok})
    assert r["id"] in {x["id"] for x in ch["records"]} and r["id"] not in ch["deleted"]
