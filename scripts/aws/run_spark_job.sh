#!/usr/bin/env bash
#
# Submit a polygon_adjustment_job run to EMR Serverless.
#
# Prerequisites:
#   - scripts/aws/setup_emr_serverless.sh has been run once on this account
#   - AWS_PROFILE set (stock-lake-ingest is fine post-setup)
#   - poetry env locally is functional (we run ensure_polygon_adjusted() before
#     submitting, since EMR uses --skip-ensure and can't create the table)
#
# Usage:
#   scripts/aws/run_spark_job.sh                         # whole-market
#   scripts/aws/run_spark_job.sh --symbols AAPL --since 2024-01-01
#   scripts/aws/run_spark_job.sh --since 2026-05-16      # weekly delta
#
# Flags (passed through to polygon_adjustment_job.py):
#   --symbols  Comma-separated. ALL or omitted = whole-market.
#   --since    YYYY-MM-DD lower bound.
#   --until    YYYY-MM-DD upper bound (inclusive).
#
# This script flags:
#   --wait     Poll until the job-run completes; print final status + duration.
#              Without --wait, just print the job-run-id and exit.
#
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────
# Config — must match setup_emr_serverless.sh
# ─────────────────────────────────────────────────────────────────────────
AWS_REGION="us-east-1"
ACCOUNT_ID="562741918372"
LAKE_BUCKET="stock-lake-562741918372-us-east-1-an"
ROLE_NAME="stock-lake-spark-emr"
EMR_APP_NAME="stockalert-spark-batch"
CODE_PREFIX="code/spark"

SCRIPT_S3_URI="s3://${LAKE_BUCKET}/${CODE_PREFIX}/polygon_adjustment_job.py"
PYDEPS_S3_URI="s3://${LAKE_BUCKET}/${CODE_PREFIX}/pydeps.zip"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"

# Output colors.
if [[ -t 1 ]]; then
  C_OK="\033[32m"; C_INFO="\033[34m"; C_END="\033[0m"
else
  C_OK=""; C_INFO=""; C_END=""
fi
log() { printf "${C_INFO}== %s${C_END}\n" "$*"; }
ok()  { printf "${C_OK}OK${C_END}  %s\n" "$*"; }

# ─────────────────────────────────────────────────────────────────────────
# Parse args — pass-through to entryPointArguments, plus our own --wait
# ─────────────────────────────────────────────────────────────────────────
WAIT=0
ENTRY_ARGS=("--skip-ensure")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --wait)     WAIT=1; shift ;;
    --symbols|--since|--until)
                ENTRY_ARGS+=("$1" "$2"); shift 2 ;;
    --help|-h)  sed -n '1,30p' "$0"; exit 0 ;;
    *)          echo "unknown arg: $1"; exit 1 ;;
  esac
done

# Build job-name with a UTC timestamp + the args so it's identifiable in EMR
# console.
JOB_NAME_TAG="$(echo "${ENTRY_ARGS[*]}" | tr ' ' '_' | tr -d '/-')"
JOB_NAME="polygon_adjust_$(date -u +%Y%m%d_%H%M%S)${JOB_NAME_TAG:+_${JOB_NAME_TAG}}"
JOB_NAME="${JOB_NAME:0:255}"  # EMR job-name limit

# ─────────────────────────────────────────────────────────────────────────
# 1. Resolve EMR_APP_ID (from env or by name lookup)
# ─────────────────────────────────────────────────────────────────────────
log "Resolve EMR application id"
if [[ -n "${EMR_APP_ID:-}" ]]; then
  ok "Using EMR_APP_ID from environment: ${EMR_APP_ID}"
else
  EMR_APP_ID="$(aws emr-serverless list-applications \
    --region "${AWS_REGION}" \
    --query "applications[?name=='${EMR_APP_NAME}'].id | [0]" \
    --output text 2>/dev/null || true)"
  if [[ -z "${EMR_APP_ID}" || "${EMR_APP_ID}" == "None" ]]; then
    echo "EMR application '${EMR_APP_NAME}' not found. Run setup_emr_serverless.sh first."
    exit 1
  fi
  ok "Found ${EMR_APP_NAME} → ${EMR_APP_ID}"
fi

# ─────────────────────────────────────────────────────────────────────────
# 2. Pre-create target table locally (EMR uses --skip-ensure)
# ─────────────────────────────────────────────────────────────────────────
log "Pre-create equities.polygon_adjusted (no-op if it exists)"
( cd "${ROOT}" && poetry run python -c "
from app.services.equities.tables import ensure_polygon_adjusted
t = ensure_polygon_adjusted()
print('  target table location:', t.location())
" )
ok "Target table verified"

# ─────────────────────────────────────────────────────────────────────────
# 3. Build job-driver JSON (entryPoint + entryPointArguments + sparkSubmit args)
# ─────────────────────────────────────────────────────────────────────────
log "Submit job: ${JOB_NAME}"

# JSON-escape each entry-point arg.
JSON_ARGS=$(printf '"%s",' "${ENTRY_ARGS[@]}" | sed 's/,$//')

JOB_DRIVER=$(cat <<JSON
{
  "sparkSubmit": {
    "entryPoint": "${SCRIPT_S3_URI}",
    "entryPointArguments": [${JSON_ARGS}],
    "sparkSubmitParameters": "--conf spark.executor.cores=4 --conf spark.dynamicAllocation.enabled=true --py-files ${PYDEPS_S3_URI}"
  }
}
JSON
)
# NOTE: Do NOT pass --packages org.apache.iceberg:... here. EMR Serverless
# release emr-7.0.0+ already bundles iceberg-spark-runtime + iceberg-aws-bundle
# on the worker classpath; --packages would tell Spark to re-fetch from Maven
# Central, which EMR Serverless workers cannot reach (no internet egress by
# default). Doing so caused a 20-min ConnectionTimeout failure on the first
# smoke-test run before this comment existed.

JOB_RUN_ID="$(aws emr-serverless start-job-run \
  --region "${AWS_REGION}" \
  --application-id "${EMR_APP_ID}" \
  --execution-role-arn "${ROLE_ARN}" \
  --name "${JOB_NAME}" \
  --job-driver "${JOB_DRIVER}" \
  --query 'jobRunId' --output text)"
ok "Submitted job-run-id=${JOB_RUN_ID}"
echo ""
echo "  Watch in console:"
echo "  https://${AWS_REGION}.console.aws.amazon.com/emr/home?region=${AWS_REGION}#/serverless/applications/${EMR_APP_ID}/jobs/${JOB_RUN_ID}"
echo ""

# ─────────────────────────────────────────────────────────────────────────
# 4. (Optional) Poll until done
# ─────────────────────────────────────────────────────────────────────────
if [[ "${WAIT}" == "1" ]]; then
  log "Polling for completion (poll every 30s)..."
  STARTED=$(date +%s)
  while true; do
    STATE="$(aws emr-serverless get-job-run \
      --region "${AWS_REGION}" \
      --application-id "${EMR_APP_ID}" \
      --job-run-id "${JOB_RUN_ID}" \
      --query 'jobRun.state' --output text)"
    ELAPSED=$(( $(date +%s) - STARTED ))
    printf "  [%4ds] state=%s\n" "${ELAPSED}" "${STATE}"
    case "${STATE}" in
      SUCCESS)
        ok "Job completed successfully in ${ELAPSED}s"
        exit 0 ;;
      FAILED|CANCELLED|CANCELLING)
        echo "Job ended in non-success state: ${STATE}"
        aws emr-serverless get-job-run \
          --region "${AWS_REGION}" \
          --application-id "${EMR_APP_ID}" \
          --job-run-id "${JOB_RUN_ID}" \
          --query 'jobRun.stateDetails' --output text
        exit 1 ;;
      *) sleep 30 ;;
    esac
  done
fi
