"""Changing a field's type keeps its data: every value is converted to the new type (never just dropped), with the
options the app sends (the old type's options carried along) and through the API / MCP too."""
import pytest

from conftest import API

VALUES = {
    "text": ["Opportunity", "Not in Scope", "Opportunity", "42", "2026-10-12", ""],
    "long_text": ["Line one\nline two", "Opportunity", "7"],
    "number": [3, 7.5, 0, -2],
    "checkbox": [True, False, True],
    "single_select": ["Todo", "Done", "Todo"],
    "multi_select": [["a", "b"], ["b"], ["c"]],
    "date": ["2026-10-12", "2026-01-31"],
    "url": ["https://example.com", "https://probyte.app/x"],
    "email": ["ada@example.com", "bob@example.com"],
    "phone": ["+1 555 0100", "555-0199"],
    "rating": [1, 4, 5],
}
TARGETS = ["text", "long_text", "number", "checkbox", "single_select", "multi_select", "date", "url", "email", "phone", "rating"]


def _make(user, old_type):
    t = user.table(name=f"From {old_type}")
    F = user.fields(t["id"])
    opts = {"choices": [{"name": n} for n in ("Todo", "Done", "a", "b", "c")]} if old_type in ("single_select", "multi_select") else {}
    f = user.j("POST", f"/tables/{t['id']}/fields", json={"name": "Col", "type": old_type, "options": opts})
    ids = []
    for i, v in enumerate(VALUES[old_type]):
        ids.append(user.j("POST", f"/tables/{t['id']}/records", json={"data": {F["Name"]: f"r{i}", f["id"]: v}})["id"])
    return t, f, ids


def _blank(v):
    return v is None or v is False or v == "" or v == []


def _filled(user, tid, fid):
    return {r["id"]: r["data"].get(fid) for r in user.records(tid)}


@pytest.mark.parametrize("old_type", list(VALUES))
@pytest.mark.parametrize("new_type", TARGETS)
def test_no_value_is_lost_when_a_type_changes(user, old_type, new_type):
    if old_type == new_type:
        return
    t, f, ids = _make(user, old_type)
    before = _filled(user, t["id"], f["id"])
    # as the field dialog sends it: the old options kept, the new type
    r = user.patch(API + f"/fields/{f['id']}", json={"name": "Col", "type": new_type, "options": f["options"], "description": None,
                                                    "required": False, "unique": False, "default_value": None})
    assert r.status_code == 200, r.text
    conv = r.json()["conversion"]
    after = _filled(user, t["id"], f["id"])
    unfit = [rid for rid in ids if not _blank(before[rid]) and _blank(after[rid]) and not (new_type == "checkbox" and after[rid] is False)]
    assert conv["kept"] == len(unfit), (conv, unfit)
    if unfit:  # changed back: the values that didn't fit are as they were
        user.j("PATCH", f"/fields/{f['id']}", json={"type": old_type, "options": f["options"]})
        back = _filled(user, t["id"], f["id"])
        for rid in unfit:
            assert back[rid] == before[rid], (old_type, new_type, before[rid], back[rid])
        user.j("PATCH", f"/fields/{f['id']}", json={"type": new_type, "options": f["options"]})
    field = next(x for x in user.j("GET", f"/tables/{t['id']}")["fields"] if x["id"] == f["id"])
    if new_type in ("single_select", "multi_select"):
        names = {c["name"] for c in field["options"]["choices"]}
        for v in after.values():
            for x in (v if isinstance(v, list) else [v] if v else []):
                assert x in names, (x, names)


def test_select_from_text_gets_every_value_as_an_option(user):
    t, f, ids = _make(user, "text")
    user.j("PATCH", f"/fields/{f['id']}", json={"type": "single_select", "options": {}})
    after = _filled(user, t["id"], f["id"])
    assert after[ids[0]] == "Opportunity" and after[ids[1]] == "Not in Scope"
    field = next(x for x in user.j("GET", f"/tables/{t['id']}")["fields"] if x["id"] == f["id"])
    assert [c["name"] for c in field["options"]["choices"]][:2] == ["Opportunity", "Not in Scope"]


def test_the_dialog_is_told_what_a_change_does_first(user):
    t, f, ids = _make(user, "text")
    p = user.j("POST", f"/fields/{f['id']}/conversion", json={"type": "number", "options": {}})
    assert p["total"] == 5 and p["converted"] == 1 and p["kept"] == 4
    assert "Opportunity" in p["unfit"] and p["examples"][0] == {"from": "42", "to": 42}
    assert _filled(user, t["id"], f["id"])[ids[0]] == "Opportunity"     # only a preview: nothing changed
    user.j("PATCH", f"/fields/{f['id']}", json={"type": "number", "options": {}})
    hist = user.j("GET", f"/records/{ids[0]}/history")
    assert any(h["changes"] and h["changes"][0]["old"] == "Opportunity" for h in hist["entries"])
    p = user.j("POST", f"/fields/{f['id']}/conversion", json={"type": "text", "options": {}})
    assert p["restored"] == 4
