#!/usr/bin/env bash
# T011 A-1. Run on the test server from OrcaTerm.
set -Eeuo pipefail

domain=erp.yiheit.net
site=/etc/nginx/sites-available/$domain.conf
link=/etc/nginx/sites-enabled/$domain.conf
challenge_dir=/srv/yihe-erp/infra/acme/.well-known/acme-challenge
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup=/root/t011-a1-nginx-$stamp
probe=t011-a1-$stamp
probe_file=$challenge_dir/$probe
probe_url=http://$domain/.well-known/acme-challenge/$probe
created_dirs=()
site_created=false
link_created=false
probe_created=false
reload_attempted=false

fail() {
  printf 'A-1 stopped: %s\n' "$*" >&2
  exit 1
}
require_absent() {
  if sudo -n test -e "$1" || sudo -n test -L "$1"; then
    fail "Target already exists: $1"
  fi
}
create_file() {
  sudo -n sh -c '
    set -eu
    umask 022
    temporary=$(mktemp -- "${1%/*}/.t011-a1.XXXXXXXX")
    trap '\''rm -f -- "$temporary"'\'' EXIT
    cat > "$temporary"
    chmod 0644 "$temporary"
    ln -T -- "$temporary" "$1"
  ' sh "$1"
}
request() {
  curl -q --noproxy '*' -sS --connect-timeout 5 --max-time 15 "$@"
}
website_snapshot() {
  local url result
  for url in http://yiheit.net/ https://yiheit.net/ http://www.yiheit.net/ https://www.yiheit.net/; do
    result=$(request -f -o /dev/null -w '%{http_code} %{redirect_url}|%{time_total}s' "$url") ||
      return 1
    printf '%s %s\n' "$url" "$result" >&2
    printf '%s %s\n' "$url" "${result%%|*}"
  done
}
check_website() {
  local after
  after=$(website_snapshot) && [[ "$after" == "$before" ]]
}
nginx_test() {
  local result
  if ! result=$(sudo -n nginx -t 2>&1); then
    printf '%s\n' "$result" >&2
    return 1
  fi
  printf '%s\n' "$result"
  [[ "$result" == *'syntax is ok'* && "$result" == *'test is successful'* &&
    "$result" != *'conflicting server name'* ]]
}
rollback() {
  local status=$? index
  trap - EXIT HUP INT TERM
  if (( status == 0 )); then return; fi
  set +e
  if [[ "$link_created" == false && "$site_created" == false &&
        "$probe_created" == false && ${#created_dirs[@]} == 0 ]]; then
    exit "$status"
  fi
  printf 'A-1 failed. Removing only the ERP paths created by this run.\n' >&2
  if [[ "$link_created" == true ]]; then
    if [[ "$(sudo -n readlink -- "$link")" != "$site" ]] || ! sudo -n rm -- "$link"; then
      printf 'ERP link changed or could not be removed. Rollback needs inspection.\n' >&2
      exit "$status"
    fi
  fi
  if [[ "$site_created" == true ]]; then sudo -n rm -- "$site"; fi
  if [[ "$probe_created" == true ]]; then sudo -n rm -- "$probe_file"; fi
  for ((index=${#created_dirs[@]}-1; index>=0; index--)); do
    sudo -n rmdir -- "${created_dirs[index]}" 2>/dev/null || true
  done
  if [[ "$reload_attempted" == true ]]; then
    if ! nginx_test || ! sudo -n systemctl reload nginx || ! check_website; then
      printf 'Rollback could not verify Nginx reload and website baseline.\n' >&2
      exit "$status"
    fi
  fi
  printf 'ERP rollback finished. Original Nginx backup retained at %s.\n' "$backup" >&2
  exit "$status"
}

sudo -n true
# Serialize concurrent runs on this host without creating a deployment lock file.
exec 9</etc/nginx
flock -n 9 || fail 'Another A-1 run holds the Nginx lock'

listeners=$(sudo -n ss -Hltn '( sport = :13000 or sport = :18000 )')
if [[ -n "$listeners" ]]; then
  printf '%s\n' "$listeners"
  fail 'TCP 13000 or 18000 is occupied'
fi
printf 'TCP 13000 and 18000 have no listeners.\n'
active=$(sudo -n nginx -T)
grep -Eq '^[[:space:]]*include[[:space:]]+/etc/nginx/sites-enabled/\*[[:space:]]*;' <<<"$active" ||
  fail 'sites-enabled include not confirmed'
if grep -Fqi "$domain" <<<"$active"; then fail 'ERP already exists in loaded configuration'; fi
unset active
if matches=$(sudo -n grep -rliF -- "$domain" /etc/nginx); then
  printf '%s\n' "$matches"
  fail 'ERP already appears under /etc/nginx'
else
  [[ $? == 1 ]] || fail 'Could not fully inspect /etc/nginx'
fi
for target in "$site" "$link" "$challenge_dir" "$backup"; do require_absent "$target"; done
for path in /srv /srv/yihe-erp /srv/yihe-erp/infra /srv/yihe-erp/infra/acme \
  /srv/yihe-erp/infra/acme/.well-known
do
  if sudo -n test -L "$path"; then fail "Unexpected symlink: $path"; fi
  if sudo -n test -e "$path" && ! sudo -n test -d "$path"; then
    fail "Not a directory: $path"
  fi
done

printf '\nWebsite before change\n'
before=$(website_snapshot) || fail 'Website preflight failed'
sudo -n mkdir -m 0700 -- "$backup"
sudo -n sh -c 'set -C; umask 077; tar -C /etc -cpf - nginx > "$1"' sh "$backup/nginx-before.tar"
sudo -n tar -tf "$backup/nginx-before.tar" >/dev/null

trap rollback EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
for path in /srv/yihe-erp /srv/yihe-erp/infra /srv/yihe-erp/infra/acme \
  /srv/yihe-erp/infra/acme/.well-known "$challenge_dir"
do
  if sudo -n test -L "$path"; then fail "Unexpected symlink: $path"; fi
  if sudo -n test -d "$path"; then
    [[ "$path" != "$challenge_dir" ]] || fail "Target already exists: $path"
  else
    require_absent "$path"
    sudo -n mkdir -m 0755 -- "$path"
    created_dirs+=("$path")
  fi
done

require_absent "$probe_file"
printf '%s\n' "$probe" | create_file "$probe_file"
probe_created=true
require_absent "$site"
create_file "$site" <<'NGINX'
server {
    listen 80;
    server_name erp.yiheit.net;

    location ^~ /.well-known/acme-challenge/ {
        root /srv/yihe-erp/infra/acme;
        default_type text/plain;
        try_files $uri =404;
    }

    location / {
        add_header X-ERP-Stage acme-bootstrap always;
        return 503;
    }
}
NGINX
site_created=true
require_absent "$link"
sudo -n ln -sT -- "$site" "$link"
link_created=true

nginx_test || fail 'Nginx configuration test failed. Reload skipped.'
reload_attempted=true
sudo -n systemctl reload nginx
printf '\nWebsite after reload\n'
check_website || fail 'Website response or redirect changed'

headers=$(request --dump-header - --output /dev/null "http://$domain/")
headers=${headers//$'\r'/}
grep -Eq '^HTTP/[^ ]+ 503([[:space:]]|$)' <<<"$headers" || fail 'ERP HTTP status is not 503'
grep -Eiq '^X-ERP-Stage:[[:space:]]*acme-bootstrap[[:space:]]*$' <<<"$headers" ||
  fail 'ERP bootstrap response header is missing'
probe_result=$(request -f -w $'\n%{http_code}' "$probe_url") || fail 'ACME probe request failed'
[[ "$probe_result" == "$probe"$'\n200' ]] || fail 'ACME probe content or status mismatched'

printf '\nA-1 passed. Nginx tested and reloaded. Website baseline unchanged.\n'
printf 'ERP HTTP 503: X-ERP-Stage: acme-bootstrap\n'
printf 'ACME challenge URL: %s\nExpected body: %s\nBackup: %s/nginx-before.tar\n' "$probe_url" "$probe" "$backup"
