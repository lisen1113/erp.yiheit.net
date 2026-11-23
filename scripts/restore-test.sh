#!/usr/bin/env bash
set -euo pipefail

root=/srv/yihe-erp
export ERP_TEST_ENV_FILE="$root/env/test.env"
compose=(docker compose --env-file "$ERP_TEST_ENV_FILE" -f "$root/app/infra/compose.test.yaml")
[[ "$("${compose[@]}" config --format json | jq -er '.services.api.environment.ERP_ENV')" == test ]]
archive=${1:?Provide the backup filename}
[[ "$archive" =~ ^erp-test-[0-9]{8}T[0-9]{6}Z\.dump\.gz$ ]]
cd "$root/backups"
sha256sum --check --status "$archive.sha256"

restore_db="yihe_erp_restore_$(date -u +%Y%m%d%H%M%S)"
"${compose[@]}" exec -T postgres sh -ec 'createdb -U "$POSTGRES_USER" "$1"' sh "$restore_db"
gzip -dc "$archive" | "${compose[@]}" exec -T postgres sh -ec \
  'pg_restore --exit-on-error -U "$POSTGRES_USER" -d "$1"' sh "$restore_db"
"${compose[@]}" run --rm --no-deps -e "POSTGRES_DB=$restore_db" api alembic current
"${compose[@]}" exec -T postgres sh -ec 'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1"' sh "$restore_db" <<'SQL'
SELECT 'company', count(*) FROM company
UNION ALL SELECT 'employee', count(*) FROM employee
UNION ALL SELECT 'app_user', count(*) FROM app_user
UNION ALL SELECT 'role', count(*) FROM role
UNION ALL SELECT 'permission', count(*) FROM permission
UNION ALL SELECT 'audit_log', count(*) FROM audit_log
UNION ALL SELECT 'security_event', count(*) FROM security_event
UNION ALL SELECT 'idempotency_record', count(*) FROM idempotency_record
UNION ALL SELECT 'file_asset', count(*) FROM file_asset
UNION ALL SELECT 'brand_asset', count(*) FROM brand_asset;
SQL
"${compose[@]}" run --rm --no-deps -T -e "POSTGRES_DB=$restore_db" api python - <<'PY'
from sqlalchemy import select

from yihe_api.auth.models import Employee, User
from yihe_api.db.models import Company
from yihe_api.db.session import DatabaseSettings, make_engine, make_session_factory
from yihe_api.dev_seed import COMPANY_ID, PEOPLE, seed_id

engine = make_engine(DatabaseSettings.from_environment())
with make_session_factory(engine)() as session:
    if session.get(Company, COMPANY_ID) is None:
        raise SystemExit("Restore is missing the fixed Seed company")
    for code, _, _ in PEOPLE:
        employee_id = seed_id("employee", code)
        user = session.scalar(
            select(User).where(User.id == seed_id("user", code), User.employee_id == employee_id)
        )
        if session.get(Employee, employee_id) is None or user is None:
            raise SystemExit(f"Restore is missing a fixed employee/user binding: {code}")
print("Fixed company and employee/user Seed UUIDs verified")
PY
printf 'Restored database retained for isolated API verification: %s\n' "$restore_db"
