#!/usr/bin/env bash
# Run the test suite against a throwaway copy of the stack (own database and Radicale; never your real data).
#   scripts/test.sh            all tests
#   scripts/test.sh api        backend tests only
#   scripts/test.sh -k sync    any pytest arguments
#   scripts/test.sh tests/api/test_fields.py   one file (or test id)
#   scripts/test.sh bench      timings at 1k/10k/50k rows (tests/bench/bench.py)
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_PROJECT_NAME=probyte-test PROBYTE_PORT=${TEST_PROBYTE_PORT:-18080} RADICALE_PORT=${TEST_RADICALE_PORT:-15232}
export POSTGRES_PASSWORD=probyte-test WEBHOOK_ALLOW_PRIVATE=true REMOTE_ALLOW_PRIVATE=true ALLOW_SIGNUP=true PUBLIC_URL= RADICALE_PUBLIC_URL= MCP_BEARER_TOKEN=
PLAYWRIGHT_IMAGE=mcr.microsoft.com/playwright/python:v1.48.0-jammy

cleanup() { docker cp probyte-test-runner:/work/tests/.artifacts tests/ >/dev/null 2>&1 || true  # screenshots of failed UI tests
  docker rm -f probyte-test-runner >/dev/null 2>&1 || true; [ "${KEEP_STACK:-}" ] || docker compose down -v >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker compose up -d --build --wait >/dev/null 2>&1
for i in $(seq 60); do docker compose exec -T app python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/auth/config')" >/dev/null 2>&1 && break; sleep 1; done

target=tests
cmd="python -m pytest -q -p no:cacheprovider --rootdir tests -c /dev/null"
if [ "${1:-}" = bench ]; then shift; cmd="pip install -q 'psycopg[binary]' 2>/dev/null; python tests/bench/bench.py"; target=""; fi
if [ "${1:-}" = api ] || [ "${1:-}" = ui ]; then target="tests/$1"; shift; fi
case "${1:-}" in tests/*) target="" ;; esac  # a file or test id given: run just that
args=""; [ $# -gt 0 ] && args=$(printf "%q " "$@")  # keep quoted pytest arguments (-k "a or b") intact
docker rm -f probyte-test-runner >/dev/null 2>&1 || true
docker create --name probyte-test-runner --network probyte-test_default -w /work \
  -e PROBYTE_URL=http://app:8000 -e RADICALE_URL=http://radicale:5232 "$PLAYWRIGHT_IMAGE" \
  bash -c "pip install -q pytest requests playwright==1.48.0 2>/dev/null && $cmd $target $args" >/dev/null
docker cp tests probyte-test-runner:/work/tests >/dev/null
docker start -a probyte-test-runner
