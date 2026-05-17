"""
MCP tool — universe scanner.

Thin adapter over `Screener.scan`. Identical Pydantic contract as
the HTTP route — agents get the same `ScreenerResult` shape the
dashboard renders.

Closes the swing-trader pipeline:
    list_bronze_symbols / get_watchlist  →  scan_universe  →  candidates
    →  run_backtest (per candidate) or get_chart_data (deeper analysis)
"""
from __future__ import annotations

import logging
from functools import lru_cache

from app.mcp.middleware import tool_call
from app.mcp.server import mcp
from app.services.screener.schemas import ScreenerResult, ScreenerSpec
from app.services.screener.screener import Screener

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _screener() -> Screener:
    return Screener.from_settings()


@mcp.tool()
def scan_universe(spec: ScreenerSpec) -> ScreenerResult:
    """Scan a universe of symbols with a declarative filter spec.

    USE WHEN: an agent wants to narrow 1000s of symbols to a short
    list of candidates before running expensive deep analysis
    (charts, backtests, LLM reasoning). The "fast filter" step.

    Args:
        spec: `ScreenerSpec` with:
            - `universe`: list of tickers, OR `watchlist_name` to
              resolve from the watchlist service.
            - `interval`: '1d' (most common for screening) | '1m' etc.
            - `lookback_bars`: window size. Must be >= the slowest
              indicator period your rules use.
            - `rules`: list of `ScreenerRule` (kind + params). All
              rules combine via logical AND.
            - `rank_by`: 'volume', 'atr_pct', 'rsi', 'rsi_desc', 'none'.
            - `limit`: max candidates returned.

    Supported rule kinds:
      Trend       — close_above_sma, close_below_sma, close_above_ema,
                    close_below_ema  (params: period)
      Momentum    — rsi_above, rsi_below  (params: period, threshold)
      Volatility  — atr_pct_above, atr_pct_below
                    (params: period, threshold; threshold is a
                     fraction, e.g. 0.02 = 2% ATR/price)
      Envelope    — close_at_lower_band, close_at_upper_band
                    (params: period, std_multiplier)
      Absolute    — price_above, price_below  (params: value)
                    volume_above  (params: value)

    Returns:
        `ScreenerResult` — ranked `candidates`, plus `universe_size`,
        `n_passed`, `rejected_count`, `errors` for transparency.
        `snapshot_id` is pinned when reading bronze (1m interval).

    Cost: bar fetch dominates. ~50-200ms per symbol for a 250-bar
    daily window from CH. 100 symbols = ~10-20s. For frequent
    scans, narrow your universe via a watchlist.

    Errors:
      - Unknown rule kind / missing rule params -> ToolError (400 in HTTP).
      - Per-symbol failures (missing data, indicator errors) DON'T
        kill the scan — they land in `errors[]` and the scan completes.
    """
    with tool_call(
        "scan_universe",
        n_rules=len(spec.rules), interval=spec.interval,
        universe_size=len(spec.universe),
        watchlist=spec.watchlist_name,
    ):
        return _screener().scan(spec)
