#!/usr/bin/env bash
set -euo pipefail

root=/srv/yihe-erp
archive=$(bash "$root/app/scripts/backup-test.sh")
cd "$root/backups"
umask 077
install -d -m 0700 daily weekly
day=$(date -u +%F)
week=$(date -u +%G-W%V)

publish() {
  local directory=$1 key=$2
  local staging
  staging=$(mktemp -d "$directory/.staging.XXXXXX")
  ln "$archive" "$archive.sha256" "$staging/"
  mv -T -- "$staging" "$directory/$key"
}

prune() {
  local directory=$1 keep=$2 pattern=$3
  local snapshots=()
  mapfile -t snapshots < <(find "$directory" -mindepth 1 -maxdepth 1 -type d \
    -printf '%f\n' | grep -E "$pattern" | LC_ALL=C sort -r)
  local snapshot
  for snapshot in "${snapshots[@]:keep}"; do
    find "$directory/$snapshot" -maxdepth 1 -type f -name 'erp-test-*.dump.gz*' -delete
    rmdir -- "$directory/$snapshot"
  done
}

exec 9>retention.lock
flock 9
[[ -d "daily/$day" ]] || publish daily "$day"
[[ -d "weekly/$week" ]] || publish weekly "$week"
prune daily 7 '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
prune weekly 4 '^[0-9]{4}-W[0-9]{2}$'
rm -- "$archive" "$archive.sha256"
