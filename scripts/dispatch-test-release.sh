#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${TEST_HOST:?Missing TEST_SSH_HOST}" "${TEST_PORT:?Missing TEST_SSH_PORT}"
: "${TEST_USER:?Missing TEST_SSH_USER}" "${TEST_KEY:?Missing TEST_SSH_PRIVATE_KEY}"
: "${TEST_KNOWN_HOSTS:?Missing TEST_SSH_KNOWN_HOSTS}" "${EXPECTED_SHA:?}"
[[ "$TEST_HOST" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]*$ ]]
[[ "$TEST_USER" =~ ^[a-z_][a-z0-9_-]*$ && "$TEST_PORT" =~ ^[0-9]{1,5}$ ]]
[[ "$EXPECTED_SHA" =~ ^[a-f0-9]{40}$ && "$(git rev-parse HEAD)" == "$EXPECTED_SHA" ]]
[[ "$BOOTSTRAP_HTTPS" == true || "$BOOTSTRAP_HTTPS" == false ]]
if [[ "$BOOTSTRAP_HTTPS" == true || -n "${ACME_EMAIL:-}" ]]; then
  [[ "${ACME_EMAIL:-}" =~ ^[a-zA-Z0-9._+%-]+@[a-zA-Z0-9.-]+$ ]]
fi

tmp=$(mktemp -d)
destination=
cleanup() {
  if [[ "$destination" =~ ^/tmp/yihe-erp-test\.[a-zA-Z0-9]+$ ]]; then
    "${remote[@]}" "rm -rf -- '$destination'" >/dev/null || true
  fi
  rm -rf -- "$tmp"
}
trap cleanup EXIT
printf '%s\n' "$TEST_KEY" > "$tmp/key"
printf '%s\n' "$TEST_KNOWN_HOSTS" > "$tmp/known_hosts"
unset TEST_KEY TEST_KNOWN_HOSTS
ssh_options=(-p "$TEST_PORT" -i "$tmp/key" -o BatchMode=yes -o ConnectTimeout=10
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$tmp/known_hosts")
remote=(ssh "${ssh_options[@]}" -- "$TEST_USER@$TEST_HOST")
platform=$("${remote[@]}" 'sudo -n true && uname -m')
case "$platform" in
  x86_64) platform=linux/amd64 ;;
  aarch64) platform=linux/arm64 ;;
  *) exit 1 ;;
esac

mkdir "$tmp/release"
git archive "$EXPECTED_SHA" | tar -x -C "$tmp/release"
for service in api web worker; do
  context="$tmp/release"
  [[ "$service" != worker ]] || context="$tmp/release/apps/worker"
  docker build --platform "$platform" -t "yihe-erp-$service:$EXPECTED_SHA" \
    -f "$tmp/release/apps/$service/Dockerfile" "$context"
done
docker build --platform "$platform" -t "yihe-erp-minio:$EXPECTED_SHA" "$tmp/release/infra/minio"
docker save "yihe-erp-minio:$EXPECTED_SHA" "yihe-erp-api:$EXPECTED_SHA" "yihe-erp-web:$EXPECTED_SHA" \
  "yihe-erp-worker:$EXPECTED_SHA" | gzip > "$tmp/release/images.tar.gz"
(cd "$tmp/release" && sha256sum images.tar.gz > images.tar.gz.sha256)
printf '%s\n' "$EXPECTED_SHA" > "$tmp/release/DEPLOY_COMMIT"
destination=$("${remote[@]}" 'mktemp -d /tmp/yihe-erp-test.XXXXXXXX')
[[ "$destination" =~ ^/tmp/yihe-erp-test\.[a-zA-Z0-9]+$ ]]
tar -C "$tmp/release" -cf - . | "${remote[@]}" "tar -xf - -C '$destination'"
"${remote[@]}" "cd '$destination' && sudo -n bash scripts/bootstrap-test-release.sh '$EXPECTED_SHA' '$BOOTSTRAP_HTTPS' '${ACME_EMAIL:-}'"
