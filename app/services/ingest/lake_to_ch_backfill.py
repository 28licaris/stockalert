"""
Lake → ClickHouse backfill (was silver→CH; CV11 retargeted at
equities.polygon_adjusted).

Reads adjusted 1-minute bars via `AdjustedOhlcvReader` (which post-CV11
sources from `equities.polygon_adjusted`) and bulk-inserts into
ClickHouse `ohlcv_1m`. The **canonical fast path** for populating CH
from deep history — replaces the legacy provider-REST-direct-to-CH
path when triggered.

Per the v2 consumer contract:
  equities.polygon_adjusted = canonical (immutable, snapshot-pinned,
                              corp-action-adj'd, ~5y deep history)
  ClickHouse                = derived hot cache (re-buildable from
                              the lake any time)

polygon_adjusted stores split-adjusted prices with adj_factor on
every row. That's what CH ohlcv_1m needs (the chart, indicators,
screener, backtest all consume the adjusted view). Consumers needing
raw prices multiply by adj_factor — math is local, no extra read.

Wall-clock notes:
  - Cold-start NVDA × 730 days ≈ 200K bars → ~5-8 seconds end-to-end
    on a warm Iceberg cache + local CH. polygon_adjusted's bucket(32,
    symbol) partitioning means single-symbol scans read ~1/32 of each
    month's data (v1 silver was month-only partitioning, read whole
    month). Actual CH insert is sub-second for 200K rows.
  - Idempotent: CH `ohlcv_1m` is a `ReplacingMergeTree(version)` — re-
    running with the same data produces no duplicates after the next
    merge.

CV15 rename pass: module + class renamed from silver_to_ch_backfill /
SilverToChBackfill. The body is kept stable for
caller compatibility — Phase 1C is the read-side cutover, not the
big rename pass. The full rename to `lake_to_ch_backfill` happens in
CV14 alongside silver-module deletion.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.db.queries import insert_bars_batch
from app.services.readers.schemas import SilverBarsResponse
from app.services.readers.adjusted_ohlcv_reader import AdjustedOhlcvReader

logger = logging.getLogger(__name__)


# Default lookback when caller passes only `days`. 730 = 2 years —
# matches the legacy "daily" backfill window and what the chart UI
# expects for a deep look-back at add-time.
DEFAULT_BACKFILL_DAYS = 730


@dataclass
class LakeToChBackfillResult:
    """Per-symbol result from `backfill_symbol`."""

    symbol: str
    bars_read: int = 0
    bars_written: int = 0
    snapshot_id: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None

    @property
    def duration_seconds(self) -> float:
        if self.started_at is None or self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()


class LakeToChBackfill:
    """Silver `ohlcv_1m` → ClickHouse `ohlcv_1m` backfill service.

    Construct via `from_settings()` for production; pass `reader`
    explicitly for tests.

    Public API:
      - `backfill_symbol(symbol, *, days=730)` — pull the last N days
        from silver and bulk-insert into CH.
      - `backfill_symbol_window(symbol, start, end)` — explicit window
        variant for fine-grained control.

    Both are sync (CH insert is blocking) — wrap in
    `asyncio.to_thread` from async callers.
    """

    def __init__(self, *, reader: Optional[AdjustedOhlcvReader] = None) -> None:
        self._reader = reader

    @classmethod
    def from_settings(cls) -> "LakeToChBackfill":
        return cls()

    def _get_reader(self) -> AdjustedOhlcvReader:
        if self._reader is None:
            self._reader = AdjustedOhlcvReader.from_settings()
        return self._reader

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    def backfill_symbol(
        self,
        symbol: str,
        *,
        days: int = DEFAULT_BACKFILL_DAYS,
    ) -> LakeToChBackfillResult:
        """Backfill the last `days` calendar days of silver bars into CH.

        Args:
            symbol: Ticker (case-insensitive).
            days: Calendar-day lookback. 730 (2y) is the default and
                matches the legacy daily-backfill window.
        """
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=max(1, int(days)))
        return self.backfill_symbol_window(symbol, start, end)

    def backfill_symbol_window(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> LakeToChBackfillResult:
        """Backfill `[start, end)` of silver bars into CH for `symbol`."""
        sym = (symbol or "").strip().upper()
        result = LakeToChBackfillResult(
            symbol=sym, started_at=datetime.now(timezone.utc),
        )
        if not sym:
            result.finished_at = datetime.now(timezone.utc)
            return result

        try:
            resp = self._get_reader().get_bars(sym, start, end)
            result.bars_read = resp.count
            result.snapshot_id = resp.snapshot_id

            if resp.count == 0:
                # Cold-start: symbol not in silver yet (brand-new ad-hoc
                # add). NOT an error — `schwab_rest_tip_fill` will cover
                # the 48-day reach in the next step.
                result.finished_at = datetime.now(timezone.utc)
                logger.info(
                    "LakeToChBackfill: %s — silver has 0 rows in "
                    "[%s..%s); skipping CH insert", sym, start, end,
                )
                return result

            ch_rows = self._silver_to_ch_rows(resp)
            insert_bars_batch(ch_rows)
            result.bars_written = len(ch_rows)
            logger.info(
                "LakeToChBackfill: %s — wrote %d bars to CH (snapshot=%s)",
                sym, result.bars_written, result.snapshot_id,
            )
        except Exception as e:
            logger.exception(
                "LakeToChBackfill: %s [%s..%s) failed: %s",
                sym, start, end, e,
            )
            result.error = f"{type(e).__name__}: {e}"

        result.finished_at = datetime.now(timezone.utc)
        return result

    # ─────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _silver_to_ch_rows(resp: SilverBarsResponse) -> list[dict]:
        """Translate `SilverBarsResponse` → CH ohlcv_1m row dicts.

        Silver's split-adjusted OHLCV maps directly to CH ohlcv_1m's
        single OHLCV set. Source tag is set to `silver-{provider}` so
        the row is distinguishable from provider-direct inserts
        (legacy path ②) in any future audit.
        """
        out: list[dict] = []
        for bar in resp.bars:
            out.append({
                "symbol": bar.symbol,
                "timestamp": bar.timestamp,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "vwap": bar.vwap or 0.0,
                "trade_count": bar.trade_count or 0,
                # Provenance: this row came from the silver-derived path,
                # not provider REST. Future audit + CH-wipe-rebuild
                # tools rely on this tag.
                "source": f"silver-{bar.source_provider}",
            })
        return out
