#!/usr/bin/env bash
# Checks the Docker Hub setup (deploy/docker-compose.yml) end to end with locally built images, on a throwaway stack:
# zero-config start, sign-up, the built-in calendar server inside the container (through Probyte's own address, as a
# phone does), a backup, the document converter, and a restart (the generated database password must stay valid).
#   scripts/test-deploy.sh            builds mpgithubcode/probyte:local etc. first
#   SKIP_BUILD=1 scripts/test-deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_PROJECT_NAME=probyte-deploytest PROBYTE_VERSION=local PROBYTE_PORT=${TEST_DEPLOY_PORT:-28080}
if [ -z "${SKIP_BUILD:-}" ]; then
  docker build -q -t mpgithubcode/probyte:local . >/dev/null
  docker build -q -t mpgithubcode/probyte-office:local ./office >/dev/null
fi
ROOT="$(pwd)"
cleanup() { (cd "$ROOT/deploy" && docker compose down -v >/dev/null 2>&1) || true; docker rm -f probyte-deploycheck >/dev/null 2>&1 || true; }
trap cleanup EXIT
cd deploy
docker compose up -d --pull never >/dev/null 2>&1
docker rm -f probyte-deploycheck >/dev/null 2>&1 || true
docker create --name probyte-deploycheck --network "${COMPOSE_PROJECT_NAME}_default" python:3.12-slim \
  sh -c "pip install -q requests >/dev/null 2>&1 && python /deploy_check.py" >/dev/null
docker cp ../scripts/deploy_check.py probyte-deploycheck:/deploy_check.py
docker start -a probyte-deploycheck
docker rm -f probyte-deploycheck >/dev/null
docker compose exec -T probyte python -c "import urllib.request; urllib.request.urlopen('http://office:3000/health')" && echo "converter ok"
docker compose restart probyte db >/dev/null 2>&1
for i in $(seq 60); do docker compose exec -T probyte python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/auth/config')" >/dev/null 2>&1 && break; sleep 2; done
docker compose exec -T probyte python -m app.admin backups && echo "admin command ok (after restart)"
! docker compose logs 2>&1 | grep -q Traceback || { docker compose logs 2>&1 | grep -B2 -A12 Traceback | head -60; exit 1; }
echo "all deploy checks passed"
