#!/usr/bin/env bash
set -euo pipefail
umask 077

root=/srv/yihe-erp
env_file="$root/env/test.env"
export ERP_TEST_ENV_FILE="$env_file"
[[ "$(docker compose --env-file "$env_file" -f "$root/app/infra/compose.test.yaml" config --format json | jq -er '.services.web.environment.ERP_ENV')" == test ]]
install -d -m 0700 "$root/backups"
cd "$root/backups"
exec 8>backup.lock
flock 8
stamp=$(date -u +%Y%m%dT%H%M%SZ)
archive="erp-test-$stamp.dump.gz"
temporary=$(mktemp)
trap 'rm -f -- "$temporary"' EXIT
docker compose --env-file "$env_file" -f "$root/app/infra/compose.test.yaml" exec -T postgres \
  sh -ec 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -Z0' \
  | gzip > "$temporary"
gzip -t "$temporary"
[[ ! -e "$archive" ]]
mv -- "$temporary" "$archive"
sha256sum "$archive" > "$archive.sha256"
printf '%s\n' "$archive"
