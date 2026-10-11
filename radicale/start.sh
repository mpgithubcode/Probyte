#!/bin/sh
# Probyte writes the users file (read-only here). On a fresh install, wait for it rather than failing to start.
for i in $(seq 120); do [ -f /radicale-users/users ] && break; sleep 1; done
exec docker-entrypoint.sh "$@"
