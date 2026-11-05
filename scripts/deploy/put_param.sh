#!/usr/bin/env bash
# Store one secret: stdin → SSM SecureString /stockalert/<env>/<name>.
# Spec: docs/operations/deployment_dev.md §6, §7 (parameter names).
#
#   printf '%s' "$VALUE" | scripts/deploy/put_param.sh <env> <name>
#   scripts/deploy/put_param.sh dev schwab_client_secret < secret.txt
#
# Exactly one trailing newline is stripped (so `echo value |` works); nothing
# else is altered. The value is never printed; only its length is logged.
# Containers see it as UPPER_CASE(<name>) after bin/render_env.sh runs on the box
# (`systemctl restart stockalert-env stockalert`, or the next release).
set -euo pipefail
SCRIPT_NAME=put_param
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() { echo "usage: printf '%s' VALUE | $0 <env> <name>   (name: [a-z0-9_]+, e.g. schwab_refresh_token)" >&2; exit 2; }
[[ $# -eq 2 ]] || usage
ENV_NAME=$1; NAME=$2
check_env "$ENV_NAME"
[[ "$NAME" =~ ^[a-z0-9_]+$ ]] || die "name must match [a-z0-9_]+ (becomes env var $(tr a-z A-Z <<< "$NAME")), got '$NAME'"
require_tools aws
[[ -t 0 ]] && die "value must come on stdin (refusing to read it from the terminal/history)"

value=$(cat; printf x); value=${value%x}        # keep every byte, including trailing newlines…
value=${value%$'\n'}                              # …then strip exactly one
[[ -n "$value" ]] || die "empty value on stdin"
[[ "$value" != *$'\n'* ]] || die "value contains a newline — env files are one line per key"

path="/stockalert/$ENV_NAME/$NAME"
version=$(aws ssm put-parameter --name "$path" --type SecureString --overwrite --value "$value" \
            --query Version --output text) || die "put-parameter $path failed"

# Cross-side verify: read it back and compare length (never the value itself).
back_len=$(aws ssm get-parameter --name "$path" --with-decryption --query 'Parameter.Value' --output text | awk '{print length($0)}')
[[ "$back_len" == "${#value}" ]] || die "readback length $back_len != ${#value} for $path"
log "OK $path version=$version length=${#value} → env ${NAME^^} (render on the box: systemctl restart stockalert-env stockalert, or next release)"
