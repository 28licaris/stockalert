"""
On-demand single-symbol silver_ohlcv_build (hot-path warmup).

Per docs/standards/data/symbol_lifecycle.md the quick path for adding
a new symbol runs `SilverOhlcvBuild.build_window([sym], start, end)`
for that single symbol over the last `days` calendar days. The bronze
side is ALREADY populated because Polygon nightly is whole-market —
this just reads bronze + corp_actions + writes silver for one symbol.

Wrapper exists to:
  - Offload the heavy Iceberg/CH work to a thread when called from an
    async context (the stream service warmup runs in the main loop).
  - Provide consistent defaults (days=730, mode="month") matching the
    locked architecture's 5-year window.
  - Surface a single async entry point that the warmup chain can await.

Wall-clock cost (typical):
  - Symbol already in bronze (Polygon nightly whole-market): ~5-15s
    end-to-end on a warm Iceberg cache + local ClickHouse. Most of
    the cost is the per-month bronze scan; per-symbol scan within a
    month is cheap.
  - Symbol NOT in bronze (e.g. very new IPO with < 1 day of history):
    returns BuildResult with 0 slices — caller falls through to the
    Schwab REST tip-fill which covers ≤48 days.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.services.silver.ohlcv.build import BuildResult, SilverOhlcvBuild

logger = logging.getLogger(__name__)


# Default lookback: 5 years per docs/standards/data/symbol_lifecycle.md.
# Caller can pass a smaller window for fast tests.
DEFAULT_ON_DEMAND_DAYS = 5 * 365


async def build_one_symbol(
    symbol: str,
    *,
    days: int = DEFAULT_ON_DEMAND_DAYS,
    end_date: Optional[date] = None,
) -> BuildResult:
    """On-demand silver build for a single symbol — the hot-path warmup.

    Args:
        symbol: Ticker (case-insensitive; upper-cased internally).
        days: Lookback in calendar days. Default 5y matches the chart's
            5-year zoom requirement per symbol_lifecycle.md.
        end_date: End of the build window, exclusive. Defaults to
            yesterday (`today - 1 day`) because Polygon flat-files for
            today's date aren't published until tomorrow's nightly.
            Today's data flows into CH via the live stream + Schwab
            tip-fill paths separately.

    Returns:
        `BuildResult` with per-slice outcomes. `result.slices_succeeded`
        tells the caller how many (symbol, day) pairs landed in silver.

    Raises:
        ValueError: if `symbol` is empty after normalization.

    Errors during the build itself are NOT raised — they land on the
    individual slice results in `BuildResult.slices`. This matches the
    pattern of the nightly job: partial failures are recorded, not
    propagated, so the warmup chain's other legs (Schwab tip-fill,
    live stream) can still complete.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        raise ValueError(f"build_one_symbol: invalid symbol {symbol!r}")

    end = end_date or (datetime.now(timezone.utc).date() - timedelta(days=1))
    start = end - timedelta(days=max(1, int(days)))

    logger.info(
        "silver_on_demand: starting symbol=%s window=%s..%s (%d days)",
        sym, start, end, (end - start).days,
    )

    # Heavy synchronous work (Iceberg metadata reads, ClickHouse
    # upserts, corp_actions cache priming) → thread pool so we don't
    # block the event loop the stream service warmup chain runs on.
    result = await asyncio.to_thread(_do_build, sym, start, end)

    logger.info(
        "silver_on_demand: done symbol=%s slices=%d (ok=%d fail=%d) "
        "silver_rows=%d duration=%.1fs",
        sym,
        len(result.slices),
        result.slices_succeeded,
        result.slices_failed,
        result.total_silver_rows,
        result.duration_seconds,
    )
    return result


def _do_build(symbol: str, start: date, end: date) -> BuildResult:
    """Synchronous build invocation (called via asyncio.to_thread)."""
    build = SilverOhlcvBuild.from_settings()
    return build.build_window(
        symbols=[symbol],
        start_date=start,
        end_date=end,
        mode="month",
    )
