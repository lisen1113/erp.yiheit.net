#!/usr/bin/env bash
set -euo pipefail
sha=62612dfa85382fbd8f10b0ea19b1cd4137b8b6eb
archive="$HOME/t011-62612df-source.tar.gz"
release="/srv/yihe-erp/releases/$sha"
hash=2586fd78d4942a9ee0957cf0fbeef99f20256ca3b5b8211d01f57c404ffae2e4
source_mode=${1:-docker-hub}
[[ "$source_mode" == docker-hub || "$source_mode" == official-alternate ]]
python_ref=public.ecr.aws/docker/library/python@sha256:0f086103b026048cc50cb5da25ec435c023c5241a83fc44daea593377abc61c4
node_ref=public.ecr.aws/docker/library/node@sha256:0e2646ab57e77b7a384a0cb59b6815b182c77e1f9c3ba36bd8435bd9c4e6680e
uv_ref=ghcr.io/astral-sh/uv@sha256:49fe3c925d4b7a84ba2ef19883307bba2bbcf929a22040afb46f2dd092d13aeb
[[ "$(uname -m)" == x86_64 ]]
sudo -n true
printf '%s  %s\n' "$hash" "$archive" | sha256sum -c -
exec 9<"$archive"
flock -n 9
work=$(mktemp -d)
build_pid=
cleanup() {
  if [[ -n "$build_pid" ]]; then
    kill -TERM "$build_pid" 2>/dev/null || true
    wait "$build_pid" 2>/dev/null || true
  fi
  rm -rf -- "$work"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

website() {
  local url result
  for url in http://yiheit.net/ https://yiheit.net/ http://www.yiheit.net/ https://www.yiheit.net/; do
    result=$(curl -q --noproxy '*' -fsS --connect-timeout 5 --max-time 12 \
      -o /dev/null -w '%{http_code} %{redirect_url}|%{time_total}' "$url") || return
    printf '%s %s\n' "$url" "$result"
  done
}

containers=(yiheit-app-1 yiheit-db-1)
website_containers() {
  sudo -n docker inspect -f '{{.Id}} {{.State.Running}} {{.RestartCount}} {{.State.StartedAt}}' "${containers[@]}"
}
baseline=$(website)
container_baseline=$(website_containers)
[[ "$(grep -c ' true ' <<<"$container_baseline")" == 2 ]]
started=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
last_swap=$(awk '/^SwapTotal:/ {total=$2} /^SwapFree:/ {print total-$2}' /proc/meminfo)
swap_streak=0

check() {
  local available disk used_swap growth current
  available=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
  disk=$(df -PB1 / /var/lib/docker | awk 'NR>1 {if (!n++ || $4<min) min=$4} END {print min}')
  (( available >= 700 * 1024 )) || { echo 'Available RAM below 700 MiB' >&2; return 1; }
  (( disk >= 30000000000 )) || { echo 'Disk space below 30 GB' >&2; return 1; }
  used_swap=$(awk '/^SwapTotal:/ {total=$2} /^SwapFree:/ {print total-$2}' /proc/meminfo)
  growth=$((used_swap-last_swap))
  if (( growth >= 32 * 1024 )); then
    swap_streak=$((swap_streak+1))
  else
    swap_streak=0
  fi
  last_swap=$used_swap
  (( growth < 128 * 1024 && swap_streak < 2 )) || { echo 'Swap is growing' >&2; return 1; }
  current=$(sudo -n journalctl -k --since "$started" --no-pager) || return 1
  if grep -Eiq 'oom-kill|out of memory|killed process' <<<"$current"; then
    echo 'New kernel OOM record' >&2
    return 1
  fi
  [[ "$(website_containers)" == "$container_baseline" ]] || { echo 'Website container changed or restarted' >&2; return 1; }
  current=$(website) || return 1
  printf '%s\n' "$current"
  diff -u <(cut -d '|' -f1 <<<"$baseline") <(cut -d '|' -f1 <<<"$current") || return 1
  paste <(cut -d '|' -f2 <<<"$baseline") <(cut -d '|' -f2 <<<"$current") |
    awk '$2 > 3 && $2 > 3*$1 {failed=1} END {exit failed}'
}

snapshot() {
  free -h
  df -h / /var/lib/docker
  uptime
  timeout 15s sudo -n docker stats --no-stream
  check
}

tar -xzf "$archive" -C "$work"
if sudo -n test -L "$release"; then
  echo 'Unexpected symlink at release path' >&2
  exit 1
elif sudo -n test -d "$release"; then
  sudo -n diff -qr -- "$work" "$release"
else
  sudo -n mkdir -p -- "$(dirname "$release")"
  sudo -n mkdir -- "$release"
  sudo -n tar -xzf "$archive" --no-same-owner -C "$release"
fi

for service in api worker web; do
  tag="yihe-erp-$service:$sha"
  printf '\n== Before build: %s ==\n' "$tag"
  snapshot
  context="$release"
  [[ "$service" != worker ]] || context="$release/apps/worker"
  dockerfile="$release/apps/$service/Dockerfile"
  if [[ "$source_mode" == official-alternate ]]; then
    # The legacy Docker builder requires its Dockerfile inside the build context.
    # Stage a verified copy in the temporary workspace; keep the release untouched.
    build_context="$work"
    [[ "$service" != worker ]] || build_context="$work/apps/worker"
    context="$build_context"
    dockerfile="$build_context/Dockerfile.t011-pinned"
    sed -e "s|^FROM python:3.12-slim-bookworm$|FROM $python_ref|" \
      -e "s|^FROM node:24-bookworm-slim$|FROM $node_ref|" \
      -e "s|^COPY --from=ghcr.io/astral-sh/uv:0.13.9 |COPY --from=$uv_ref |" \
      "$release/apps/$service/Dockerfile" > "$dockerfile"
    base=$python_ref
    [[ "$service" != web ]] || base=$node_ref
    grep -Fxq "FROM $base" "$dockerfile"
    if [[ "$service" == api ]]; then grep -Fq "COPY --from=$uv_ref " "$dockerfile"; fi
  fi
  build_started=$SECONDS
  sudo -n docker build --platform linux/amd64 \
    --label "org.opencontainers.image.revision=$sha" \
    -t "$tag" -f "$dockerfile" "$context" &
  build_pid=$!
  while kill -0 "$build_pid" 2>/dev/null; do
    sleep 5
    if kill -0 "$build_pid" 2>/dev/null; then check; fi
  done
  wait "$build_pid"
  build_pid=
  printf '\nBuild duration: %ss\n' "$((SECONDS-build_started))"
  sudo -n docker image inspect -f '{{index .RepoTags 0}} {{.Id}} {{.Size}} bytes {{.Os}}/{{.Architecture}}' "$tag"
  printf '\n== After build: %s ==\n' "$tag"
  snapshot
done
printf '\nT011 serial builds completed for %s.\n' "$sha"
