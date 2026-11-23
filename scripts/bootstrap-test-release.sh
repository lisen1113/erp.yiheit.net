#!/usr/bin/env bash
set -euo pipefail
umask 077

sha=${1:?}
bootstrap=${2:?}
email=${3:-}
[[ $EUID == 0 && "$sha" =~ ^[a-f0-9]{40}$ && "$(cat DEPLOY_COMMIT)" == "$sha" ]]
[[ "$bootstrap" == true || "$bootstrap" == false ]]
if [[ "$bootstrap" == true ]]; then
  [[ "$email" =~ ^[a-zA-Z0-9._+%-]+@[a-zA-Z0-9.-]+$ ]]
fi
for cmd in curl docker flock jq nginx certbot openssl python3; do command -v "$cmd" >/dev/null; done
docker compose version
exec 9>/run/lock/yihe-erp-test-deploy.lock
flock -n 9
sha256sum --check --status images.tar.gz.sha256

root=/srv/yihe-erp
domain=erp.yiheit.net
site=/etc/nginx/sites-available/erp.yiheit.net.conf
link=/etc/nginx/sites-enabled/erp.yiheit.net.conf
webroot=$root/infra/acme
lineage=/etc/letsencrypt/live/$domain
hook=/etc/letsencrypt/renewal-hooks/deploy/erp-nginx-reload
env_file=$root/env/test.env
release=$root/releases/$sha
origin=https://$domain
probe=
backup=
site_created=false
hook_created=false
app_started=false
prior_release=
prior_env=
[[ ! -L "$root" && ! -L "$root/env" && ! -L "$root/releases" ]]
[[ ! -L "$root/infra" && ! -L "$webroot" && ! -L "$webroot/.well-known" ]]
[[ ! -L "$webroot/.well-known/acme-challenge" && ! -L "$env_file" ]]
[[ ! -e "$root/app" || -L "$root/app" ]]

fetch() {
  curl -q --noproxy '*' -fsS --connect-timeout 5 --max-time 20 "$@"
}
website() {
  local url result
  for url in http://yiheit.net/ https://yiheit.net/ http://www.yiheit.net/ https://www.yiheit.net/; do
    result=$(fetch -o /dev/null -w '%{http_code} %{redirect_url}|%{time_total}' "$url") || return 1
    printf '%s %s\n' "$url" "$result" >&2
    printf '%s %s\n' "$url" "${result%%|*}"
  done
}
baseline=$(website)
check_website() {
  local actual
  actual=$(website) && [[ "$actual" == "$baseline" ]]
}
reload() {
  local result
  result=$(nginx -t 2>&1) || { printf '%s\n' "$result" >&2; return 1; }
  printf '%s\n' "$result"
  [[ "$result" == *'syntax is ok'* && "$result" == *'test is successful'* && "$result" != *'conflicting server name'* ]] &&
    systemctl reload nginx && check_website
}
resources() {
  free -h
  swapon --show
  uptime
  df -hT / /srv /var/lib/docker
  [[ "$(df -PB1 /srv | awk 'NR==2 {print $4}')" -ge 20000000000 ]]
  [[ "$(df -PB1 /var/lib/docker | awk 'NR==2 {print $4}')" -ge 20000000000 ]]
  awk '/MemTotal:/ {total=$2} /MemAvailable:/ {available=$2} END {exit !(total && available / total >= .20)}' /proc/meminfo
}
snapshot() {
  tar -C /etc -cpf "$backup/$1.tar" nginx
}
rollback() {
  local result=$?
  trap - EXIT
  if [[ -n "$probe" ]]; then rm -f -- "$webroot/.well-known/acme-challenge/$probe"; fi
  if [[ "$result" == 0 ]]; then return; fi
  set +e
  if [[ "$hook_created" == true ]]; then mv -- "$hook" "$backup/erp-hook.disabled"; fi
  if [[ "$site_created" == true ]]; then
    snapshot rollback-before
    mv -- "$link" "$backup/erp-link.disabled" 2>/dev/null
    mv -- "$site" "$backup/erp-site.disabled" 2>/dev/null
    reload || printf 'ERP vhost rollback or website verification failed\n' >&2
  fi
  if [[ "$app_started" == true ]]; then
    if [[ -n "$prior_release" && -n "$prior_env" ]]; then
      install -m 0600 "$prior_env" "$env_file"
      ln -sfnT "$prior_release" "$root/app"
      docker compose --env-file "$env_file" -f "$root/app/infra/compose.test.yaml" \
        up -d --wait --pull never postgres redis api web worker
    else
      "${compose[@]}" stop api web worker redis postgres
    fi
  fi
  printf 'Deployment stopped. ERP certificates, database volumes and backups are retained.\n' >&2
  exit "$result"
}
trap rollback EXIT

resources
started_at=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
active=$(nginx -T 2>/dev/null)
grep -Eq '^[[:space:]]*include[[:space:]]+/etc/nginx/sites-enabled/\*[[:space:]]*;' <<<"$active"
if [[ "$bootstrap" == true ]]; then
  [[ ! -e "$site" && ! -L "$site" && ! -e "$link" && ! -L "$link" && ! -e "$hook" && ! -L "$hook" ]]
  [[ ! -e "$lineage" && ! -L "$lineage" && ! -e "/etc/letsencrypt/renewal/$domain.conf" ]]
  if grep -Fqi "$domain" <<<"$active"; then exit 1; fi
  if grep -riqF "$domain" /etc/nginx; then exit 1; else [[ $? == 1 ]]; fi
  [[ -z "$(ss -Hltn '( sport = :13000 or sport = :18000 )')" ]]
  [[ -z "$(docker ps -aq --filter label=com.docker.compose.project=yihe-erp-test)" ]]
  [[ -z "$(docker volume ls -q --filter label=com.docker.compose.project=yihe-erp-test)" ]]
  [[ -z "$(docker network ls -q --filter label=com.docker.compose.project=yihe-erp-test)" ]]
  [[ ! -e "$root/app" && ! -e "$env_file" ]]
else
  [[ "$(readlink "$link")" == "$site" && -f "$lineage/fullchain.pem" ]]
  [[ -L "$root/app" && -f "$env_file" && "$(stat -c %a "$env_file")" == 600 ]]
  prior_release=$(readlink "$root/app")
  [[ "$prior_release" =~ ^/srv/yihe-erp/releases/[a-f0-9]{40}$ ]]
  fetch "$origin/api/ready" >/dev/null
fi
unset active
systemctl is-active --quiet certbot.timer

install -d -m 0755 "$root" "$root/infra" "$root/releases" "$root/logs" "$root/volumes"
install -d -m 0700 "$root/env" "$root/backups"
backup=$(mktemp -d "$root/backups/deploy-$(date -u +%Y%m%dT%H%M%SZ).XXXXXXXX")
if [[ "$bootstrap" == true ]]; then
  snapshot http-before
  install -d -m 0755 "$webroot" "$webroot/.well-known" "$webroot/.well-known/acme-challenge"
  probe="t011-$(openssl rand -hex 16)"
  (set -C; umask 022; printf '%s\n' "$probe" > "$webroot/.well-known/acme-challenge/$probe")
  (set -C; umask 022; sed 's@location / .*@location / { add_header X-ERP-Stage acme-bootstrap always; return 503; }@' \
    infra/nginx/erp-test-http.conf > "$site")
  site_created=true
  ln -sT "$site" "$link"
  reload
  [[ "$(curl -q --noproxy '*' -sS --max-time 20 -o /dev/null -w '%{http_code}' "http://$domain/")" == 503 ]]
  [[ "$(fetch "http://$domain/.well-known/acme-challenge/$probe")" == "$probe" ]]
  certbot certonly --non-interactive --agree-tos --email "$email" --webroot \
    -w "$webroot" --cert-name "$domain" -d "$domain"
  openssl x509 -in "$lineage/fullchain.pem" -noout -ext subjectAltName -dates
  openssl x509 -in "$lineage/fullchain.pem" -noout -checkhost "$domain" |
    grep -Fx "Hostname $domain does match certificate"
  check_website
fi

if [[ ! -d "$release" ]]; then
  mkdir -m 0755 "$release"
  tar --exclude=./images.tar.gz --exclude=./images.tar.gz.sha256 -cf - . | tar -xf - -C "$release"
fi
[[ "$(cat "$release/DEPLOY_COMMIT")" == "$sha" ]]
docker load -i images.tar.gz
new_env=$(mktemp "$root/env/.test-release.XXXXXXXX")
if [[ "$bootstrap" == true ]]; then
  for image in docker.io/library/postgres:16-bookworm docker.io/library/redis:7-bookworm; do docker pull "$image"; done
  postgres=$(docker image inspect -f '{{ index .RepoDigests 0 }}' docker.io/library/postgres:16-bookworm)
  redis=$(docker image inspect -f '{{ index .RepoDigests 0 }}' docker.io/library/redis:7-bookworm)
  printf 'ERP_ENV=test\nPOSTGRES_DB=yihe_erp_seed_test\nPOSTGRES_USER=yihe_erp_test\n' > "$new_env"
  printf 'ERP_POSTGRES_IMAGE=%s\nERP_REDIS_IMAGE=%s\n' "$postgres" "$redis" >> "$new_env"
  printf 'POSTGRES_PASSWORD=%s\nERP_AUTH_JWT_KEY=%s\nERP_AUTH_MFA_KEY=%s\nERP_AUDIT_IDENTIFIER_KEY=%s\n' \
    "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" \
    "$(openssl rand -base64 32 | tr '+/' '-_')" "$(openssl rand -hex 32)" >> "$new_env"
  seed_password_file=$root/env/seed-password
  (set -C; openssl rand -hex 24 > "$seed_password_file")
else
  prior_env=$backup/test.env
  install -m 0600 "$env_file" "$prior_env"
  grep -vE '^ERP_(API|WEB|WORKER)_IMAGE=' "$env_file" > "$new_env"
fi
for service in api web worker; do
  image="yihe-erp-$service:$sha"
  docker image inspect "$image" >/dev/null
  printf 'ERP_%s_IMAGE=%s\n' "${service^^}" "$image" >> "$new_env"
done
export ERP_TEST_ENV_FILE=$env_file
mv -fT "$new_env" "$env_file"
ln -sfnT "$release" "$root/app"
compose=(docker compose --env-file "$env_file" -f "$root/app/infra/compose.test.yaml")
[[ "$("${compose[@]}" config --format json | jq -er '.services.api.environment.ERP_ENV')" == test ]]
app_started=true
"${compose[@]}" up -d --wait --pull never postgres redis
bash "$root/app/scripts/backup-test.sh"
"${compose[@]}" run --rm --no-deps api alembic heads | grep -Fx '0006_file_brand_assets (head)'
"${compose[@]}" run --rm --no-deps api alembic upgrade head
"${compose[@]}" run --rm --no-deps api alembic current | grep -Fx '0006_file_brand_assets (head)'
"${compose[@]}" up -d --wait --pull never api web worker
fetch http://127.0.0.1:18000/health
fetch http://127.0.0.1:18000/ready
fetch http://127.0.0.1:13000/ | grep -F 'TEST / 测试环境' >/dev/null

if [[ "$bootstrap" == true ]]; then
  snapshot https-before
  candidate=$(mktemp /etc/nginx/sites-available/.erp.XXXXXXXX)
  cat infra/nginx/erp-test-http.conf infra/nginx/erp-test-https.conf > "$candidate"
  chmod 0644 "$candidate"
  mv -fT "$candidate" "$site"
  reload
  [[ "$(fetch -o /dev/null -w '%{http_code} %{redirect_url}' "http://$domain/t011")" == "301 $origin/t011" ]]
fi
fetch "$origin/api/health"
fetch "$origin/api/ready"
fetch "$origin/" | grep -F 'TEST / 测试环境' >/dev/null
check_website

if [[ "$bootstrap" == true ]]; then
  install -d -m 0755 "$(dirname "$hook")"
  install -m 0755 infra/nginx/certbot-erp-reload.sh "$hook"
  hook_created=true
  for config in /etc/letsencrypt/cli.ini "/etc/letsencrypt/renewal/$domain.conf"; do
    if [[ -f "$config" ]] && grep -Eqi '^[[:space:]]*(pre[-_]hook|post[-_]hook)[[:space:]]*=' "$config"; then
      printf 'Unexpected Certbot pre/post hook in %s\n' "$config" >&2
      exit 1
    fi
  done
  certbot renew --cert-name "$domain" --dry-run --no-directory-hooks
  RENEWED_LINEAGE="$lineage" "$hook"
  check_website
fi

export ERP_SEED_DEFAULT_PASSWORD
ERP_SEED_DEFAULT_PASSWORD=$(cat "$root/env/seed-password")
for attempt in 1 2; do
  "${compose[@]}" run --rm --no-deps -e ERP_SEED_DEFAULT_PASSWORD api python -m yihe_api.cli seed-dev
done
unset ERP_SEED_DEFAULT_PASSWORD
"${compose[@]}" run --rm --no-deps -T \
  -e "T011_BOOTSTRAP_HTTPS=$bootstrap" -e T011_SEED_PASSWORD_FILE=/run/t011/password \
  -v "$root/env/seed-password:/run/t011/password:ro" --user 0 api \
  python - < "$root/app/scripts/smoke-test-https.py"
if [[ "$bootstrap" == true ]]; then
  resources
  bash "$root/app/scripts/smoke-test-storage.sh" "$sha"
  resources
fi
archive=$(bash "$root/app/scripts/backup-test.sh")
bash "$root/app/scripts/restore-test.sh" "$archive"
check_website
resources
docker ps --filter label=com.docker.compose.project=yihe-erp-test --format '{{.Names}} {{.Status}}'
if journalctl -k --since "$started_at" --no-pager | grep -Ei 'oom-kill|out of memory|killed process'; then exit 1; fi
printf '\nPublished test release %s; HTTPS, migration, seed and restore gates passed.\n' "$sha"
