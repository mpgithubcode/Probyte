"""Views saved in older formats get today's: a filter group instead of a flat list, a list of sorts instead of one,
and groups instead of a table view's single group-by (a board's groupBy, its columns, stays)."""
from psycopg.types.json import Jsonb


def modern(vtype: str, cfg: dict) -> dict:
    c = dict(cfg)
    if "filters" in c:
        flat = c.pop("filters") or []
        if not (isinstance(c.get("filter"), dict) and isinstance(c["filter"].get("items"), list)):
            c["filter"] = {"conjunction": "and", "items": [x for x in flat if isinstance(x, dict)]}
    if "sort" in c:
        one = c.pop("sort")
        if not isinstance(c.get("sorts"), list) and isinstance(one, dict) and one.get("field"):
            c["sorts"] = [one]
    if vtype != "kanban" and "groupBy" in c:
        by = c.pop("groupBy")
        if by and not isinstance(c.get("groups"), list):
            c["groups"] = [{"field": by, "dir": "asc"}]
    return c


def run(conn):
    for v in conn.execute("SELECT id, type, config FROM views").fetchall():
        new = modern(v["type"], v["config"] or {})
        if new != (v["config"] or {}):
            conn.execute("UPDATE views SET config = %s WHERE id = %s", (Jsonb(new), v["id"]))
