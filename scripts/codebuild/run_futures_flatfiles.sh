#!/usr/bin/env bash
# Runs scripts/polygon_futures_flatfiles_backfill.py for each exchange group.
#
# Exchange groups run SEQUENTIALLY to avoid concurrent Iceberg metadata
# updates to the same table (Glue optimistic locking is safe but noisy
# under high contention). Each group writes to futures.polygon_futures;
# full 10-year run of all 4 groups completes in ~3-5 hours on CodeBuild.
#
# Env vars (set in buildspec or overridden via --environment-variables-override):
#   BACKFILL_START_YEAR    default 2017 (first year in Polygon flat files)
#   BACKFILL_END_YEAR      default 2026
#   BACKFILL_BATCH_SIZE    default 10000 (Iceberg append batch size in rows)
#   BACKFILL_DRY_RUN       "true" → count bars but do not write
#   BACKFILL_EXCHANGES     comma-separated list of groups to run (default: all)
#                          valid values: cme, comex, cbot, nymex
set -euo pipefail

SCRIPT="scripts/polygon_futures_flatfiles_backfill.py"

# Common args forwarded to every invocation.
COMMON_ARGS=(
    "--start-year" "${BACKFILL_START_YEAR:-2017}"
    "--end-year"   "${BACKFILL_END_YEAR:-2026}"
    "--batch-size" "${BACKFILL_BATCH_SIZE:-10000}"
)
if [ "${BACKFILL_DRY_RUN:-}" = "true" ]; then
    COMMON_ARGS+=("--dry-run")
fi

# Parse the exchange list so individual groups can be skipped.
IFS=',' read -ra EXCHANGES <<< "${BACKFILL_EXCHANGES:-cme,comex,cbot,nymex}"

should_run() {
    local target="$1"
    for ex in "${EXCHANGES[@]}"; do
        if [ "$(echo "$ex" | tr '[:upper:]' '[:lower:]')" = "$target" ]; then
            return 0
        fi
    done
    return 1
}

echo "─── FUTURES FLAT-FILES BACKFILL START $(date -u +'%Y-%m-%dT%H:%M:%SZ') ───"
echo "    years     : ${BACKFILL_START_YEAR:-2017}–${BACKFILL_END_YEAR:-2026}"
echo "    exchanges : ${BACKFILL_EXCHANGES:-cme,comex,cbot,nymex}"
echo "    dry-run   : ${BACKFILL_DRY_RUN:-(no)}"

# ── CME — equity index + FX futures (14 roots) ────────────────────────────────
if should_run "cme"; then
    echo ""
    echo "════ CME (equity index + FX) ════"
    echo "    roots: ES MES NQ MNQ YM MYM RTY M2K 6E 6J 6B 6A 6C 6S"
    poetry run python "$SCRIPT" \
        --root ES MES NQ MNQ YM MYM RTY M2K 6E 6J 6B 6A 6C 6S \
        "${COMMON_ARGS[@]}"
else
    echo "Skipping CME (not in BACKFILL_EXCHANGES)"
fi

# ── COMEX — metals (7 roots) ──────────────────────────────────────────────────
if should_run "comex"; then
    echo ""
    echo "════ COMEX (metals) ════"
    echo "    roots: GC MGC SI SIL HG PL PA"
    poetry run python "$SCRIPT" \
        --root GC MGC SI SIL HG PL PA \
        "${COMMON_ARGS[@]}"
else
    echo "Skipping COMEX (not in BACKFILL_EXCHANGES)"
fi

# ── CBOT — rates + grains (10 roots) ─────────────────────────────────────────
if should_run "cbot"; then
    echo ""
    echo "════ CBOT (rates + grains) ════"
    echo "    roots: ZB UB ZN ZF ZT ZC ZS ZW ZM ZL"
    poetry run python "$SCRIPT" \
        --root ZB UB ZN ZF ZT ZC ZS ZW ZM ZL \
        "${COMMON_ARGS[@]}"
else
    echo "Skipping CBOT (not in BACKFILL_EXCHANGES)"
fi

# ── NYMEX — energy (6 roots) ──────────────────────────────────────────────────
if should_run "nymex"; then
    echo ""
    echo "════ NYMEX (energy) ════"
    echo "    roots: CL MCL NG RB HO BZ"
    poetry run python "$SCRIPT" \
        --root CL MCL NG RB HO BZ \
        "${COMMON_ARGS[@]}"
else
    echo "Skipping NYMEX (not in BACKFILL_EXCHANGES)"
fi

echo ""
echo "─── FUTURES FLAT-FILES BACKFILL END $(date -u +'%Y-%m-%dT%H:%M:%SZ') ───"
