#!/bin/sh
set -eu

umask 077
root_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${CONTEXT_BACKUP_DIR:-"$root_dir/backups"}
retention_days=${CONTEXT_BACKUP_RETENTION_DAYS:-14}
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup="$backup_dir/context-$timestamp.dump"

mkdir -p "$backup_dir"
cd "$root_dir"
docker compose exec -T db pg_dump -U context -d context -Fc > "$backup"
docker compose exec -T db pg_restore --list < "$backup" > /dev/null
find "$backup_dir" -type f -name 'context-*.dump' -mtime "+$retention_days" -delete
echo "$backup"
