"""Field types: creating each one, values, formulas, links, lookups/rollups, conditional fields, type changes."""
import pytest

from conftest import API


def add(user, tid, name, type, **options):
    return user.j("POST", f"/tables/{tid}/fields", json={"name": name, "type": type, "options": options})


def new(user, tid, data):
    return user.j("POST", f"/tables/{tid}/records", json={"data": data})


def rows(user, tid):
    return {r["id"]: r["data"] for r in user.records(tid)}


def test_every_stored_field_type_round_trips(user):
    t = user.table(name="Everything")
    F = user.fields(t["id"])
    ids = {
        "long_text": add(user, t["id"], "Long", "long_text")["id"],
        "number": add(user, t["id"], "Num", "number", precision=2)["id"],
        "checkbox": add(user, t["id"], "Check", "checkbox")["id"],
        "multi_select": add(user, t["id"], "Tags", "multi_select", choices=[{"name": "a"}, {"name": "b"}])["id"],
        "date": add(user, t["id"], "Day", "date")["id"],
        "datetime": add(user, t["id"], "When", "date", includeTime=True)["id"],
        "url": add(user, t["id"], "Site", "url")["id"],
        "email": add(user, t["id"], "Mail", "email")["id"],
        "rating": add(user, t["id"], "Stars", "rating", max=5)["id"],
        "user": add(user, t["id"], "Owner", "user")["id"],
        "recurrence": add(user, t["id"], "Repeat", "recurrence")["id"],
    }
    me = user.j("GET", "/auth/me")["id"]
    values = {F["Name"]: "Row", ids["long_text"]: "line 1\nline 2", ids["number"]: 3.25, ids["checkbox"]: True,
              ids["multi_select"]: ["a", "c"], ids["date"]: "2026-10-12", ids["datetime"]: "2026-10-12T09:30:00Z",
              ids["url"]: "https://example.com", ids["email"]: "a@b.co", ids["rating"]: 4, ids["user"]: [me],
              ids["recurrence"]: "FREQ=WEEKLY;BYDAY=MO"}
    r = new(user, t["id"], values)
    d = rows(user, t["id"])[r["id"]]
    for k, v in values.items():
        assert d[k] == v or (isinstance(v, str) and v in str(d[k])), (k, d[k], v)
    # a new multi-select option is registered on the field
    tags = next(f for f in user.j("GET", f"/tables/{t['id']}")["fields"] if f["id"] == ids["multi_select"])
    assert {"a", "b", "c"} <= {c["name"] for c in tags["options"]["choices"]}


def test_system_fields_and_autonumber(user):
    t = user.table(name="Sys")
    F = user.fields(t["id"])
    auto = add(user, t["id"], "No", "autonumber")["id"]
    ct = add(user, t["id"], "Created", "created_time")["id"]
    mt = add(user, t["id"], "Modified", "last_modified_time")["id"]
    cb = add(user, t["id"], "By", "created_by")["id"]
    a = new(user, t["id"], {F["Name"]: "a"})
    b = new(user, t["id"], {F["Name"]: "b"})
    d = rows(user, t["id"])
    me = user.j("GET", "/auth/me")["id"]
    assert (d[a["id"]][auto], d[b["id"]][auto]) == (1, 2)
    assert d[a["id"]][ct].endswith("Z") and d[a["id"]][mt] and d[a["id"]][cb] == me
    # computed fields can't be written
    r = user.patch(API + f"/records/{a['id']}", json={"data": {auto: 99}})
    assert r.status_code >= 400 or rows(user, t["id"])[a["id"]][auto] == 1


@pytest.mark.parametrize("expr,expected", [
    ("{Amount} * 2 + 1", 21),
    ("IF({Amount} > 5, \"big\", \"small\")", "big"),
    ("CONCATENATE({Name}, \"-\", {Amount})", "Widget-10"),
    ("ROUND({Amount} / 3, 2)", 3.33),
    ("UPPER(LEFT({Name}, 3))", "WID"),
    ("LEN({Name})", 6),
    ("DATETIME_FORMAT({Due}, \"YYYY/MM/DD\")", "2026/10/12"),
    ("DATETIME_DIFF(\"2026-10-20\", {Due}, \"day\")", 8),
    ("AND({Amount} > 1, {Name} = \"Widget\")", True),
    ("SWITCH({Amount}, 10, \"ten\", \"other\")", "ten"),
    ("YEAR({Due})", 2026),
])
def test_formulas(user, expr, expected):
    t = user.table(name="Formulas")
    F = user.fields(t["id"])
    amount = add(user, t["id"], "Amount", "number")["id"]
    due = add(user, t["id"], "Due", "date")["id"]
    chk = user.j("POST", f"/tables/{t['id']}/formula", json={"expression": expr})
    assert not chk.get("error"), chk
    f = add(user, t["id"], "Calc", "formula", expression=expr)["id"]
    r = new(user, t["id"], {F["Name"]: "Widget", amount: 10, due: "2026-10-12"})
    v = rows(user, t["id"])[r["id"]][f]
    assert v == expected, (expr, v)


def test_bad_formulas_are_rejected(user):
    t = user.table(name="Bad formulas")
    for expr in ("{Nope} + 1", "SUM(", "NOSUCHFUNC(1)"):
        chk = user.post(API + f"/tables/{t['id']}/formula", json={"expression": expr})
        assert chk.status_code >= 400 or chk.json().get("error"), expr
        assert user.post(API + f"/tables/{t['id']}/fields", json={"name": "X", "type": "formula", "options": {"expression": expr}}).status_code == 400


def test_links_are_two_way_and_lookups_rollups_follow(user):
    orders = user.table(name="Orders")
    items = user.table(name="Items")
    O, I = user.fields(orders["id"]), user.fields(items["id"])
    price = add(user, items["id"], "Price", "number")["id"]
    link = add(user, orders["id"], "Items", "link", foreignTableId=items["id"])
    back = link["options"]["symmetricFieldId"]
    assert back in user.fields(items["id"]).values()
    total = add(user, orders["id"], "Total", "rollup", linkFieldId=link["id"], lookupFieldId=price, func="sum")["id"]
    names = add(user, orders["id"], "Item names", "lookup", linkFieldId=link["id"], lookupFieldId=I["Name"])["id"]
    a = new(user, items["id"], {I["Name"]: "Pen", price: 2})
    b = new(user, items["id"], {I["Name"]: "Book", price: 15})
    o = new(user, orders["id"], {O["Name"]: "#1", link["id"]: [a["id"], b["id"]]})
    d = rows(user, orders["id"])[o["id"]]
    assert d[total] == 17 and sorted(d[names]) == ["Book", "Pen"]
    assert rows(user, items["id"])[a["id"]][back] == [o["id"]]          # the other side was linked too
    # unlinking from the other side updates this one
    user.j("PATCH", f"/records/{b['id']}", json={"data": {back: []}})
    assert rows(user, orders["id"])[o["id"]][link["id"]] == [a["id"]]
    assert rows(user, orders["id"])[o["id"]][total] == 2
    # deleting a linked record removes it from links
    user.j("DELETE", f"/records/{a['id']}")
    assert rows(user, orders["id"])[o["id"]][link["id"]] in ([], None)
    # links by title through the import / API path resolve
    titles = user.j("GET", f"/tables/{items['id']}/titles")
    assert b["id"] in str(titles)


def test_link_to_another_users_table_is_refused(user, other_user):
    mine = user.table(name="Mine")
    theirs = other_user.table(name="Theirs")
    r = user.post(API + f"/tables/{mine['id']}/fields", json={"name": "L", "type": "link", "options": {"foreignTableId": theirs["id"]}})
    assert r.status_code in (400, 404)


def test_conditional_rollup(user):
    people = user.table(name="People")
    tasks = user.table(name="Work")
    P, T = user.fields(people["id"]), user.fields(tasks["id"])
    hours = add(user, tasks["id"], "Hours", "number")["id"]
    who = add(user, tasks["id"], "Who", "text")["id"]
    f = add(user, people["id"], "Open hours", "conditional_rollup", foreignTableId=tasks["id"], lookupFieldId=hours, func="sum",
            filters=[{"field": who, "op": "is", "valueField": P["Name"]}, {"field": T["Status"], "op": "is_not", "value": "Done"}])["id"]
    ada = new(user, people["id"], {P["Name"]: "Ada"})
    for h, w, s in ((3, "Ada", "Todo"), (4, "Ada", "Done"), (5, "Bob", "Todo"), (1, "Ada", "Doing")):
        new(user, tasks["id"], {T["Name"]: "t", hours: h, who: w, T["Status"]: s})
    assert rows(user, people["id"])[ada["id"]][f] == 4


@pytest.mark.parametrize("old_type,old_value,new_type,expected", [
    ("text", "42", "number", 42),
    ("number", 7, "text", "7"),
    ("text", "yes", "checkbox", True),
    ("text", "a, b", "multi_select", ["a", "b"]),
    ("text", "2026-10-12", "date", "2026-10-12"),
    ("single_select", "Todo", "text", "Todo"),
    ("checkbox", True, "number", 1),
])
def test_field_type_changes_convert_values(user, old_type, old_value, new_type, expected):
    t = user.table(name="Convert")
    F = user.fields(t["id"])
    fid = F["Status"] if old_type == "single_select" else add(user, t["id"], "Col", old_type)["id"]
    r = new(user, t["id"], {F["Name"]: "x", fid: old_value})
    user.j("PATCH", f"/fields/{fid}", json={"type": new_type, "options": {}})
    assert rows(user, t["id"])[r["id"]].get(fid) == expected


def test_field_rename_delete_and_primary_rules(user):
    t = user.table(name="Rules")
    F = user.fields(t["id"])
    notes = F["Notes"]
    user.j("PATCH", f"/fields/{notes}", json={"name": "Comments"})
    assert "Comments" in user.fields(t["id"])
    f = add(user, t["id"], "Twice", "formula", expression="{Comments} & {Comments}")
    assert "Comments" in f["options"].get("displayExpression", "Comments")
    user.j("DELETE", f"/fields/{notes}")
    fields = {x["id"]: x for x in user.j("GET", f"/tables/{t['id']}")["fields"]}
    assert notes not in fields and fields[f["id"]]["options"].get("_error")   # the formula shows it's broken
    assert user.delete(API + f"/fields/{F['Name']}").status_code == 400            # primary field stays
    assert user.patch(API + f"/fields/{F['Name']}", json={"type": "checkbox"}).status_code == 400
    assert user.post(API + f"/tables/{t['id']}/fields", json={"name": "Z", "type": "nonsense"}).status_code == 400


def test_button_field_validation(user):
    t = user.table(name="Buttons")
    F = user.fields(t["id"])
    assert user.post(API + f"/tables/{t['id']}/fields", json={"name": "B", "type": "button", "options": {"action": "update"}}).status_code == 400
    assert user.post(API + f"/tables/{t['id']}/fields", json={"name": "B", "type": "button", "options": {"action": "url"}}).status_code == 400
    b = add(user, t["id"], "Finish", "button", action="update", updates=[{"fieldId": F["Status"], "value": "Done"}])
    assert b["options"]["updates"][0]["value"] == "Done"
