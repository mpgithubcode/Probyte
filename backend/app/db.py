import os
import secrets

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://probyte:probyte@localhost:5432/probyte")

# A sync holds two connections while it runs (its per-table lock, and its work), so the worker sizes this to its threads.
POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "10"))
pool = ConnectionPool(DATABASE_URL, kwargs={"row_factory": dict_row}, open=False, min_size=1, max_size=POOL_SIZE)


def init_db() -> None:
    pool.open(wait=True, timeout=60)
    from . import migrate
    with pool.connection() as conn:
        migrate.run(conn)
        conn.execute("INSERT INTO app_settings (key, value) VALUES ('secret', %s) ON CONFLICT DO NOTHING",
                     (secrets.token_hex(32),))


def app_secret() -> bytes:
    """Server-side secret (generated once, stored in the database) for signing share-access cookies."""
    global _secret
    if _secret is None:
        with pool.connection() as conn:
            _secret = bytes.fromhex(conn.execute("SELECT value FROM app_settings WHERE key = 'secret'").fetchone()["value"])
    return _secret


_secret: bytes | None = None


def connection():
    return pool.connection()


__all__ = ["pool", "init_db", "connection", "Jsonb"]
