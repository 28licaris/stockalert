#!/usr/bin/env bash
# Quick ClickHouse spot-checks for live data ingestion.
#
# Usage:
#   ./scripts/ch_verify.sh                  # default checks (all queries)
#   ./scripts/ch_verify.sh source           # show per-source ingestion summary
#   ./scripts/ch_verify.sh recent           # last 30 min of polygon-tagged bars
#   ./scripts/ch_verify.sh rate             # bars/minute by source over last 30 min
#   ./scripts/ch_verify.sh symbols          # latest timestamp per symbol (polygon only)
#   ./scripts/ch_verify.sh symbol IWM       # per-symbol source breakdown (any provider mix)
#   ./scripts/ch_verify.sh gaps IWM         # remaining within-session gaps for one symbol (24h)
#   ./scripts/ch_verify.sh all              # everything (same as no args)
#
# Override defaults via env vars:
#   CH_URL=http://127.0.0.1:8123  CH_DB=stocks  SOURCE=polygon  WINDOW_MIN=30
set -euo pipefail

CH_URL="${CH_URL:-http://127.0.0.1:8123}"
CH_DB="${CH_DB:-stocks}"
SOURCE="${SOURCE:-polygon}"
WINDOW_MIN="${WINDOW_MIN:-30}"

# url-encoded query helper.
encode() { python3 -c "import sys, urllib.parse; print(urllib.parse.quote(sys.stdin.read()))"; }

run() {
  local title="$1"
  local sql="$2"
  echo "=== $title ==="
  # Send the database as URL query and the SQL as the POST body. ClickHouse
  # accepts both GET (with ?query=...) and POST (body), but mixing
  # `?database=...` with `--data-urlencode query=...` can confuse the URL
  # parser; passing the SQL as the *raw* body sidesteps that.
  curl --silent --show-error --fail \
    --data-binary "$sql FORMAT PrettyCompactMonoBlock" \
    "$CH_URL/?database=$CH_DB" || true
  echo
}

ingestion_by_source() {
  run "Ingestion by source (all-time)" \
    "SELECT source, count() AS bars, uniq(symbol) AS symbols,
            min(timestamp) AS first, max(timestamp) AS latest
     FROM ohlcv_1m
     GROUP BY source
     ORDER BY latest DESC"
}

recent_polygon_bars() {
  run "Recent ${SOURCE} bars (last ${WINDOW_MIN} min)" \
    "SELECT symbol, timestamp, round(close, 2) AS close,
            toUInt32(volume) AS vol, source
     FROM ohlcv_1m
     WHERE source = '${SOURCE}' AND timestamp > now() - INTERVAL ${WINDOW_MIN} MINUTE
     ORDER BY timestamp DESC
     LIMIT 20"
}

per_minute_rate() {
  run "Bars/minute by source (last ${WINDOW_MIN} min)" \
    "SELECT source, toStartOfMinute(timestamp) AS minute,
            count() AS bars, uniq(symbol) AS symbols
     FROM ohlcv_1m
     WHERE timestamp > now() - INTERVAL ${WINDOW_MIN} MINUTE
     GROUP BY source, minute
     ORDER BY minute DESC, source"
}

per_symbol_latest() {
  run "${SOURCE}: latest timestamp per symbol" \
    "SELECT symbol, max(timestamp) AS latest, count() AS bars
     FROM ohlcv_1m
     WHERE source = '${SOURCE}'
     GROUP BY symbol
     ORDER BY latest DESC"
}

symbol_source_mix() {
  # Per-symbol provider provenance: how many bars came from each provider,
  # the first/last timestamps tagged with that provider, and the latest close.
  # Answers: "Is my IWM chart a mix of schwab + polygon, and when did each
  # provider write its last bar?"
  local sym="$1"
  run "Source mix for ${sym}" \
    "SELECT source, count() AS bars,
            min(timestamp) AS first_seen,
            max(timestamp) AS last_seen,
            round(argMax(close, timestamp), 2) AS latest_close
     FROM ohlcv_1m
     WHERE symbol = '${sym}'
     GROUP BY source
     ORDER BY last_seen DESC"
}

remaining_gaps_for_symbol() {
  # Show the (still-unfilled) within-session gaps for one symbol in the last
  # 24h. Uses the same lagInFrame window pattern as `find_intraday_gaps`,
  # capped at the 4h overnight boundary. Use this after running gap-fill to
  # spot any hole the provider couldn't deliver.
  local sym="$1"
  run "Remaining within-session gaps for ${sym} (last 24h)" \
    "SELECT prev_ts, ts AS next_ts,
            dateDiff('minute', prev_ts, ts) - 1 AS missing_bars
     FROM (
        SELECT timestamp AS ts,
               lagInFrame(timestamp, 1) OVER (ORDER BY timestamp ASC) AS prev_ts
        FROM ohlcv_1m FINAL
        WHERE symbol = '${sym}'
          AND timestamp >= now() - INTERVAL 24 HOUR
     )
     WHERE prev_ts != toDateTime64(0, 3, 'UTC')
       AND dateDiff('minute', prev_ts, ts) > 1
       AND dateDiff('minute', prev_ts, ts) < 240
     ORDER BY prev_ts ASC
     LIMIT 50"
}

case "${1:-all}" in
  source)   ingestion_by_source ;;
  recent)   recent_polygon_bars ;;
  rate)     per_minute_rate ;;
  symbols)  per_symbol_latest ;;
  symbol)
    [[ -z "${2:-}" ]] && { echo "Usage: $0 symbol <TICKER>"; exit 2; }
    symbol_source_mix "${2^^}"
    ;;
  gaps)
    [[ -z "${2:-}" ]] && { echo "Usage: $0 gaps <TICKER>"; exit 2; }
    remaining_gaps_for_symbol "${2^^}"
    ;;
  all|"")
    ingestion_by_source
    recent_polygon_bars
    per_minute_rate
    per_symbol_latest
    ;;
  *)
    echo "Unknown subcommand: $1"
    echo "Run with no args, or one of: source|recent|rate|symbols|symbol|gaps|all"
    exit 2
    ;;
esac
