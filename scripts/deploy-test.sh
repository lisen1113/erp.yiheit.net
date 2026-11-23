#!/usr/bin/env bash
set -euo pipefail

root=/srv/yihe-erp
env_file="$root/env/test.env"
export ERP_TEST_ENV_FILE="$env_file"
compose=(docker compose --env-file "$env_file" -f "$root/app/infra/compose.test.yaml")
exec 9>"$root/infra/deployment.lock"
flock -n 9
[[ "$(stat -c '%a' "$env_file")" == 600 ]]
[[ "$("${compose[@]}" config --format json | jq -er '.services.api.environment.ERP_ENV')" == test ]]
[[ "$("${compose[@]}" config --format json | jq -er '.services.web.environment.ERP_ENV')" == test ]]
[[ "$(df -P "$root" | awk 'NR == 2 {print $5+0}')" -lt 80 ]]
for image in $("${compose[@]}" config --images); do
  [[ "$image" =~ @sha256:[[:xdigit:]]{64}$ ]]
done

check_website() {
  for site in yiheit.net www.yiheit.net; do
    for scheme in http https; do
      curl --max-time 15 --fail --silent --show-error -o /dev/null \
        -w '%{http_code} %{time_total}\n' "$scheme://$site/"
    done
  done
}
check_website
"${compose[@]}" pull
"${compose[@]}" up -d --wait postgres redis
bash "$root/app/scripts/backup-test.sh"
"${compose[@]}" run --rm --no-deps api alembic current
"${compose[@]}" run --rm --no-deps api alembic upgrade head
"${compose[@]}" up -d --wait api web
curl --max-time 15 --fail --silent --show-error https://erp.yiheit.net/api/health
curl --max-time 15 --fail --silent --show-error https://erp.yiheit.net/api/ready
curl --max-time 15 --fail --silent --show-error https://erp.yiheit.net/ | grep -F 'TEST / 测试环境' >/dev/null
check_website
git -C "$root/app" rev-parse HEAD
