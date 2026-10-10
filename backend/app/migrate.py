"""Versioned database migrations.

Files in migrations/ run once each, in name order: NNNN_name.sql (plain SQL) or NNNN_name.py (a run(conn)
function). Applied versions are recorded in schema_migrations. An advisory lock lets the web and worker
processes start at the same time safely.
"""
import importlib
import logging
from pathlib import Path

log = logging.getLogger("probyte.migrate")
MIGRATIONS = Path(__file__).parent / "migrations"


def run(conn) -> list[str]:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    conn.execute("SELECT pg_advisory_xact_lock(7711)")
    done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    applied = []
    for path in sorted(MIGRATIONS.iterdir()):
        if path.suffix not in (".sql", ".py") or path.stem.startswith("_") or path.stem in done:
            continue
        with conn.transaction():
            if path.suffix == ".sql":
                conn.execute(path.read_text())
            else:
                importlib.import_module(f"app.migrations.{path.stem}").run(conn)
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,))
        log.info("applied migration %s", path.stem)
        applied.append(path.stem)
    return applied
