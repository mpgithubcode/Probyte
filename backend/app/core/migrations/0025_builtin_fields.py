"""Every table gets the built-in Created time, Last modified time, Created by and Last modified by fields (hidden in
views until shown, and not deletable). New tables get them from data/schema.py (BUILTIN_FIELDS)."""

BUILTIN = [("created_time", "Created time", "created_time"), ("modified_time", "Last modified time", "last_modified_time"),
           ("created_by", "Created by", "created_by"), ("modified_by", "Last modified by", "last_modified_by")]


def run(conn):
    for t in conn.execute("SELECT id FROM tables WHERE kind <> 'remote'").fetchall():
        tid = t["id"]
        have = {r["system_key"] for r in conn.execute("SELECT system_key FROM fields WHERE table_id = %s AND system_key IS NOT NULL", (tid,)).fetchall()}
        names = {r["n"] for r in conn.execute("SELECT lower(name) AS n FROM fields WHERE table_id = %s", (tid,)).fetchall()}
        for key, name, ftype in BUILTIN:
            if key in have:
                continue
            out, n = name, 2
            while out.lower() in names:
                out, n = f"{name} {n}", n + 1
            names.add(out.lower())
            # before Record ID, which stays last
            pos = conn.execute("SELECT COALESCE(MAX(position) FILTER (WHERE type <> 'record_id'), -1) + 1 AS n FROM fields WHERE table_id = %s",
                               (tid,)).fetchone()["n"]
            conn.execute("UPDATE fields SET position = position + 1 WHERE table_id = %s AND position >= %s", (tid, pos))
            conn.execute("INSERT INTO fields (table_id, name, type, options, system_key, position) VALUES (%s, %s, %s, '{}', %s, %s)",
                         (tid, out, ftype, key, pos))
