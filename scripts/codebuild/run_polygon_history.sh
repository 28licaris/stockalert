#!/usr/bin/env bash
# Composes args + runs scripts/polygon_history_backfill.py from CodeBuild.
#
# Env vars (set in buildspec_polygon_history.yml or overridden via
# --environment-variables-override on `aws codebuild start-build`):
#   BACKFILL_SINCE        ISO date (default 2006-01-01)
#   BACKFILL_UNTIL        ISO date (empty = yesterday-ET)
#   BACKFILL_CONCURRENCY  parallel days in flight (default 4)
#
# The underlying script is idempotent: it pre-scans the target table
# for already-loaded trading days and skips them. Safe to re-run on
# timeout or partial failure.
set -euo pipefail
set -o pipefail

ARGS=("--since" "${BACKFILL_SINCE:-2006-01-01}")
if [ -n "${BACKFILL_UNTIL:-}" ]; then
    ARGS+=("--until" "$BACKFILL_UNTIL")
fi
ARGS+=("--concurrency" "${BACKFILL_CONCURRENCY:-4}")

echo "Running: poetry run python scripts/polygon_history_backfill.py ${ARGS[*]}"
echo "Wall-time estimate: 3-7 hours depending on how many days are already loaded."
echo "─── BACKFILL START $(date -u +'%Y-%m-%dT%H:%M:%SZ') ───"

poetry run python scripts/polygon_history_backfill.py "${ARGS[@]}"

echo "─── BACKFILL END   $(date -u +'%Y-%m-%dT%H:%M:%SZ') ───"
