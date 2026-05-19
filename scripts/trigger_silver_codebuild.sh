#!/usr/bin/env bash
# Trigger the silver-build CodeBuild project with env-var overrides.
#
# Wraps the `aws codebuild start-build` incantation so operators don't
# have to remember the exact env-var-override syntax. Prints the build
# ID + a one-liner to watch progress.
#
# Usage:
#   scripts/trigger_silver_codebuild.sh [options]
#
# Options:
#   --symbols VALUE       Symbol spec: seed | active | "AAPL,NVDA,..."  (default: seed)
#   --since YYYY-MM-DD    Lower bound  (default: 2021-01-04 = BRONZE_HISTORY_START)
#   --until YYYY-MM-DD    Upper bound  (default: empty = yesterday)
#   --mode VALUE          full | nightly | empty (default: empty = use since/until)
#   --watch               After triggering, invoke watch_silver_codebuild.sh on the build
#   --aws-profile NAME    AWS profile (default: stock-lake)
#   --region REGION       AWS region (default: us-east-1)
#   --project NAME        CodeBuild project (default: sockalert-silver-full-backfill)
#   --help                Print this help
#
# Examples:
#   # Default: seed universe, since BRONZE_HISTORY_START, until yesterday
#   scripts/trigger_silver_codebuild.sh
#
#   # Custom: a specific symbol slice, narrow window, watch to completion
#   scripts/trigger_silver_codebuild.sh \
#       --symbols "AAPL,NVDA,TSLA" --since 2024-01-01 --until 2024-06-30 --watch
#
#   # Whole-market (TA-5.6 — when ready)
#   scripts/trigger_silver_codebuild.sh --symbols active --watch
#
# Coding standards (docs/standards/coding.md):
#   - Rule 1A: pipefail enabled so a silent SIGPIPE doesn't mask aws failures.
#   - Rule 1B: all outcomes logged (success AND failure paths).
#   - Rule 1F: no bare exception swallows.

set -o pipefail
set -u

# Defaults
SYMBOLS="seed"
SINCE="2021-01-04"
UNTIL=""
MODE=""
WATCH=0
AWS_PROFILE_NAME="stock-lake"
REGION="us-east-1"
PROJECT="sockalert-silver-full-backfill"

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --symbols)     SYMBOLS="$2"; shift 2 ;;
        --since)       SINCE="$2"; shift 2 ;;
        --until)       UNTIL="$2"; shift 2 ;;
        --mode)        MODE="$2"; shift 2 ;;
        --watch)       WATCH=1; shift ;;
        --aws-profile) AWS_PROFILE_NAME="$2"; shift 2 ;;
        --region)      REGION="$2"; shift 2 ;;
        --project)     PROJECT="$2"; shift 2 ;;
        --help|-h)
            # Print the header comment block as the help text
            sed -n '2,/^$/p' "$0" | sed 's/^# //; s/^#//'
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            echo "Run with --help for usage." >&2
            exit 2
            ;;
    esac
done

# Sanity-check the AWS CLI is available + reachable
if ! command -v aws >/dev/null 2>&1; then
    echo "ERROR: aws CLI not found in PATH" >&2
    exit 2
fi

echo "─── silver-build CodeBuild trigger ───"
echo "  project:       $PROJECT"
echo "  profile:       $AWS_PROFILE_NAME"
echo "  region:        $REGION"
echo "  symbols:       $SYMBOLS"
echo "  since:         $SINCE"
echo "  until:         ${UNTIL:-<yesterday>}"
echo "  mode:          ${MODE:-<since/until>}"

# Confirm project is reachable (BatchGetProjects is in our IAM policy)
PROJECT_NAME=$(aws --profile "$AWS_PROFILE_NAME" --region "$REGION" \
    codebuild batch-get-projects --names "$PROJECT" \
    --query 'projects[0].name' --output text 2>&1)

if [[ "$PROJECT_NAME" != "$PROJECT" ]]; then
    echo "ERROR: could not reach project '$PROJECT'." >&2
    echo "  aws output: $PROJECT_NAME" >&2
    exit 2
fi

# Build the env-var override array.
ENV_OVERRIDES=(
    "name=SILVER_BUILD_SINCE,value=${SINCE},type=PLAINTEXT"
    "name=SILVER_BUILD_UNTIL,value=${UNTIL},type=PLAINTEXT"
    "name=SILVER_BUILD_MODE,value=${MODE},type=PLAINTEXT"
    "name=SILVER_BUILD_SYMBOLS,value=${SYMBOLS},type=PLAINTEXT"
)

# Trigger.
echo
echo "Triggering build..."
BUILD_ID=$(aws --profile "$AWS_PROFILE_NAME" --region "$REGION" \
    codebuild start-build \
    --project-name "$PROJECT" \
    --environment-variables-override "${ENV_OVERRIDES[@]}" \
    --query 'build.id' --output text)

if [[ -z "$BUILD_ID" || "$BUILD_ID" == "None" ]]; then
    echo "ERROR: start-build returned no build ID" >&2
    exit 2
fi

echo
echo "✅ Build started"
echo "   build_id:    $BUILD_ID"
echo "   console:     https://${REGION}.console.aws.amazon.com/codesuite/codebuild/projects/${PROJECT}/build/${BUILD_ID//:/%3A}"
echo "   watch:       scripts/watch_silver_codebuild.sh '$BUILD_ID'"
echo

if [[ "$WATCH" -eq 1 ]]; then
    WATCHER="$(dirname "$0")/watch_silver_codebuild.sh"
    if [[ ! -x "$WATCHER" ]]; then
        echo "ERROR: watcher not found or not executable at $WATCHER" >&2
        exit 2
    fi
    echo "─── handing off to watcher ───"
    exec "$WATCHER" "$BUILD_ID" \
        --aws-profile "$AWS_PROFILE_NAME" \
        --region "$REGION" \
        --project "$PROJECT"
fi
