#!/bin/sh
# Probyte installer: a folder with the Docker Compose file and settings, then start it.
#
#   curl -fsSL https://raw.githubusercontent.com/mpgithubcode/Probyte/main/deploy/install.sh | sh
#
# Options (environment variables): PROBYTE_DIR (default ./probyte), PROBYTE_PORT (8080), PROBYTE_VERSION (latest).
# Needs Docker with the compose plugin. Run it again to upgrade (your data and settings are kept).
set -eu
REPO="https://raw.githubusercontent.com/mpgithubcode/Probyte/main/deploy"
DIR="${PROBYTE_DIR:-probyte}"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "Docker isn't installed: see https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "The Docker Compose plugin is missing: see https://docs.docker.com/compose/install/"
docker info >/dev/null 2>&1 || die "Can't talk to Docker (is it running? You may need sudo, or to be in the docker group)"
command -v curl >/dev/null 2>&1 || die "curl is needed"

mkdir -p "$DIR"
cd "$DIR"
say "Downloading Probyte's compose file into $(pwd)"
curl -fsSL "$REPO/docker-compose.yml" -o docker-compose.yml

if [ ! -f .env ]; then
  tz="${TZ:-}"
  [ -z "$tz" ] && [ -f /etc/timezone ] && tz="$(cat /etc/timezone)"
  [ -z "$tz" ] && command -v timedatectl >/dev/null 2>&1 && tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
  [ -z "$tz" ] && [ -L /etc/localtime ] && tz="$(readlink /etc/localtime | sed 's#.*/zoneinfo/##')"
  {
    echo "# Probyte settings (see https://github.com/mpgithubcode/Probyte/blob/main/deploy/.env.example)"
    echo "PROBYTE_PORT=${PROBYTE_PORT:-8080}"
    echo "TZ=${tz:-UTC}"
    [ -n "${PROBYTE_VERSION:-}" ] && echo "PROBYTE_VERSION=$PROBYTE_VERSION"
  } > .env
  say "Wrote .env (port ${PROBYTE_PORT:-8080}, time zone ${tz:-UTC})"
fi

say "Pulling the images (about 1.5 GB the first time)…"
docker compose pull -q
docker compose up -d

port="$(sed -n 's/^PROBYTE_PORT=//p' .env | tail -1)"; port="${port:-8080}"
printf 'Starting'
i=0
until curl -fs "http://localhost:$port/api/auth/config" >/dev/null 2>&1; do
  i=$((i + 1)); [ "$i" -gt 90 ] && { echo; die "Probyte didn't start; see: cd $(pwd) && docker compose logs probyte"; }
  printf '.'; sleep 2
done
echo
ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
say "Probyte is running:"
echo "  http://localhost:$port"
[ -n "$ip" ] && echo "  http://$ip:$port"
echo
echo "Create an account there: the first one is the admin."
echo "Settings live in $(pwd)/.env; upgrade by running this installer again."
echo "Put it behind HTTPS (a reverse proxy) before using it from the internet or with phones."
