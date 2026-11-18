#!/usr/bin/env bash
set -euo pipefail

project="t002-verify-$$"
env_file=$(mktemp)
empty_file=$(mktemp)
compose=(docker compose --project-name "$project" --env-file "$env_file")
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD ERP_DEV_PORT
port=18080

cleanup() {
  "${compose[@]}" down --volumes --remove-orphans
  rm -f "$env_file" "$empty_file"
}
trap cleanup EXIT

printf 'POSTGRES_DB=erp_verify\nPOSTGRES_USER=erp_verify\nPOSTGRES_PASSWORD=%s\nERP_DEV_PORT=%s\n' \
  "$(openssl rand -hex 24)" "$port" > "$env_file"

set -x
if result=$(docker compose --project-name "$project" --env-file "$empty_file" config --quiet 2>&1); then
  echo "Expected missing configuration to fail" >&2
  exit 1
fi
[[ "$result" == *"Set POSTGRES_DB in .env"* ]]
sed '/^POSTGRES_PASSWORD=/d' "$env_file" > "$empty_file"
if result=$(docker compose --project-name "$project" --env-file "$empty_file" config --quiet 2>&1); then
  echo "Expected missing password to fail" >&2
  exit 1
fi
[[ "$result" == *"Set POSTGRES_PASSWORD in .env"* ]]
echo "Missing database configuration and missing password both fail explicitly."

"${compose[@]}" up -d --build --wait --wait-timeout 120
"${compose[@]}" ps
for service in postgres redis api web nginx; do
  container=$("${compose[@]}" ps -q "$service")
  [[ "$(docker inspect --format '{{.State.Health.Status}}' "$container")" == healthy ]]
done
[[ "$("${compose[@]}" exec -T redis redis-cli ping | tr -d '\r')" == PONG ]]
curl -fsS "http://127.0.0.1:$port/api/health" | grep -Fx '{"status":"ok"}'
curl -fsS "http://127.0.0.1:$port/" | grep -o '上海以和 ERP' | sort -u

psql() {
  "${compose[@]}" exec -T postgres psql -U erp_verify -d erp_verify -v ON_ERROR_STOP=1 "$@"
}

# A synthetic large object exercises persistence without creating a business table/schema.
oid=$(psql -Atqc "SELECT lo_from_bytea(0, convert_to('t002-volume-probe', 'UTF8'))")
old_container=$("${compose[@]}" ps -q postgres)
"${compose[@]}" stop
"${compose[@]}" up -d --wait --wait-timeout 120
curl -fsS "http://127.0.0.1:$port/api/health" | grep -Fx '{"status":"ok"}'

"${compose[@]}" rm -sf postgres
"${compose[@]}" up -d --wait --wait-timeout 120
[[ "$("${compose[@]}" ps -q postgres)" != "$old_container" ]]
[[ "$(psql -Atqc "SELECT convert_from(lo_get($oid), 'UTF8')")" == t002-volume-probe ]]
psql -qc "SELECT lo_unlink($oid)"
"${compose[@]}" ps
echo "All T002 Compose checks passed."
