#!/usr/bin/env bash
# Nightly identity PostgreSQL dump → s3://<ops-bucket>/pg_dump/ (30-day lifecycle).
# Spec: docs/operations/deployment_dev.md §1 "Backups", §7. Cron: 04:00 UTC.
# Verifies the upload cross-side (head-object size == local size) before
# declaring success; a zero-byte dump is a failure, not a backup.
set -euo pipefail

ROOT=${STOCKALERT_ROOT:-/opt/stockalert}
log() { printf '%s pg_backup: %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

[[ -f /etc/stockalert.conf ]] || die "/etc/stockalert.conf missing"
set -a; source /etc/stockalert.conf; set +a
export AWS_DEFAULT_REGION="$AWS_REGION"
: "${OPS_BUCKET:?OPS_BUCKET not set in /etc/stockalert.conf}"
cd "$ROOT"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
name="stockalert_identity_${STOCKALERT_ENV}_${stamp}.dump"
tmp=$(mktemp "/tmp/${name}.XXXXXX")
trap 'rm -f "$tmp"' EXIT

# -Fc = custom format (pg_restore-able, compressed). -T so no tty is required under cron.
docker compose exec -T postgres pg_dump -U stockalert -Fc stockalert_identity > "$tmp" \
  || die "pg_dump failed"
size=$(stat -c %s "$tmp")
log "dump bytes=$size file=$name"
(( size > 1024 )) || die "dump is only $size bytes — refusing to upload an empty backup"

key="pg_dump/${name}"
aws s3 cp --only-show-errors "$tmp" "s3://${OPS_BUCKET}/${key}" || die "s3 cp failed"
remote=$(aws s3api head-object --bucket "$OPS_BUCKET" --key "$key" --query ContentLength --output text) \
  || die "head-object after upload failed"
[[ "$remote" == "$size" ]] || die "size mismatch local=$size s3=$remote"
log "BACKUP OK s3://${OPS_BUCKET}/${key} bytes=$size"
