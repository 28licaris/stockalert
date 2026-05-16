#!/usr/bin/env bash
# Provision the stock-lake S3 bucket + AWS Glue database for the Iceberg
# data platform. Idempotent — safe to re-run.
#
# Requires AWS CLI v2 and credentials configured (env, ~/.aws/credentials,
# or IAM role). Run from anywhere; uses absolute paths.
#
# Usage:
#   BUCKET=stock-lake REGION=us-east-1 GLUE_DB=stock_lake \
#     scripts/provision_lake_infra.sh
#
# Or rely on .env defaults defined in this file:
set -euo pipefail

BUCKET="${BUCKET:-${STOCK_LAKE_BUCKET:-stock-lake}}"
REGION="${REGION:-${STOCK_LAKE_REGION:-us-east-1}}"
GLUE_DB="${GLUE_DB:-${ICEBERG_GLUE_DATABASE:-stock_lake}}"
WAREHOUSE_PREFIX="${WAREHOUSE_PREFIX:-${ICEBERG_WAREHOUSE_PREFIX:-iceberg}}"

echo "==> Target configuration"
echo "    Bucket    : ${BUCKET}"
echo "    Region    : ${REGION}"
echo "    Glue DB   : ${GLUE_DB}"
echo "    Warehouse : s3://${BUCKET}/${WAREHOUSE_PREFIX}/"
echo

# ──────────────────────────────────────────────────────────────────────
# Sanity: identity + region
# ──────────────────────────────────────────────────────────────────────
echo "==> Verifying AWS identity"
aws sts get-caller-identity --output table

# ──────────────────────────────────────────────────────────────────────
# Bucket
# ──────────────────────────────────────────────────────────────────────
echo "==> Bucket: ${BUCKET}"
if aws s3api head-bucket --bucket "${BUCKET}" 2>/dev/null; then
  echo "    exists"
else
  echo "    creating in ${REGION}"
  if [[ "${REGION}" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "${BUCKET}" --region "${REGION}"
  else
    aws s3api create-bucket \
      --bucket "${BUCKET}" \
      --region "${REGION}" \
      --create-bucket-configuration "LocationConstraint=${REGION}"
  fi
fi

# Public access block (ON)
echo "==> Blocking public access"
aws s3api put-public-access-block --bucket "${BUCKET}" --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# Versioning (ON)
echo "==> Enabling versioning"
aws s3api put-bucket-versioning --bucket "${BUCKET}" --versioning-configuration "Status=Enabled"

# Default encryption (SSE-S3)
echo "==> Enabling default encryption (SSE-S3)"
aws s3api put-bucket-encryption --bucket "${BUCKET}" --server-side-encryption-configuration '{
  "Rules": [{
    "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
    "BucketKeyEnabled": true
  }]
}'

# Lifecycle (transitions per data_platform_plan.md §3)
echo "==> Applying lifecycle rules"
LIFECYCLE_JSON="$(mktemp)"
cat >"${LIFECYCLE_JSON}" <<JSON
{
  "Rules": [
    {
      "ID": "bronze-tiering",
      "Filter": {"Prefix": "${WAREHOUSE_PREFIX}/bronze/"},
      "Status": "Enabled",
      "Transitions": [
        {"Days": 180, "StorageClass": "STANDARD_IA"},
        {"Days": 365, "StorageClass": "GLACIER_IR"}
      ],
      "NoncurrentVersionExpiration": {"NoncurrentDays": 30},
      "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}
    },
    {
      "ID": "silver-hot",
      "Filter": {"Prefix": "${WAREHOUSE_PREFIX}/silver/"},
      "Status": "Enabled",
      "NoncurrentVersionExpiration": {"NoncurrentDays": 30},
      "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}
    },
    {
      "ID": "gold-hot",
      "Filter": {"Prefix": "${WAREHOUSE_PREFIX}/gold/"},
      "Status": "Enabled",
      "NoncurrentVersionExpiration": {"NoncurrentDays": 30},
      "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}
    },
    {
      "ID": "legacy-raw-multipart-cleanup",
      "Filter": {"Prefix": "raw/"},
      "Status": "Enabled",
      "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}
    }
  ]
}
JSON
aws s3api put-bucket-lifecycle-configuration --bucket "${BUCKET}" --lifecycle-configuration "file://${LIFECYCLE_JSON}"
rm -f "${LIFECYCLE_JSON}"

# ──────────────────────────────────────────────────────────────────────
# Glue Data Catalog database
# ──────────────────────────────────────────────────────────────────────
echo "==> Glue database: ${GLUE_DB}"
if aws glue get-database --name "${GLUE_DB}" --region "${REGION}" >/dev/null 2>&1; then
  echo "    exists"
else
  echo "    creating"
  aws glue create-database --region "${REGION}" --database-input "{
    \"Name\": \"${GLUE_DB}\",
    \"Description\": \"StockAlert Iceberg catalog (bronze/silver/gold).\"
  }"
fi

# ──────────────────────────────────────────────────────────────────────
# Done
# ──────────────────────────────────────────────────────────────────────
echo
echo "==> Provisioning complete."
echo "    Iceberg warehouse: s3://${BUCKET}/${WAREHOUSE_PREFIX}/"
echo "    Glue database:     ${GLUE_DB}"
echo
echo "Next step: run the connectivity gate test"
echo "    poetry run pytest tests/integration/test_iceberg_connectivity.py -v"
