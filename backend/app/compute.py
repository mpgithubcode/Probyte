"""Computed field values (formula, lookup, rollup, conditional lookup/rollup, auto number,
created/modified time & by), evaluated when records are read so they are never stale.

One Engine per request; it caches computed tables so lookups across linked tables are cheap.
"""
from datetime import datetime, timezone

from . import formula as F

STORED_TYPES = {"text", "long_text", "number", "checkbox", "single_select", "multi_select", "date", "url", "email",
                "rating", "link", "user", "attachment", "recurrence"}
COMPUTED_TYPES = {"formula", "lookup", "rollup", "conditional_lookup", "conditional_rollup", "autonumber", "record_id",
                  "created_time", "last_modified_time", "created_by", "last_modified_by", "button"}
FIELD_TYPES = STORED_TYPES | COMPUTED_TYPES
LIST_TYPES = {"multi_select", "link", "user", "attachment", "lookup", "conditional_lookup"}
FILTER_OPS = {"is", "is_not", "contains", "not_contains", "gt", "gte", "lt", "lte", "empty", "not_empty"}


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


class Engine:
    def __init__(self, conn):
        self.conn = conn
        self._fields: dict[str, list] = {}
        self._computed: dict[str, list] = {}
        self._raw: dict[str, list] = {}
        self._active: set[str] = set()
        self._asts: dict[str, object] = {}
        self._users: dict[str, str] | None = None
        self._titles: dict[str, dict] = {}
        self._index: dict[str, dict] = {}
        self._vt: dict[str, str] = {}
        self._tables: dict[str, dict | None] = {}

    def table(self, table_id) -> dict | None:
        tid = str(table_id)
        if tid not in self._tables:
            self._tables[tid] = self.conn.execute("SELECT id, kind, remote FROM tables WHERE id = %s", (tid,)).fetchone()
        return self._tables[tid]

    def is_remote(self, table_id) -> bool:
        t = self.table(table_id)
        return bool(t and t["kind"] == "remote")

    # ------------------------------------------------------------ metadata

    def fields(self, table_id) -> list:
        tid = str(table_id)
        if tid not in self._fields:
            self._fields[tid] = self.conn.execute(
                "SELECT * FROM fields WHERE table_id = %s ORDER BY position, name", (tid,)).fetchall()
        return self._fields[tid]

    def field(self, field_id):
        fid = str(field_id)
        for fs in self._fields.values():
            for f in fs:
                if str(f["id"]) == fid:
                    return f
        f = self.conn.execute("SELECT * FROM fields WHERE id = %s", (fid,)).fetchone() if fid else None
        if f:
            self.fields(f["table_id"])  # warm cache
        return f

    def users(self) -> dict:
        if self._users is None:
            self._users = {str(r["id"]): r["username"] for r in self.conn.execute("SELECT id, username FROM users").fetchall()}
        return self._users

    def titles(self, table_id) -> dict:
        """record id -> title (primary field text) for a table, read raw to avoid recursion."""
        tid = str(table_id)
        if tid not in self._titles and self.is_remote(tid):
            prim = next((f for f in self.fields(tid) if f["is_primary"]), None)
            self._titles[tid] = {str(r["id"]): str(r["data"].get(str(prim["id"])) or "Untitled") for r in self.records(tid)} if prim else {}
        if tid not in self._titles:
            prim = next((f for f in self.fields(tid) if f["is_primary"]), None)
            out = {}
            if prim:
                for r in self.conn.execute("SELECT id, data->>%s AS t FROM records WHERE table_id = %s AND NOT deleted",
                                           (str(prim["id"]), tid)).fetchall():
                    out[str(r["id"])] = r["t"] or "Untitled"
            self._titles[tid] = out
        return self._titles[tid]

    def inner(self, f, depth=0):
        """(type, options, field) of the values a lookup/rollup collects, following nested lookups."""
        o = f["options"]
        if f.get("system_key") == "remote":  # mirrored from a linked table: the source described it
            inn = o.get("_inner")
            return (inn["type"], inn.get("options") or {}, {"id": "__inner", "name": inn["name"], "type": inn["type"],
                    "options": inn.get("options") or {}, "table_id": f["table_id"], "system_key": "remote"}) if inn else None
        if f["type"] in ("lookup", "rollup"):
            link = self.field(o.get("linkFieldId"))
            if not link or link["type"] != "link" or str(link["table_id"]) != str(f["table_id"]):
                return None
        target = self.field(o.get("lookupFieldId"))
        if not target:
            return None
        if target["type"] in ("lookup", "conditional_lookup") and depth < 5:
            return self.inner(target, depth + 1)
        return target["type"], target["options"], target

    def value_type(self, f, depth=0) -> str:
        """number | text | boolean | date | array — how a field's values behave in formulas."""
        key = str(f["id"])
        if depth == 0 and key in self._vt:
            return self._vt[key]
        vt = self._value_type(f, depth)
        if depth == 0:
            self._vt[key] = vt
        return vt

    def _value_type(self, f, depth=0) -> str:
        t = f["type"]
        if f.get("system_key") == "remote" and t in ("formula", "rollup", "conditional_rollup"):
            return f["options"].get("resultType") or "any"
        if t in ("number", "rating", "autonumber"):
            return "number"
        if t in ("date", "created_time", "last_modified_time"):
            return "date"
        if t == "checkbox":
            return "boolean"
        if t in LIST_TYPES:
            return "array"
        if t == "formula":
            if depth > 8:
                return "any"
            try:
                ast = self.ast(f)
            except F.FormulaError:
                return "any"
            by_id = {str(x["id"]): x for x in self.fields(f["table_id"])}
            return F.infer(ast, lambda ref: self.value_type(by_id[ref], depth + 1) if ref in by_id else "any")
        if t in ("rollup", "conditional_rollup"):
            inn = self.inner(f)
            return F.rollup_type(f["options"].get("func", "countall"), self.value_type(inn[2]) if inn else "text") \
                if f["options"].get("func") in F.ROLLUPS else "any"
        return "text"

    def config_error(self, f) -> str | None:
        t, o = f["type"], f["options"]
        if f.get("system_key") == "remote":
            return None
        if t == "link":
            if not self.conn.execute("SELECT 1 FROM tables WHERE id = %s", (o.get("foreignTableId"),)).fetchone():
                return "The linked table was deleted"
        if t in ("lookup", "rollup", "conditional_lookup", "conditional_rollup"):
            if self.inner(f) is None:
                return "A field this depends on was deleted or changed"
            if t.endswith("rollup") and o.get("func") not in F.ROLLUPS:
                return "Choose a rollup function"
        if t == "formula":
            try:
                ast = self.ast(f)
                known = {str(x["id"]) for x in self.fields(f["table_id"])}
                missing = [r for r in F.field_refs(ast) if r not in known]
                if missing:
                    return "The formula refers to a field that was deleted"
            except F.FormulaError as e:
                return str(e)
        return None

    def describe(self, table_id) -> list:
        """Fields as sent to the browser: plus computed flag, inner type, result type, readable formula."""
        out = []
        names = {str(x["id"]): x["name"] for x in self.fields(table_id)}
        if self.is_remote(table_id):
            perms = ((self.table(table_id)["remote"] or {}).get("permissions") or {})
            for f in self.fields(table_id):
                o = {k: v for k, v in f["options"].items() if k not in ("_remote_id", "_readonly")}
                out.append({**f, "options": o, "computed": f["type"] in COMPUTED_TYPES,
                            "readonly": bool(f["options"].get("_readonly")) or not perms.get("edit")})
            return out
        for f in self.fields(table_id):
            d = dict(f)
            o = dict(f["options"])
            d["computed"] = f["type"] in COMPUTED_TYPES
            err = self.config_error(f)
            if err:
                o["_error"] = err
            if f["type"] in ("lookup", "rollup", "conditional_lookup", "conditional_rollup"):
                inn = self.inner(f)
                if inn:
                    t, opts, target = inn
                    o["_inner"] = {"type": t, "options": opts, "name": target["name"]}
            if f["type"] in ("formula", "rollup", "conditional_rollup"):
                o["resultType"] = self.value_type(f)
            if f["type"] == "formula" and o.get("expression"):
                try:
                    o["displayExpression"] = F.rewrite_refs(o["expression"], lambda r: names.get(r, r))
                except F.FormulaError:
                    o["displayExpression"] = o["expression"]
            d["options"] = o
            out.append(d)
        return out

    # ------------------------------------------------------------ records

    def raw(self, table_id) -> list:
        tid = str(table_id)
        if tid not in self._raw and self.is_remote(tid):
            from . import remote
            self._raw[tid] = remote.records(self.conn, self.table(tid))
        if tid not in self._raw:
            rows = self.conn.execute(
                "SELECT id, table_id, data, sync_status, created_at, updated_at, seq, created_by, updated_by "
                "FROM records WHERE table_id = %s AND NOT deleted ORDER BY created_at, id", (tid,)).fetchall()
            self._raw[tid] = [{**r, "data": dict(r["data"])} for r in rows]
        return self._raw[tid]

    def records(self, table_id) -> list:
        tid = str(table_id)
        if tid in self._computed:
            return self._computed[tid]
        if self.is_remote(tid):  # values arrive already computed by the source
            self._computed[tid] = self.raw(tid)
            return self._computed[tid]
        if tid in self._active:
            return self.raw(tid)  # circular lookup between tables: fall back to stored values
        self._active.add(tid)
        try:
            recs = [{**r, "data": dict(r["data"])} for r in self.raw(tid)]
            fields = self.fields(tid)
            computed = [f for f in fields if f["type"] in COMPUTED_TYPES]
            for r in recs:
                for f in computed:
                    r["data"].pop(str(f["id"]), None)  # never trust stale stored values
            self._drop_gone_links(fields, recs)
            for f in self._order(computed):
                self._compute_field(f, recs)
            self._events_done(tid, fields, recs)
            self._computed[tid] = recs
            return recs
        finally:
            self._active.discard(tid)

    def _events_done(self, tid, fields, recs):
        """An event's Completed box is ticked once it's over (events have no completion on the server)."""
        t = self.table(tid)
        if not t or t["kind"] not in ("events", "mixed"):
            return
        keys = {f["system_key"]: str(f["id"]) for f in fields if f.get("system_key")}
        if "done" not in keys:
            return
        now = datetime.now(timezone.utc)
        for r in recs:
            d = r["data"]
            if t["kind"] == "mixed" and d.get(keys.get("component")) != "Event":
                continue
            end = d.get(keys.get("dtend")) or d.get(keys.get("dtstart"))
            over = False
            if isinstance(end, str) and end:
                try:
                    e = F.parse_dt(end)
                    if len(end) == 10:  # an all-day event lasts until the end of that day
                        e = F.local(e).replace(hour=23, minute=59, second=59)
                    over = e < now
                except F.FormulaError:
                    pass
            d[keys["done"]] = over

    def _drop_gone_links(self, fields, recs):
        """Links into a linked table point at records its owner may have deleted or stopped sharing:
        don't show those (unless the source is just unreachable right now)."""
        for f in fields:
            ftid = f["options"].get("foreignTableId") if f["type"] == "link" else None
            if not ftid or not self.is_remote(ftid):
                continue
            valid = set(self.titles(ftid))
            if (self.table(ftid)["remote"] or {}).get("error"):
                continue
            fid = str(f["id"])
            for r in recs:
                if isinstance(r["data"].get(fid), list):
                    r["data"][fid] = [x for x in r["data"][fid] if str(x) in valid]

    def _deps(self, f) -> set:
        if f["type"] == "formula":
            try:
                return set(F.field_refs(self.ast(f)))
            except F.FormulaError:
                return set()
        if f["type"] in ("conditional_lookup", "conditional_rollup"):
            return {c.get("valueField") for c in f["options"].get("filters", []) if c.get("valueField")}
        return set()

    def _order(self, computed: list) -> list:
        by_id = {str(f["id"]): f for f in computed}
        out, state = [], {}

        def visit(fid):
            if state.get(fid) == 2:
                return
            if state.get(fid) == 1:
                return  # cycle: evaluated as-is, the formula will see missing values
            state[fid] = 1
            for d in self._deps(by_id[fid]):
                if d in by_id:
                    visit(d)
            state[fid] = 2
            out.append(by_id[fid])
        for fid in by_id:
            visit(fid)
        return out

    def ast(self, f):
        key = str(f["id"]) + ":" + (f["options"].get("expression") or "")
        if key not in self._asts:
            try:
                self._asts[key] = F.parse(f["options"].get("expression") or "")
            except F.FormulaError as e:
                self._asts[key] = e
        a = self._asts[key]
        if isinstance(a, F.FormulaError):
            raise a
        return a

    def index(self, table_id) -> dict:
        """record id -> computed record, built once per table."""
        tid = str(table_id)
        if tid not in self._index:
            self._index[tid] = {str(r["id"]): r for r in self.records(tid)}
        return self._index[tid]

    @staticmethod
    def _collect(recs, lookup_fid):
        vals = []
        for rec in recs:
            if rec is None:
                continue
            v = rec["data"].get(lookup_fid)
            if isinstance(v, list):
                vals.extend(v)
            elif v is not None and v != "":
                vals.append(v)
        return vals

    def shown(self, f, v):
        """Values as the user sees them: link -> titles, user -> names, attachment -> file names."""
        if f is None:
            return v
        if f["type"] == "link":
            titles = self.titles(f["options"].get("foreignTableId"))
            return [titles.get(str(x), "") for x in (v or [])]
        if f["type"] in ("user", "created_by", "last_modified_by"):
            users = self.users()
            ids = v if isinstance(v, list) else ([v] if v else [])
            return [users.get(str(x), "") for x in ids]
        if f["type"] == "attachment":
            return [a.get("name", "") for a in (v or []) if isinstance(a, dict)]
        return v

    def _norm(self, field, value, vt):
        """A value prepared once for comparisons: (blank, lowercase texts, number, datetime, is_list)."""
        shown = self.shown(field, value)
        blank = F.is_blank(shown)
        items = shown if isinstance(shown, list) else [shown]
        texts = [F.to_str(x).lower() for x in items if not F.is_blank(x)]
        num = dt = None
        if not blank:
            try:
                if vt == "date":
                    dt = F.parse_dt(shown)
                elif vt != "array":
                    num = F.to_num(shown)
            except F.FormulaError:
                pass
        return blank, texts, num, dt, isinstance(shown, list)

    @staticmethod
    def _test(op, left, right, vt) -> bool:
        lblank, ltexts, lnum, ldt, llist = left
        rblank, rtexts, rnum, rdt, _ = right
        if op == "empty":
            return lblank
        if op == "not_empty":
            return not lblank
        if op in ("gt", "gte", "lt", "lte"):
            a, b = (ldt, rdt) if vt == "date" else (lnum, rnum)
            if lblank or rblank or a is None or b is None:
                return False
            return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
        if vt == "date" and not llist:
            hit = ldt is not None and ldt == rdt
        elif op in ("contains", "not_contains"):
            hit = any(r in l for r in rtexts for l in ltexts)
        elif llist:
            hit = bool(set(ltexts) & set(rtexts))
        else:
            hit = ltexts == rtexts or (lblank and rblank)
        return hit if op in ("is", "contains") else not hit

    def _conditional_matches(self, f, recs):
        """For each record, the foreign records matching the field's conditions (in table order).

        Conditions against a fixed value filter the other table once; an "is" condition against a
        field of this record uses a hash index, so typical setups stay fast on large tables."""
        o = f["options"]
        ftid = o.get("foreignTableId")
        foreign = self.records(ftid)
        ffields = {str(x["id"]): x for x in self.fields(ftid)}
        conds = []
        for c in o.get("filters", []):
            cf = ffields.get(c.get("field"))
            if cf is None:
                continue
            vt = self.value_type(cf)
            left = [self._norm(cf, fr["data"].get(c["field"]), vt) for fr in foreign]
            conds.append((c, vt, left))
        static = [(c, vt, left, self._norm(None, c.get("value"), vt)) for c, vt, left in conds if not c.get("valueField")]
        dynamic = [(c, vt, left) for c, vt, left in conds if c.get("valueField")]
        base = [i for i in range(len(foreign)) if all(self._test(c.get("op", "is"), left[i], right, vt) for c, vt, left, right in static)]

        indexed = next((d for d in dynamic if d[0].get("op", "is") == "is" and d[1] != "date"), None)
        index = None
        if indexed:
            _, ivt, ileft = indexed
            index = {}
            for i in base:
                blank, texts, _, _, is_list = ileft[i]
                for key in (texts if is_list else [texts[0] if texts else ""]):
                    index.setdefault(key, []).append(i)
        rest = [d for d in dynamic if d is not indexed]
        out = []
        for r in recs:
            rights = {id(d): self._norm(self.field(d[0]["valueField"]), r["data"].get(d[0]["valueField"]), d[1]) for d in dynamic}
            if index is not None:
                rblank, rtexts, *_ = rights[id(indexed)]
                is_list = indexed[1] == "array"
                if is_list:
                    cand = sorted({i for t in rtexts for i in index.get(t, [])})
                elif rblank:
                    cand = index.get("", [])
                else:
                    cand = index.get(rtexts[0], []) if len(rtexts) == 1 else []
            else:
                cand = base
            out.append([foreign[i] for i in cand
                        if all(self._test(d[0].get("op", "is"), d[2][i], rights[id(d)], d[1]) for d in rest)])
        return out

    def _compute_field(self, f, recs):
        fid, t, o = str(f["id"]), f["type"], f["options"]
        if t == "button":
            return
        if t == "autonumber":
            for r in recs:
                r["data"][fid] = r["seq"]
            return
        if t == "record_id":
            for r in recs:
                r["data"][fid] = str(r["id"])
            return
        if t in ("created_time", "last_modified_time"):
            col = "created_at" if t == "created_time" else "updated_at"
            for r in recs:
                r["data"][fid] = _iso(r[col])
            return
        if t in ("created_by", "last_modified_by"):
            col = "created_by" if t == "created_by" else "updated_by"
            for r in recs:
                r["data"][fid] = str(r[col]) if r[col] else None
            return
        if self.config_error(f):
            for r in recs:
                r["data"][fid] = None
            return
        if t in ("lookup", "rollup"):
            link = self.field(o["linkFieldId"])
            by_id = self.index(link["options"]["foreignTableId"])
            inner_t = self.value_type(self.inner(f)[2])
            lfid, link_id = str(o["lookupFieldId"]), str(link["id"])
            for r in recs:
                vals = self._collect((by_id.get(str(x)) for x in r["data"].get(link_id) or []), lfid)
                r["data"][fid] = vals if t == "lookup" else F.rollup(o["func"], vals, inner_t)
            return
        if t in ("conditional_lookup", "conditional_rollup"):
            ffields = {str(x["id"]): x for x in self.fields(o.get("foreignTableId"))}
            inner_t = self.value_type(self.inner(f)[2])
            lfid = str(o["lookupFieldId"])
            for r, match in zip(recs, self._conditional_matches(f, recs)):
                if o.get("sortField") in ffields:
                    sf = ffields[o["sortField"]]
                    key = (lambda x: F.parse_dt(x["data"].get(o["sortField"])) or F.parse_dt("1900-01-01")) if self.value_type(sf) == "date" \
                        else (lambda x: (F.is_blank(x["data"].get(o["sortField"])), F.to_str(x["data"].get(o["sortField"]))))
                    match.sort(key=key, reverse=o.get("sortDir") == "desc")
                if o.get("limit"):
                    match = match[: int(o["limit"])]
                vals = self._collect(match, lfid)
                r["data"][fid] = vals if t == "conditional_lookup" else F.rollup(o["func"], vals, inner_t)
            return
        if t == "formula":
            ast = self.ast(f)
            fields = {str(x["id"]): x for x in self.fields(f["table_id"])}
            for r in recs:
                ctx = F.Context(lambda ref, r=r: self._formula_value(fields.get(ref), r),
                                record_id=str(r["id"]), auto_number=r["seq"],
                                created=r["created_at"], modified=r["updated_at"])
                try:
                    r["data"][fid] = F.to_json(F.evaluate(ast, ctx))
                except F.FormulaError as e:
                    r["data"][fid] = {"__error": str(e)}
                except RecursionError:
                    r["data"][fid] = {"__error": "Formula is too deeply nested"}

    def _formula_value(self, f, r):
        """A field's value as a formula sees it (dates parsed, links as titles, users as names)."""
        if f is None:
            raise F.FormulaError("Unknown field")
        v = r["data"].get(str(f["id"]))
        t = f["type"]
        if isinstance(v, dict) and "__error" in v:
            return v
        if t == "link":
            titles = self.titles(f["options"].get("foreignTableId"))
            return [titles.get(str(x), "") for x in (v or [])]
        if t in ("user", "created_by", "last_modified_by"):
            users = self.users()
            ids = v if isinstance(v, list) else ([v] if v else [])
            names = [users.get(str(x), "") for x in ids]
            return names if t == "user" else (names[0] if names else None)
        if t == "attachment":
            return [a.get("name", "") for a in (v or []) if isinstance(a, dict)]
        if self.value_type(f) == "date" and isinstance(v, str) and v:
            return F.parse_dt(v).date() if len(v) == 10 else F.parse_dt(v)
        return v

    # ------------------------------------------------------------ references for display

    def refs(self, table_id, records) -> dict:
        """Titles of linked records and usernames that the given records refer to."""
        if self.is_remote(table_id):
            from . import remote
            return remote.refs(self.conn, self.table(table_id))
        rec_titles, user_ids = {}, set()
        for f in self.fields(table_id):
            fid, t = str(f["id"]), f["type"]
            link_table = None
            if t == "link":
                link_table = f["options"].get("foreignTableId")
            elif t in ("lookup", "conditional_lookup"):
                inn = self.inner(f)
                if inn and inn[0] == "link":
                    link_table = inn[1].get("foreignTableId")
                elif inn and inn[0] in ("user", "created_by", "last_modified_by"):
                    t = "user"
            if link_table:
                titles = self.titles(link_table)
                for r in records:
                    for x in r["data"].get(fid) or []:
                        rec_titles[str(x)] = titles.get(str(x), "Untitled")
            if t in ("user", "created_by", "last_modified_by"):
                for r in records:
                    v = r["data"].get(fid)
                    user_ids.update(str(x) for x in (v if isinstance(v, list) else [v]) if x)
        users = self.users()
        return {"records": rec_titles, "users": {u: users[u] for u in user_ids if u in users}}
