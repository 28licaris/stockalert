#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

AWS_PROFILE="${AWS_PROFILE:-stockalert-admin}"
AWS_REGION="${AWS_REGION:-us-east-1}"
COGNITO_USER_POOL_ID="${COGNITO_USER_POOL_ID:-us-east-1_NxTarvrk4}"
COGNITO_CLIENT_ID="${COGNITO_CLIENT_ID:-5tb9l4uv74jncbdm9vgd4lr76c}"

if [[ ! -x .venv/bin/uvicorn ]]; then
  echo "Run this script from the repository root with the project virtualenv installed." >&2
  exit 1
fi

COGNITO_CLIENT_SECRET="$({ AWS_PAGER='' aws cognito-idp describe-user-pool-client \
  --user-pool-id "$COGNITO_USER_POOL_ID" \
  --client-id "$COGNITO_CLIENT_ID" \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --query 'UserPoolClient.ClientSecret' \
  --output text; })"

export AUTH_ENABLED=true
export IDENTITY_DATABASE_URL="${IDENTITY_DATABASE_URL:-postgresql+psycopg://stockalert:stockalert_dev@127.0.0.1:5432/stockalert_identity}"
export COGNITO_DOMAIN="${COGNITO_DOMAIN:-https://stockalert-dev-562741918372.auth.us-east-1.amazoncognito.com}"
export COGNITO_ISSUER_URL="${COGNITO_ISSUER_URL:-https://cognito-idp.us-east-1.amazonaws.com/$COGNITO_USER_POOL_ID}"
export COGNITO_CLIENT_ID
export COGNITO_CLIENT_SECRET
export COGNITO_REDIRECT_URI="${COGNITO_REDIRECT_URI:-http://localhost:8000/auth/callback}"
export COGNITO_LOGOUT_URI="${COGNITO_LOGOUT_URI:-http://localhost:5173/app/login}"
export AUTH_COOKIE_SECURE="${AUTH_COOKIE_SECURE:-false}"

exec .venv/bin/uvicorn app.main_api:app --host 127.0.0.1 --port 8000
