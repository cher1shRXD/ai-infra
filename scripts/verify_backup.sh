#!/bin/sh
set -eu

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "usage: $0 <backup.dump>" >&2
  exit 2
fi

root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
database="context_restore_test_$$"
cd "$root_dir"
cleanup() {
  docker compose exec -T db dropdb -U context --if-exists "$database" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker compose exec -T db createdb -U context "$database"
docker compose exec -T db pg_restore -U context -d "$database" --exit-on-error < "$1"
docker compose exec -T db psql -U context -d "$database" -Atc \
  "SELECT count(*) FROM schema_migration; SELECT count(*) FROM api_key;" > /dev/null
echo "backup restore verified"
