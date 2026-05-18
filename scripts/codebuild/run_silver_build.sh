#!/usr/bin/env bash
# Composes args + runs scripts/run_silver_ohlcv_build.py from CodeBuild.
#
# Called by scripts/codebuild/buildspec.yml's `build` phase. Lives in
# its own file because CodeBuild's YAML parser doesn't tolerate
# multi-line literal blocks with shell heredoc syntax.
#
# Env vars (set in buildspec.yml or overridden via
# --environment-variables-override on `aws codebuild start-build`):
#   SILVER_BUILD_MODE     "full" | "nightly" | ""  (empty = use --since/--until)
#   SILVER_BUILD_SYMBOLS  "active" | "seed" | "AAPL,NVDA,..."
#   SILVER_BUILD_SINCE    ISO date (optional)
#   SILVER_BUILD_UNTIL    ISO date (optional)
set -euo pipefail

ARGS=()

case "${SILVER_BUILD_MODE:-}" in
    full)    ARGS+=("--full") ;;
    nightly) ARGS+=("--nightly") ;;
    "")      ;; # explicit window via --since/--until below
    *)       echo "ERROR: unknown SILVER_BUILD_MODE='$SILVER_BUILD_MODE'" >&2; exit 1 ;;
esac

if [ -n "${SILVER_BUILD_SINCE:-}" ]; then
    ARGS+=("--since" "$SILVER_BUILD_SINCE")
fi
if [ -n "${SILVER_BUILD_UNTIL:-}" ]; then
    ARGS+=("--until" "$SILVER_BUILD_UNTIL")
fi

ARGS+=("--symbols" "${SILVER_BUILD_SYMBOLS:-active}")
ARGS+=("--out-json" "/tmp/silver_run/result.json")

echo "Running: poetry run python scripts/run_silver_ohlcv_build.py ${ARGS[*]}"
poetry run python scripts/run_silver_ohlcv_build.py "${ARGS[@]}"
