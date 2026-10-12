"""Versioned database migrations.

Files in migrations/ run once each, in name order: NNNN_name.sql (plain SQL) or NNNN_name.py (a run(conn)
function). Applied versions are recorded in schema_migrations. An advisory lock lets the web and worker
processes start at the same time safely. Extensions (app/extensions/<id>/migrations) are migrated the same way.
"""
import importlib
import logging
from pathlib import Path

log = logging.getLogger("probyte.migrate")
MIGRATIONS = Path(__file__).parent / "migrations"


def run(conn) -> list[str]:
    conn.execute("SELECT pg_advisory_xact_lock(7711)")  # first: the app and the worker may start together
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations").fetchall()}
    applied = []
    # Probyte's own, then each extension's (recorded as "<extension>:<name>")
    from app.extensions import migration_dirs
    sources = [("", MIGRATIONS, "app.core.migrations")] + [(f"{ext}:", d, f"app.extensions.{ext}.migrations") for ext, d in migration_dirs()]
    for prefix, folder, package in sources:
        for path in sorted(folder.iterdir()):
            version = prefix + path.stem
            if path.suffix not in (".sql", ".py") or path.stem.startswith("_") or version in done:
                continue
            with conn.transaction():
                if path.suffix == ".sql":
                    conn.execute(path.read_text())
                else:
                    importlib.import_module(f"{package}.{path.stem}").run(conn)
                conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (version,))
            log.info("applied migration %s", version)
            applied.append(version)
    return applied
