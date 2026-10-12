#!/bin/sh
# Probyte's container entrypoint.
#
# PROBYTE_ALL_IN_ONE=true (the Docker Hub setup, deploy/docker-compose.yml): one container runs the web app, the
# background worker and the calendar / contacts server (Radicale, on 127.0.0.1; phones reach it through Probyte), with
# everything kept under DATA_DIR (/data): attachments, backups, the encryption key, calendars and contacts.
# Otherwise (the from-source docker-compose.yml) it just runs the command: app, worker and Radicale are separate there.
set -e
if [ "${PROBYTE_ALL_IN_ONE:-}" = "true" ]; then
  DATA_DIR="${DATA_DIR:-/data}"
  export ATTACHMENTS_DIR="${ATTACHMENTS_DIR:-$DATA_DIR/attachments}"
  export BACKUP_DIR="${BACKUP_DIR:-$DATA_DIR/backups}"
  export SECRET_KEY_FILE="${SECRET_KEY_FILE:-$DATA_DIR/keys/probyte.key}"
  export RADICALE_HTPASSWD="${RADICALE_HTPASSWD:-$DATA_DIR/radicale/users}"
  export RADICALE_DATA_DIR="${RADICALE_DATA_DIR:-$DATA_DIR/radicale/collections}"
  export RADICALE_URL="${RADICALE_URL:-http://127.0.0.1:5232/}"
  export RUN_WORKER="${RUN_WORKER:-embedded}"
  export POSTGRES_HOST="${POSTGRES_HOST:-db}"
  mkdir -p "$ATTACHMENTS_DIR" "$BACKUP_DIR" "$(dirname "$SECRET_KEY_FILE")" "$RADICALE_DATA_DIR"
  touch "$RADICALE_HTPASSWD"
  # Radicale: restarted if it ever stops; Probyte writes its users file on start
  (
    while true; do
      python -m radicale --config /app/docker/radicale.conf \
        --storage-filesystem-folder "$RADICALE_DATA_DIR" --auth-htpasswd-filename "$RADICALE_HTPASSWD" || true
      echo "radicale stopped; restarting in 3 seconds" >&2
      sleep 3
    done
  ) &
fi
exec "$@"
