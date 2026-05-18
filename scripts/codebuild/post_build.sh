#!/usr/bin/env bash
# Uploads the silver build's result.json to S3 + prints a summary.
#
# Called by scripts/codebuild/buildspec.yml's `post_build` phase.
# Lives in its own file because CodeBuild's YAML parser doesn't
# tolerate multi-line literal blocks with shell heredoc syntax.
#
# Required env (set by buildspec):
#   STOCK_LAKE_BUCKET     S3 bucket for the report
#   CODEBUILD_BUILD_ID    auto-set by CodeBuild (used in S3 key)

set -uo pipefail   # NOT -e: post_build should still finish even on minor errors

RESULT_FILE="/tmp/silver_run/result.json"

if [ ! -f "$RESULT_FILE" ]; then
    echo "WARNING: $RESULT_FILE not produced. See logs above for the build failure."
    exit 0
fi

REPORT_KEY="silver_build_reports/codebuild-${CODEBUILD_BUILD_ID}.json"
aws s3 cp "$RESULT_FILE" "s3://${STOCK_LAKE_BUCKET}/${REPORT_KEY}"
echo "Report:          s3://${STOCK_LAKE_BUCKET}/${REPORT_KEY}"
echo "--- summary ---"

if command -v jq >/dev/null 2>&1; then
    jq '{status, mode, since, until, symbols_count, result: {slices, slices_succeeded, slices_failed, silver_rows, duration_seconds}}' "$RESULT_FILE"
else
    cat "$RESULT_FILE"
fi
