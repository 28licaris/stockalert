#!/usr/bin/env bash
# The 7-day chore: get a fresh Schwab refresh token on the Mac, push it to SSM,
# restart worker + api on the box so they pick it up.
# Spec: docs/operations/deployment_dev.md §6. Memory: reference_schwab_token_expiry_freeze.
#
#   scripts/deploy/push_schwab_token.sh <env>            # runs the OAuth script, then pushes
#   scripts/deploy/push_schwab_token.sh <env> --no-auth  # pushes data/.schwab_refresh_token as-is
#
# While the box streams, the laptop must run with STREAM_PROVIDER=none — Schwab
# allows one streamer session; the token is shared, the session is not.
set -euo pipefail
SCRIPT_NAME=push_schwab_token
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() { echo "usage: $0 <env> [--no-auth]" >&2; exit 2; }
[[ $# -ge 1 && $# -le 2 ]] || usage
ENV_NAME=$1; shift
RUN_AUTH=true
[[ "${1:-}" == "--no-auth" ]] && RUN_AUTH=false
[[ $# -le 1 ]] || usage
check_env "$ENV_NAME"
require_tools aws poetry python3

cd "$REPO_ROOT"
token_file=${SCHWAB_REFRESH_TOKEN_FILE:-data/.schwab_refresh_token}

if $RUN_AUTH; then
  log "running scripts/schwab_get_refresh_token.py (browser OAuth; writes $token_file)"
  poetry run python scripts/schwab_get_refresh_token.py || die "schwab_get_refresh_token.py failed"
fi
[[ -s "$token_file" ]] || die "$token_file missing or empty"
age_min=$(( ( $(date +%s) - $(stat -f %m "$token_file") ) / 60 ))
log "token file age=${age_min}m length=$(tr -d '\n' < "$token_file" | wc -c | tr -d ' ')"
(( age_min < 7 * 24 * 60 )) || die "token file is older than 7 days — run without --no-auth"

tr -d '\n' < "$token_file" | "$REPO_ROOT/scripts/deploy/put_param.sh" "$ENV_NAME" schwab_refresh_token

instance=$(stack_output "$ENV_NAME" InstanceId)
ssm_run "$ENV_NAME" "$instance" 600 "schwab-token" <<'EOS'
set -euo pipefail
cd /opt/stockalert
bin/render_env.sh
docker compose up -d --force-recreate --wait worker api
docker compose ps --format '{{.Service}}={{.State}}' | tr '\n' ' '; echo
docker compose exec -T api python -c "import json,urllib.request; print('api /health:', json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5))))"
EOS
log "TOKEN OK pushed to /stockalert/$ENV_NAME/schwab_refresh_token and worker+api recreated on $instance"
