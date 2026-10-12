"""Encrypt the stored calendar-account passwords and webhook secrets (see secretbox.py)."""
from app.core.secretbox import PREFIX, seal


def run(conn):
    for table, column in (("caldav_accounts", "password"), ("webhooks", "secret")):
        for row in conn.execute(f"SELECT id, {column} AS v FROM {table} WHERE {column} IS NOT NULL AND {column} <> '' "
                                f"AND {column} NOT LIKE %s", (PREFIX + "%",)).fetchall():
            conn.execute(f"UPDATE {table} SET {column} = %s WHERE id = %s", (seal(row["v"]), row["id"]))
