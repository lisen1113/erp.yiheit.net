#!/bin/sh
set -eu
[ "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/erp.yiheit.net ] || exit 0
nginx -t
systemctl reload nginx
