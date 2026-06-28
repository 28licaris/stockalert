#!/usr/bin/env bash
#
# upgrade_polygon_depth.sh — orchestrate the post-Polygon-subscription-upgrade
# rebuild: extend polygon_raw with new years, then rebuild CH from the expanded
# lake so charts see the new depth. (Adjustment is computed at read time, so
# there is no separate materialization step.)
#
# Trigger this AFTER Polygon has finished syncing the new historical years
# to your S3 bucket. The script verifies S3 has the requested years before
# touching anything else.
#
# Wall time for 15 added years (2003-2020 example): ~2-4 hours, broken down:
#   - Athena incremental import:  ~30 min
#   - CH rebuild from lake:        ~1-3 hours (network-bound)
#
# Live tier keeps serving the EXISTING window throughout — no downtime.
#
# Usage:
#   scripts/aws/upgrade_polygon_depth.sh 2003,2004,2005,2006,2007,2008,2009,2010,2011,2012,2013,2014,2015,2016,2017,2018,2019,2020
#   scripts/aws/upgrade_polygon_depth.sh 2018,2019,2020   # smaller window
#
# Prerequisites:
#   - AWS_PROFILE set (stockalert-admin for IAM-touching steps;
#     stock-lake for Athena/S3/EMR runtime).
#   - Polygon's S3 sync for the requested years has COMPLETED — the
#     pre-flight verifies year=YYYY/ exists in the raw cache.
#   - polygon_raw exists and has the current data (we extend, not replace).
#
set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────
LAKE_BUCKET="${STOCK_LAKE_BUCKET:-stock-lake-562741918372-us-east-1-an}"
AWS_REGION="${AWS_REGION:-us-east-1}"
HOTLOAD_PARALLELISM="${HOTLOAD_PARALLELISM:-8}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"

# Color helpers
if [[ -t 1 ]]; then
  C_OK="\033[32m"; C_INFO="\033[34m"; C_WARN="\033[33m"; C_END="\033[0m"
else
  C_OK=""; C_INFO=""; C_WARN=""; C_END=""
fi
log()  { printf "${C_INFO}== %s${C_END}\n" "$*"; }
ok()   { printf "${C_OK}OK${C_END}  %s\n" "$*"; }
warn() { printf "${C_WARN}!!${C_END}  %s\n" "$*"; }
die()  { printf "ERROR: %s\n" "$*" >&2; exit 1; }

YEARS_ARG="${1:-}"
[[ -z "${YEARS_ARG}" ]] && die "Usage: $0 YEAR1,YEAR2,YEAR3,...  (e.g. 2003,2004,...,2020)"

# Normalize: comma → space, dedupe, sort
YEARS=$(echo "${YEARS_ARG}" | tr ',' '\n' | sort -un | xargs)
[[ -z "${YEARS}" ]] && die "no valid years parsed from '${YEARS_ARG}'"

log "Plan: extend polygon depth with years: ${YEARS}"

# ─────────────────────────────────────────────────────────────────────────
# Preflight 1: verify S3 has each requested year
# ─────────────────────────────────────────────────────────────────────────
log "Preflight 1: S3 cache check"
for y in ${YEARS}; do
  prefix="raw/provider=polygon-flatfiles/kind=minute/year=${y}/"
  if ! aws s3 ls "s3://${LAKE_BUCKET}/${prefix}" --region "${AWS_REGION}" >/dev/null 2>&1; then
    die "year=${y} not found at s3://${LAKE_BUCKET}/${prefix}.  Has Polygon finished syncing? Wait + retry."
  fi
  n_files=$(aws s3 ls "s3://${LAKE_BUCKET}/${prefix}" --region "${AWS_REGION}" | wc -l | xargs)
  printf "  year=%d : %s files in S3\n" "${y}" "${n_files}"
done
ok "All requested years present in S3"

# ─────────────────────────────────────────────────────────────────────────
# Preflight 2: confirm polygon_raw exists (we extend, not recreate)
# ─────────────────────────────────────────────────────────────────────────
log "Preflight 2: polygon_raw exists check"
pre_rows=$(
  cd "${ROOT}" && poetry run python -c "
from app.services.iceberg_catalog import get_catalog
from app.services.equities.schemas import equities_table_id
t = get_catalog().load_table(equities_table_id('polygon_raw'))
snap = t.current_snapshot()
print(int(snap.summary.additional_properties.get('total-records', 0)) if snap else 0)
"
)
[[ "${pre_rows}" -gt 0 ]] || die "polygon_raw is empty — run lake_import_athena.py WITHOUT --incremental for first-time import"
ok "polygon_raw current row count: ${pre_rows}"

# ─────────────────────────────────────────────────────────────────────────
# Confirm with operator
# ─────────────────────────────────────────────────────────────────────────
cat <<EOF

================================================================
  POLYGON DEPTH EXTENSION PLAN
================================================================

  Years to add:           ${YEARS}
  Current polygon_raw:    ${pre_rows} rows
  Will run:
    1. Athena incremental INSERT (~30 min, ~\$0.20 in scan fees)
    2. Spark adjustment whole-market re-run (~2 hours, ~\$2-3 EMR)
    3. CH hot-load (~1-3 hours, network-bound, \$0)

  Total wall:  ~3-5 hours
  Total cost:  ~\$2-3 AWS

  Live tier keeps serving existing data throughout. No downtime.

EOF
read -p "Proceed? (yes/no): " ans
[[ "${ans}" == "yes" ]] || die "aborted by operator"

# ─────────────────────────────────────────────────────────────────────────
# Step 1: Athena incremental
# ─────────────────────────────────────────────────────────────────────────
log "Step 1/3: Athena incremental import"
year_csv=$(echo "${YEARS}" | tr ' ' ',')
(
  cd "${ROOT}" && \
  poetry run python scripts/lake_import_athena.py \
    --incremental --years "${year_csv}"
)
ok "Athena incremental done"

post_athena_rows=$(
  cd "${ROOT}" && poetry run python -c "
from app.services.iceberg_catalog import get_catalog
from app.services.equities.schemas import equities_table_id
t = get_catalog().load_table(equities_table_id('polygon_raw'))
snap = t.current_snapshot()
print(int(snap.summary.additional_properties.get('total-records', 0)) if snap else 0)
"
)
delta=$(( post_athena_rows - pre_rows ))
[[ "${delta}" -gt 0 ]] || die "polygon_raw did not grow (was ${pre_rows}, now ${post_athena_rows}). Aborting."
ok "polygon_raw now ${post_athena_rows} rows (+${delta})"

# (Step 2 — the whole-market Spark adjustment — was RETIRED in v2: split
#  adjustment is computed at READ time from polygon_raw + market_splits, so
#  extending polygon_raw needs no re-materialization step.)

# ─────────────────────────────────────────────────────────────────────────
# Step 2: CH rebuild from the expanded lake (read_arrow union, read-time adjusted)
# ─────────────────────────────────────────────────────────────────────────
log "Step 2/2: CH rebuild from expanded lake"
(
  cd "${ROOT}" && \
  poetry run python scripts/rebuild_ch_from_lake.py \
    --symbols active --wipe \
    --parallelism "${HOTLOAD_PARALLELISM}"
)
ok "CH rebuild done"

# ─────────────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────────────
cat <<EOF

================================================================
  ✅ POLYGON DEPTH EXTENSION COMPLETE
================================================================

  Years added:           ${YEARS}
  polygon_raw rows:      ${pre_rows} → ${post_athena_rows} (+${delta})

  Verification:
    scripts/spot_check_polygon_adjusted.py    # math + parity
    scripts/validate_pipeline.py              # end-to-end live-tier

EOF
