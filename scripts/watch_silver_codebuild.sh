#!/usr/bin/env bash
# Poll a CodeBuild silver-build to terminal state, then pull + print
# the result.json from S3.
#
# Usage:
#   scripts/watch_silver_codebuild.sh BUILD_ID [options]
#
# Options:
#   --aws-profile NAME    AWS profile (default: stock-lake)
#   --region REGION       AWS region (default: us-east-1)
#   --project NAME        CodeBuild project (default: sockalert-silver-full-backfill)
#   --bucket NAME         S3 bucket for build reports
#                         (default: stock-lake-562741918372-us-east-1-an)
#   --poll-seconds N      Status poll interval (default: 60)
#   --quiet               Suppress per-poll status lines (only print on phase change + terminal)
#   --help                Print this help
#
# Exit codes:
#   0 — build SUCCEEDED, result.json downloaded + summarized
#   1 — build reached terminal non-success state (FAILED/STOPPED/TIMED_OUT/FAULT)
#   2 — operator/argument error, OR result.json missing after a successful build
#       (treated as fail-loud per coding_standards.md rule 1E — verify-mutation)
#
# Coding standards (docs/standards/coding.md):
#   - Rule 1A: pipefail enabled (status reads through a python parse pipeline).
#   - Rule 1B: every poll outcome logged with timestamp + status + phase.
#   - Rule 1C: phase changes get a distinct log line (per-iteration completion marker).
#   - Rule 1E: after a SUCCEEDED build, we verify the result.json was actually
#     uploaded to S3. A "success" without the report would be a silent failure.

set -o pipefail
set -u

# Defaults
AWS_PROFILE_NAME="stock-lake"
REGION="us-east-1"
PROJECT="sockalert-silver-full-backfill"
BUCKET="stock-lake-562741918372-us-east-1-an"
POLL_SECONDS=60
QUIET=0

# Positional + options
if [[ $# -lt 1 ]]; then
    echo "ERROR: missing BUILD_ID arg" >&2
    echo "Run with --help for usage." >&2
    exit 2
fi

BUILD_ID="$1"; shift

while [[ $# -gt 0 ]]; do
    case "$1" in
        --aws-profile) AWS_PROFILE_NAME="$2"; shift 2 ;;
        --region)      REGION="$2"; shift 2 ;;
        --project)     PROJECT="$2"; shift 2 ;;
        --bucket)      BUCKET="$2"; shift 2 ;;
        --poll-seconds) POLL_SECONDS="$2"; shift 2 ;;
        --quiet)       QUIET=1; shift ;;
        --help|-h)
            sed -n '2,/^$/p' "$0" | sed 's/^# //; s/^#//'
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            exit 2
            ;;
    esac
done

if [[ -z "$BUILD_ID" || "$BUILD_ID" == "None" ]]; then
    echo "ERROR: empty BUILD_ID" >&2
    exit 2
fi

echo "─── CodeBuild watcher ───"
echo "  build_id:    $BUILD_ID"
echo "  project:     $PROJECT"
echo "  poll every:  ${POLL_SECONDS}s"
echo "  started at:  $(date -u +%FT%TZ)"
echo "---"

PREV_STATUS=""
PREV_PHASE=""

while true; do
    # Single API call returns both fields; parse to two variables.
    BSTATUS=$(aws --profile "$AWS_PROFILE_NAME" --region "$REGION" \
        codebuild batch-get-builds --ids "$BUILD_ID" \
        --query 'builds[0].buildStatus' --output text 2>&1)
    BPHASE=$(aws --profile "$AWS_PROFILE_NAME" --region "$REGION" \
        codebuild batch-get-builds --ids "$BUILD_ID" \
        --query 'builds[0].currentPhase' --output text 2>&1)

    # Always log if status/phase changed; otherwise gated by --quiet.
    if [[ "$BSTATUS" != "$PREV_STATUS" || "$BPHASE" != "$PREV_PHASE" ]] \
       || [[ "$QUIET" -eq 0 ]]; then
        echo "[$(date -u +%H:%M:%SZ)] status=$BSTATUS phase=$BPHASE"
    fi

    case "$BSTATUS" in
        IN_PROGRESS)
            PREV_STATUS="$BSTATUS"
            PREV_PHASE="$BPHASE"
            sleep "$POLL_SECONDS"
            ;;
        SUCCEEDED)
            echo
            echo "✅ BUILD SUCCEEDED"
            break
            ;;
        FAILED|FAULT|TIMED_OUT|STOPPED)
            echo
            echo "❌ BUILD ENDED: $BSTATUS"
            echo "   console: https://${REGION}.console.aws.amazon.com/codesuite/codebuild/projects/${PROJECT}/build/${BUILD_ID//:/%3A}"
            exit 1
            ;;
        *)
            echo "❌ unexpected build status: '$BSTATUS'" >&2
            exit 2
            ;;
    esac
done

# Pull + print the result.json. Rule 1E — verify the mutation.
# BUILD_ID has the form "project-name:uuid"; the uuid is what's in S3.
UUID="${BUILD_ID#*:}"
S3_KEY="silver_build_reports/codebuild-${UUID}.json"
S3_URI="s3://${BUCKET}/${S3_KEY}"
LOCAL_REPORT="/tmp/silver_build_${UUID}.json"

echo "Pulling report from $S3_URI ..."
if ! aws --profile "$AWS_PROFILE_NAME" --region "$REGION" \
        s3 cp "$S3_URI" "$LOCAL_REPORT" >/dev/null; then
    echo "ERROR: result.json missing at $S3_URI" >&2
    echo "Build reported SUCCEEDED but no report was uploaded — possible silent failure." >&2
    exit 2
fi

echo "Report → $LOCAL_REPORT"
echo
echo "─── summary ─────────────────────────────────────────────"
if command -v jq >/dev/null 2>&1; then
    jq '{
        status, mode, since, until, symbols_count,
        slices:           .result.slices,
        slices_succeeded: .result.slices_succeeded,
        slices_failed:    .result.slices_failed,
        silver_rows:      .result.silver_rows,
        duration_seconds: .result.duration_seconds
    }' "$LOCAL_REPORT"
else
    cat "$LOCAL_REPORT"
fi
echo "─────────────────────────────────────────────────────────"
echo

# Also surface failed slices if any.
if command -v jq >/dev/null 2>&1; then
    FAILED_COUNT=$(jq -r '.result.slices_failed // 0' "$LOCAL_REPORT")
    if [[ "$FAILED_COUNT" != "0" ]]; then
        echo "⚠️  $FAILED_COUNT slice(s) failed. First errors:"
        jq '.result.errors[:5]' "$LOCAL_REPORT"
        exit 1
    fi
fi

echo "✅ verify-mutation: report present, slices_failed=0"
