"""Number and date formats on every field that shows numbers or dates (number, formula, rollup, lookup, created time),
kept by the server and used for the API's text values."""
import requests

from conftest import BASE

V1 = BASE + "/api/v1"


def test_formats_on_numbers_formulas_rollups_and_lookups(user):
    t = user.table(name="Costs")
    F = user.fields(t["id"])
    est = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Estimated Costs", "type": "number",
                                                           "options": {"numberFormat": "currency", "currency": "$", "precision": 2}})
    tax = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "With tax", "type": "formula", "options": {
        "expression": "{Estimated Costs} * 1.0625", "numberFormat": "currency", "currency": "€", "precision": 2}})
    share = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Share", "type": "formula", "options": {
        "expression": "{Estimated Costs} / 10000", "numberFormat": "percent", "precision": 1}})
    when = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Created", "type": "created_time", "options": {"dateFormat": "iso", "timeFormat": "24"}})
    assert tax["options"]["numberFormat"] == "currency" and tax["options"]["currency"] == "€"     # kept on a formula
    assert when["options"]["dateFormat"] == "iso"
    user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: "Panel", est["id"]: 1234.5}})
    # a rollup and a lookup in another table, with their own formats
    jobs = user.table(name="Jobs")
    J = user.fields(jobs["id"])
    link = user.j("POST", f"/tables/{jobs['id']}/fields", json={"name": "Items", "type": "link", "options": {"foreignTableId": t["id"]}})
    total = user.j("POST", f"/tables/{jobs['id']}/fields", json={"name": "Total", "type": "rollup", "options": {
        "linkFieldId": link["id"], "lookupFieldId": est["id"], "func": "sum", "numberFormat": "currency", "currency": "£", "separators": False}})
    look = user.j("POST", f"/tables/{jobs['id']}/fields", json={"name": "Costs", "type": "lookup", "options": {
        "linkFieldId": link["id"], "lookupFieldId": est["id"], "numberFormat": "decimal", "precision": 0}})
    assert total["options"]["currency"] == "£" and look["options"]["precision"] == 0
    item = user.records(t["id"])[0]["id"]
    user.j("POST", f"/tables/{jobs['id']}/records", json={"data": {J["Name"]: "Mill St", link["id"]: [item]}})
    W = {"Authorization": "Bearer " + user.j("POST", "/keys", json={"name": "k", "scope": "read"})["key"]}
    rec = requests.get(f"{V1}/tables/Costs/records", headers=W, params={"cell_format": "text"}).json()["records"][0]["fields"]
    assert rec["Estimated Costs"] == "$1,234.50" and rec["With tax"] == "€1,311.66" and rec["Share"] == "12.3%"
    job = requests.get(f"{V1}/tables/Jobs/records", headers=W, params={"cell_format": "text"}).json()["records"][0]["fields"]
    assert job["Total"] == "£1234.50"
