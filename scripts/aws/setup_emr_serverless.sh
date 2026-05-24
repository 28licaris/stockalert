#!/usr/bin/env bash
#
# Idempotent setup for EMR Serverless Spark on this AWS account.
# Safe to re-run — every step is check-then-create.
#
# Creates / verifies:
#   1. IAM execution role `stock-lake-spark-emr` (EMR assumes this to run jobs)
#   2. Permissions policy attached to that role (Glue + S3 + CloudWatch Logs)
#   3. EMR-management permissions added to user `stock-lake-ingest`
#   4. EMR Serverless application `stockalert-spark-batch` (Spark on emr-7.0.0)
#   5. Upload of entry script + pydeps.zip to s3://${LAKE_BUCKET}/code/spark/
#
# Prerequisites (operator):
#   - AWS_PROFILE pointing at an identity with IAM admin access (root or admin
#     IAM user). The day-to-day stock-lake-ingest user lacks IAM write
#     permissions; the setup steps need broader perms one time.
#   - aws CLI v2 installed and authenticated.
#
# Outputs (printed at end):
#   - EMR_APP_ID — pass to `aws emr-serverless start-job-run`
#   - EXECUTION_ROLE_ARN — pass to `--execution-role-arn`
#   - SCRIPT_S3_URI + PYDEPS_S3_URI — the entryPoint + py-files paths
#
# Usage:
#   AWS_PROFILE=<admin-profile> scripts/aws/setup_emr_serverless.sh
#
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────
# Config (matches the resources referenced in the IAM policies under
# scripts/aws/iam_policies/. If you change one of these, update the
# JSON files too.)
# ─────────────────────────────────────────────────────────────────────────
AWS_REGION="us-east-1"
ACCOUNT_ID="562741918372"
LAKE_BUCKET="stock-lake-562741918372-us-east-1-an"
ROLE_NAME="stock-lake-spark-emr"
USER_NAME="stock-lake-ingest"
EMR_APP_NAME="stockalert-spark-batch"
EMR_RELEASE_LABEL="emr-7.0.0"
CODE_PREFIX="code/spark"

# Paths relative to project root (resolved at runtime).
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"
POLICIES_DIR="${HERE}/iam_policies"
ENTRY_SCRIPT="${ROOT}/scripts/spark/polygon_adjustment_job.py"
PYDEPS_SOURCE_REL="scripts/spark/__init__.py"   # relative to ROOT for the zip

# Output paths in S3.
SCRIPT_S3_URI="s3://${LAKE_BUCKET}/${CODE_PREFIX}/polygon_adjustment_job.py"
PYDEPS_S3_URI="s3://${LAKE_BUCKET}/${CODE_PREFIX}/pydeps.zip"

# Color helpers (no-op if not a terminal).
if [[ -t 1 ]]; then
  C_OK="\033[32m"; C_INFO="\033[34m"; C_WARN="\033[33m"; C_END="\033[0m"
else
  C_OK=""; C_INFO=""; C_WARN=""; C_END=""
fi
log()  { printf "${C_INFO}== %s${C_END}\n" "$*"; }
ok()   { printf "${C_OK}OK${C_END}  %s\n" "$*"; }
warn() { printf "${C_WARN}!!${C_END}  %s\n" "$*"; }

# ─────────────────────────────────────────────────────────────────────────
# 0. Preflight
# ─────────────────────────────────────────────────────────────────────────
log "0. Preflight"
command -v aws >/dev/null || { echo "aws CLI not found"; exit 1; }
command -v zip >/dev/null || { echo "zip not found"; exit 1; }
[[ -f "${ENTRY_SCRIPT}" ]] || { echo "Entry script missing: ${ENTRY_SCRIPT}"; exit 1; }
[[ -f "${ROOT}/${PYDEPS_SOURCE_REL}" ]] || { echo "pydeps source missing: ${ROOT}/${PYDEPS_SOURCE_REL}"; exit 1; }
for f in spark-emr-execution-role-trust-policy.json \
         spark-emr-execution-role-permissions.json \
         stock-lake-ingest-emr-additions.json; do
  [[ -f "${POLICIES_DIR}/${f}" ]] || { echo "Missing policy file: ${POLICIES_DIR}/${f}"; exit 1; }
done

CALLER_IDENTITY="$(aws sts get-caller-identity --output json)"
CALLER_ARN="$(echo "${CALLER_IDENTITY}" | grep -o '"Arn": "[^"]*"' | cut -d'"' -f4)"
CALLER_ACCT="$(echo "${CALLER_IDENTITY}" | grep -o '"Account": "[^"]*"' | cut -d'"' -f4)"

if [[ "${CALLER_ACCT}" != "${ACCOUNT_ID}" ]]; then
  echo "AWS account mismatch: caller is ${CALLER_ACCT}, expected ${ACCOUNT_ID}"
  echo "Set AWS_PROFILE to an identity on account ${ACCOUNT_ID}."
  exit 1
fi
ok "Authenticated as ${CALLER_ARN} on account ${ACCOUNT_ID}"

# ─────────────────────────────────────────────────────────────────────────
# 1. IAM execution role
# ─────────────────────────────────────────────────────────────────────────
log "1. IAM execution role: ${ROLE_NAME}"
if aws iam get-role --role-name "${ROLE_NAME}" >/dev/null 2>&1; then
  ok "Role ${ROLE_NAME} already exists, skipping create"
else
  aws iam create-role \
    --role-name "${ROLE_NAME}" \
    --assume-role-policy-document "file://${POLICIES_DIR}/spark-emr-execution-role-trust-policy.json" \
    --description "EMR Serverless execution role for Spark batch jobs on the equities lake" \
    >/dev/null
  ok "Created role ${ROLE_NAME}"
fi

# Always (re-)attach the permissions policy — content may evolve over time.
aws iam put-role-policy \
  --role-name "${ROLE_NAME}" \
  --policy-name "spark-emr-execution-permissions" \
  --policy-document "file://${POLICIES_DIR}/spark-emr-execution-role-permissions.json"
ok "Permissions policy applied to role"

ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

# ─────────────────────────────────────────────────────────────────────────
# 2. EMR additions to stock-lake-ingest user
# ─────────────────────────────────────────────────────────────────────────
log "2. EMR additions to user: ${USER_NAME}"
if aws iam get-user --user-name "${USER_NAME}" >/dev/null 2>&1; then
  aws iam put-user-policy \
    --user-name "${USER_NAME}" \
    --policy-name "stock-lake-ingest-emr-additions" \
    --policy-document "file://${POLICIES_DIR}/stock-lake-ingest-emr-additions.json"
  ok "EMR additions applied to user ${USER_NAME}"
else
  warn "User ${USER_NAME} not found — skip user-policy attach"
  warn "If you use roles or a different user, attach stock-lake-ingest-emr-additions.json there manually"
fi

# ─────────────────────────────────────────────────────────────────────────
# 3. EMR Serverless application
# ─────────────────────────────────────────────────────────────────────────
log "3. EMR Serverless application: ${EMR_APP_NAME}"
EMR_APP_ID="$(aws emr-serverless list-applications \
  --region "${AWS_REGION}" \
  --query "applications[?name=='${EMR_APP_NAME}'].id | [0]" \
  --output text 2>/dev/null || true)"

if [[ -n "${EMR_APP_ID}" && "${EMR_APP_ID}" != "None" ]]; then
  ok "Application ${EMR_APP_NAME} already exists (id=${EMR_APP_ID})"
else
  EMR_APP_ID="$(aws emr-serverless create-application \
    --region "${AWS_REGION}" \
    --name "${EMR_APP_NAME}" \
    --release-label "${EMR_RELEASE_LABEL}" \
    --type SPARK \
    --maximum-capacity '{"cpu":"100vCPU","memory":"400GB"}' \
    --auto-start-configuration '{"enabled":true}' \
    --auto-stop-configuration '{"enabled":true,"idleTimeoutMinutes":15}' \
    --query 'applicationId' --output text)"
  ok "Created application id=${EMR_APP_ID}"
fi

# ─────────────────────────────────────────────────────────────────────────
# 4. Build + upload pydeps.zip
# ─────────────────────────────────────────────────────────────────────────
log "4. Build pydeps.zip"
PYDEPS_TMP="$(mktemp -d)/pydeps.zip"
(
  cd "${ROOT}"
  # Zip path-prefixed so `from scripts.spark import …` works at runtime.
  zip -q -r "${PYDEPS_TMP}" "${PYDEPS_SOURCE_REL}"
)
PYDEPS_BYTES="$(wc -c < "${PYDEPS_TMP}" | tr -d ' ')"
ok "Built pydeps.zip (${PYDEPS_BYTES} bytes)"

# ─────────────────────────────────────────────────────────────────────────
# 5. Upload entry script + pydeps to S3
# ─────────────────────────────────────────────────────────────────────────
log "5. Upload to S3 under s3://${LAKE_BUCKET}/${CODE_PREFIX}/"
aws s3 cp "${ENTRY_SCRIPT}" "${SCRIPT_S3_URI}" --region "${AWS_REGION}" --only-show-errors
ok "Uploaded ${SCRIPT_S3_URI}"

aws s3 cp "${PYDEPS_TMP}" "${PYDEPS_S3_URI}" --region "${AWS_REGION}" --only-show-errors
ok "Uploaded ${PYDEPS_S3_URI}"

rm -f "${PYDEPS_TMP}"

# ─────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────
cat <<EOF

================================================================
  EMR Serverless setup complete
================================================================

  EMR_APP_ID            ${EMR_APP_ID}
  EXECUTION_ROLE_ARN    ${ROLE_ARN}
  SCRIPT_S3_URI         ${SCRIPT_S3_URI}
  PYDEPS_S3_URI         ${PYDEPS_S3_URI}
  EMR_RELEASE_LABEL     ${EMR_RELEASE_LABEL}

  Export for downstream scripts:
    export EMR_APP_ID="${EMR_APP_ID}"
    export EXECUTION_ROLE_ARN="${ROLE_ARN}"

  Next step:
    scripts/aws/run_spark_job.sh --symbols AAPL --since 2024-01-01

EOF
