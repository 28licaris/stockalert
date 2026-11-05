#!/usr/bin/env bash
# Render /opt/stockalert/.env from app.env + release.env + SSM Parameter Store.
# Spec: docs/operations/deployment_dev.md §7 (secrets) and §3 (flags).
#
# Order (later wins, docker compose semantics): app.env → release.env → SSM
# /stockalert/<env>/* (leaf name upper-cased) → derived values. Written to a
# temp file with umask 077 and moved into place atomically, so a half-written
# .env is never what `docker compose` reads. Exits non-zero, with the missing
# key named, when a required value is absent — a container must never start
# against the example's defaults.
#
# Env (from /etc/stockalert.conf via systemd): STOCKALERT_ENV, AWS_REGION.
set -euo pipefail

ROOT=${STOCKALERT_ROOT:-/opt/stockalert}
: "${STOCKALERT_ENV:?STOCKALERT_ENV not set (see /etc/stockalert.conf)}"
: "${AWS_REGION:?AWS_REGION not set (see /etc/stockalert.conf)}"
export AWS_DEFAULT_REGION="$AWS_REGION"

log() { printf '%s render_env: %s\n' "$(date -u +%FT%TZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

APP_ENV="$ROOT/app.env"
RELEASE_ENV="$ROOT/release.env"
OUT="$ROOT/.env"
SSM_PATH="/stockalert/${STOCKALERT_ENV}/"

[[ -f "$APP_ENV" ]] || die "$APP_ENV missing — was infra/dev/ synced to the box?"

umask 077
TMP=$(mktemp "$ROOT/.env.render.XXXXXX")
trap 'rm -f "$TMP"' EXIT

# Single-quote a value for an env file; escape embedded single quotes.
q() { local v=${1//\'/\'\\\'\'}; printf "'%s'" "$v"; }

{
  echo "# RENDERED by bin/render_env.sh at $(date -u +%FT%TZ) — do not edit; edit app.env or SSM"
  echo "# ── app.env ──"
  cat "$APP_ENV"
  echo
  echo "# ── release.env ──"
  if [[ -f "$RELEASE_ENV" ]]; then cat "$RELEASE_ENV"; else echo "# (none — bin/release.sh has not run yet)"; fi
  echo
  echo "# ── SSM ${SSM_PATH}* ──"
} > "$TMP"

# ── SSM ───────────────────────────────────────────────────────────────────────
# One call, paginated by the CLI; names become UPPER_CASE env keys.
ssm_tsv=$(aws ssm get-parameters-by-path --path "$SSM_PATH" --recursive --with-decryption \
            --query 'Parameters[].[Name,Value]' --output text) \
  || die "ssm get-parameters-by-path $SSM_PATH failed (instance role? region=$AWS_REGION)"

ssm_count=0
if [[ -n "$ssm_tsv" ]]; then
  while IFS=$'\t' read -r name value; do
    [[ -n "$name" ]] || continue
    key=$(basename "$name" | tr '[:lower:]' '[:upper:]' | tr -c 'A-Z0-9_\n' '_')
    printf '%s=%s\n' "$key" "$(q "$value")" >> "$TMP"
    ssm_count=$((ssm_count + 1))
  done <<< "$ssm_tsv"
fi
log "ssm parameters under $SSM_PATH: $ssm_count"
[[ $ssm_count -gt 0 ]] || die "zero SSM parameters under $SSM_PATH — load them with scripts/deploy/put_param.sh first"

# ── Load what we have so far to derive the rest ───────────────────────────────
set -a
# shellcheck disable=SC1090
source "$TMP"
set +a

{
  echo
  echo "# ── derived by render_env.sh ──"
} >> "$TMP"

# DEV_HOST: committed value wins; else app.<eip-with-dashes>.sslip.io from IMDSv2.
if [[ -z "${DEV_HOST:-}" ]]; then
  tok=$(curl -fsS -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' http://169.254.169.254/latest/api/token) \
    || die "IMDSv2 token request failed and DEV_HOST is not set"
  pub_ip=$(curl -fsS -H "X-aws-ec2-metadata-token: $tok" http://169.254.169.254/latest/meta-data/public-ipv4) \
    || die "no public-ipv4 in IMDS (EIP not attached yet?) and DEV_HOST is not set"
  DEV_HOST="app.${pub_ip//./-}.sslip.io"
  printf 'DEV_HOST=%s\n' "$(q "$DEV_HOST")" >> "$TMP"
  log "DEV_HOST derived from EIP: $DEV_HOST"
else
  log "DEV_HOST from config: $DEV_HOST"
fi

# POSTGRES_PASSWORD is embedded in a URL: keep it URL-safe rather than encode it.
[[ -n "${POSTGRES_PASSWORD:-}" ]] || die "POSTGRES_PASSWORD missing (SSM ${SSM_PATH}postgres_password)"
[[ "$POSTGRES_PASSWORD" =~ ^[A-Za-z0-9_.~-]+$ ]] \
  || die "postgres_password must be URL-safe ([A-Za-z0-9_.~-]) — it is embedded in IDENTITY_DATABASE_URL"
{
  printf 'IDENTITY_DATABASE_URL=%s\n' "$(q "postgresql+psycopg://stockalert:${POSTGRES_PASSWORD}@postgres:5432/stockalert_identity")"
  printf 'COGNITO_REDIRECT_URI=%s\n'  "$(q "https://${DEV_HOST}/auth/callback")"
  printf 'COGNITO_LOGOUT_URI=%s\n'    "$(q "https://${DEV_HOST}/app/login")"
  printf 'BILLING_RETURN_URL=%s\n'    "$(q "https://${DEV_HOST}/app/settings")"
} >> "$TMP"

# ── Required keys: fail loud, name the key ────────────────────────────────────
set -a; source "$TMP"; set +a
missing=()
for k in APP_IMAGE AWS_REGION LOG_GROUP STOCK_LAKE_BUCKET CLICKHOUSE_PASSWORD POSTGRES_PASSWORD \
         COGNITO_CLIENT_ID COGNITO_CLIENT_SECRET SCHWAB_CLIENT_ID SCHWAB_CLIENT_SECRET \
         SCHWAB_REFRESH_TOKEN POLYGON_API_KEY ADMIN_EMAILS; do
  [[ -n "${!k:-}" ]] || missing+=("$k")
done
if (( ${#missing[@]} )); then
  die "required keys missing: ${missing[*]} (APP_IMAGE → bin/release.sh; others → scripts/deploy/put_param.sh)"
fi
for k in STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET STRIPE_PRICE_MONTHLY STRIPE_PRICE_ANNUAL ANTHROPIC_API_KEY BLS_API_KEY BEA_API_KEY; do
  [[ -n "${!k:-}" ]] || log "WARN optional key unset: $k (feature degrades to 'not configured')"
done

total=$(grep -cE '^[A-Z_][A-Z0-9_]*=' "$TMP")
mv -f "$TMP" "$OUT"
trap - EXIT
chmod 0600 "$OUT"
log "wrote $OUT keys=$total ssm=$ssm_count env=$STOCKALERT_ENV host=$DEV_HOST"
