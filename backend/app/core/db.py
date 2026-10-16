import os
import secrets

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

def _database_url() -> str:
    """DATABASE_URL, or one made from POSTGRES_HOST / POSTGRES_PASSWORD (or POSTGRES_PASSWORD_FILE: the Docker Hub setup
    generates the password on first start and shares it with the database through a small volume)."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    from urllib.parse import quote
    pw_file = os.environ.get("POSTGRES_PASSWORD_FILE", "")
    if pw_file and os.path.exists(pw_file):  # the generated one wins over a POSTGRES_PASSWORD left in a .env
        password = open(pw_file).read().strip()
    else:
        password = os.environ.get("POSTGRES_PASSWORD", "")
    user, name = os.environ.get("POSTGRES_USER", "probyte"), os.environ.get("POSTGRES_DB", "probyte")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    return f"postgresql://{quote(user)}:{quote(password or 'probyte')}@{host}:5432/{quote(name)}"


DATABASE_URL = _database_url()

# A sync holds two connections while it runs (its per-table lock, and its work), so the worker sizes this to its threads.
POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "10"))
# Durable over months of uptime: a connection is checked before it's handed out (after a database restart, a dead one
# is replaced instead of failing a request), connections are renewed hourly, and a transaction left idle (a request
# that died mid-way) is ended by the database after 30 minutes rather than holding its locks for ever.
IDLE_TX_SECONDS = int(os.environ.get("DB_IDLE_TRANSACTION_SECONDS", "1800"))
pool = ConnectionPool(DATABASE_URL, open=False, min_size=1, max_size=POOL_SIZE, check=ConnectionPool.check_connection,
                      max_lifetime=3600, max_idle=600, timeout=30,
                      kwargs={"row_factory": dict_row, "application_name": "probyte",
                              "options": f"-c idle_in_transaction_session_timeout={IDLE_TX_SECONDS * 1000}"})


def init_db() -> None:
    pool.open(wait=True, timeout=60)
    from app.core import migrate
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
