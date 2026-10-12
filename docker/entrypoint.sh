#!/bin/sh
# Probyte's container entrypoint.
#
# PROBYTE_ALL_IN_ONE=true (the Docker Hub setup, deploy/docker-compose.yml): one container runs the web app (with
# Probyte's own CalDAV/CardDAV server) and the background worker, with everything kept under DATA_DIR (/data):
# attachments, backups and the encryption key. Calendars and contacts are in the database.
# Otherwise (the from-source docker-compose.yml) it just runs the command: app and worker are separate there.
set -e
if [ "${PROBYTE_ALL_IN_ONE:-}" = "true" ]; then
  DATA_DIR="${DATA_DIR:-/data}"
  export ATTACHMENTS_DIR="${ATTACHMENTS_DIR:-$DATA_DIR/attachments}"
  export BACKUP_DIR="${BACKUP_DIR:-$DATA_DIR/backups}"
  export SECRET_KEY_FILE="${SECRET_KEY_FILE:-$DATA_DIR/keys/probyte.key}"
  export RADICALE_DATA_DIR="${RADICALE_DATA_DIR:-$DATA_DIR/radicale/collections}"   # calendars from before 0.9, brought in once
  export RUN_WORKER="${RUN_WORKER:-embedded}"
  export POSTGRES_HOST="${POSTGRES_HOST:-db}"
  mkdir -p "$ATTACHMENTS_DIR" "$BACKUP_DIR" "$(dirname "$SECRET_KEY_FILE")"
fi
exec "$@"
