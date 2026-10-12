"""Probyte backend."""
import os

# The one-container setup (Docker Hub, deploy/docker-compose.yml): everything under DATA_DIR, the calendar server on
# 127.0.0.1, the worker inside the web app, the database at "db". Set here, so admin commands run with
# `docker compose exec probyte python -m app.admin …` see the same settings as the app (docker/entrypoint.sh too).
if os.environ.get("PROBYTE_ALL_IN_ONE", "").lower() == "true":
    _data = os.environ.get("DATA_DIR", "/data")
    for _k, _v in {
        "ATTACHMENTS_DIR": f"{_data}/attachments",
        "BACKUP_DIR": f"{_data}/backups",
        "SECRET_KEY_FILE": f"{_data}/keys/probyte.key",
        "RADICALE_HTPASSWD": f"{_data}/radicale/users",
        "RADICALE_DATA_DIR": f"{_data}/radicale/collections",
        "RADICALE_URL": "http://127.0.0.1:5232/",
        "RUN_WORKER": "embedded",
        "POSTGRES_HOST": "db",
    }.items():
        os.environ.setdefault(_k, _v)
