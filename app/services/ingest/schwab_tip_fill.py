"""
Schwab REST tip-fill (TA-5.3.2).

Fills the gap between silver's per-symbol watermark and "now-1min" by
pulling 1-minute bars from Schwab's pricehistory endpoint and writing
to BOTH:
  - equities.schwab_universe (idempotent; the immutable archive)
  - ClickHouse ohlcv_1m      (idempotent; chart available immediately)

**Why dual-write to CH directly.** Per [streaming_universe_model.md][1]
+ [silver_layer_plan §6][2], this is the ONE bounded exception to
the "no historical → CH directly" rule:
  - The window is ≤48 days (Schwab REST's 1-min reach)
  - Near-live, not bulk archive
  - Without it, a brand-new ad-hoc symbol's chart would be empty for
    ~24h until the next nightly silver_build → silver_to_ch_backfill
    chain caught up

The bronze write is the long-term archive; the CH write makes the
cockpit "warming up" UX instant. The next nightly silver_build picks
up the new bronze rows and merges them into silver — at which point
silver_to_ch_backfill (TA-5.3.1) re-syncs CH from silver canonical.

[1]: ../../../docs/streaming_universe_model.md
[2]: ../../../docs/silver_layer_plan.md

**Source tag.** Bronze + CH rows get `source = "schwab-tipfill"` to
distinguish from:
  - `"schwab"` — nightly REST backfill (24h-stale window)
  - `"schwab-stream"` — live CHART_EQUITY stream (sub-second)

Future audit tools can answer "how many tip-fills today?" via source
tag distribution.

**Failure model.** Dual writes are best-effort but ordered:
  1. Schwab REST fetch — must succeed (fail fast otherwise)
  2. Bronze write — if it fails, abort (preserve archive integrity)
  3. CH write — if it fails, result records the failure but the
     bronze archive is still safe; next nightly silver chain catches up

All paths return a `TipFillResult` so callers can decide retry policy.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import pandas as pd
import pyarrow as pa
from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, LessThan

from app.services.iceberg_catalog import get_catalog
from app.services.equities.schemas import equities_table_id

logger = logging.getLogger(__name__)


# Schwab pricehistory 1-min bars reach back ~48 calendar days. Going
# further returns empty windows silently. Bound the request so we
# don't accidentally fetch nothing.
SCHWAB_REST_MAX_LOOKBACK_DAYS = 48

# Source tag for bronze + CH rows.
TIP_FILL_SOURCE_TAG = "schwab-tipfill"


@dataclass
class TipFillResult:
    """Per-symbol result from `tip_fill()`."""

    symbol: str
    silver_watermark: Optional[datetime] = None
    gap_start: Optional[datetime] = None
    gap_end: Optional[datetime] = None
    bars_fetched: int = 0
    bars_written_bronze: int = 0
    bars_written_ch: int = 0
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


class SchwabTipFill:
    """Schwab REST → equities.schwab_universe + CH ohlcv_1m tip-fill.

    Construct via `from_settings()` for production; pass `schwab_provider`,
    `equities_sink`, `ch_insert` (defaults to the real CH inserter), and
    `catalog` explicitly for tests.
    """

    def __init__(
        self,
        *,
        schwab_provider: Any = None,
        equities_sink: Any = None,
        ch_insert: Any = None,
        catalog: Any = None,
    ) -> None:
        self._schwab = schwab_provider
        self._equities_sink = equities_sink
        self._ch_insert = ch_insert
        self._catalog = catalog

    @classmethod
    def from_settings(cls) -> "SchwabTipFill":
        return cls()

    # ─────────────────────────────────────────────────────────────────
    # Lazy initializers (so construction is cheap + test-friendly)
    # ─────────────────────────────────────────────────────────────────

    def _get_schwab(self):
        if self._schwab is None:
            from app.config import get_provider
            self._schwab = get_provider("schwab")
        return self._schwab

    def _get_equities_sink(self):
        if self._equities_sink is None:
            from app.services.equities.sink import EquitiesIcebergSink
            self._equities_sink = EquitiesIcebergSink.for_schwab_universe()
        return self._equities_sink

    def _get_ch_insert(self):
        if self._ch_insert is None:
            from app.db.queries import insert_bars_batch
            self._ch_insert = insert_bars_batch
        return self._ch_insert

    def _get_catalog(self):
        if self._catalog is None:
            self._catalog = get_catalog()
        return self._catalog

    # ─────────────────────────────────────────────────────────────────
    # Gap computation
    # ─────────────────────────────────────────────────────────────────

    def compute_gap(
        self, symbol: str, *, now: Optional[datetime] = None,
    ) -> tuple[Optional[datetime], datetime, datetime]:
        """Compute the (silver_watermark, gap_start, gap_end) tuple.

        silver_watermark = max(timestamp) in silver.ohlcv_1m for this
                          symbol, or None if the symbol is absent.

        gap_start = max(silver_watermark + 1min, now - 48d)
                    — bounded by Schwab REST's reach.

        gap_end   = now - 1 minute, snapped to the minute boundary
                    — avoid the in-flight live minute.

        Why +1min on silver_watermark: silver bars are minute-boundary
        timestamps, so the watermark IS the last filled minute. The
        next minute is the first unfilled one.
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            raise ValueError("symbol is required")

        now = now or datetime.now(timezone.utc)
        # Snap to minute boundary, then back off one minute to avoid
        # the live-streaming minute that's still in flight.
        gap_end = now.replace(second=0, microsecond=0) - timedelta(minutes=1)

        watermark = self._read_silver_watermark(sym)

        if watermark is None:
            # Brand-new symbol: full 48-day reach.
            gap_start = gap_end - timedelta(days=SCHWAB_REST_MAX_LOOKBACK_DAYS)
        else:
            # Resume from silver's last known minute + 1.
            resume = watermark + timedelta(minutes=1)
            max_reach = gap_end - timedelta(days=SCHWAB_REST_MAX_LOOKBACK_DAYS)
            gap_start = max(resume, max_reach)

        return watermark, gap_start, gap_end

    def _read_silver_watermark(self, symbol: str) -> Optional[datetime]:
        """Read max(timestamp) for `symbol` from equities.schwab_universe.

        Function name (`_read_silver_watermark`) kept stable to avoid a
        rename cascade through the call sites + test fixtures; the
        target table is the v2 lake equivalent of v1's silver.ohlcv_1m
        for the Schwab-side watermark question. Returns None if the
        table doesn't exist yet or no rows exist for the symbol.
        """
        from pyiceberg.exceptions import NoSuchTableError

        try:
            ohlcv_table = self._get_catalog().load_table(
                equities_table_id("schwab_universe"),
            )
        except NoSuchTableError:
            logger.info(
                "tip_fill: equities.schwab_universe absent; %s treated "
                "as brand-new ad-hoc symbol (no history → 48-day fetch)",
                symbol,
            )
            return None
        except Exception as e:
            logger.warning(
                "tip_fill: failed to load equities.schwab_universe: %s; "
                "treating %s as no-history (defensive)", e, symbol,
            )
            return None

        # Scan only the timestamp column for this symbol. We only
        # need max(ts); PyIceberg doesn't have a direct aggregate API
        # but column-filtered scans are cheap given the (symbol, ts)
        # sort key. Cap at last 60 days for speed (gap_start floor is
        # 48d anyway — older silver bars don't affect the gap).
        floor = (datetime.now(timezone.utc) - timedelta(days=60)).replace(
            second=0, microsecond=0,
        )
        try:
            arrow = ohlcv_table.scan(
                row_filter=And(
                    EqualTo("symbol", symbol),
                    GreaterThanOrEqual("timestamp", floor),
                ),
                selected_fields=("timestamp",),
            ).to_arrow()
        except Exception as e:
            logger.warning(
                "tip_fill: silver watermark scan failed for %s: %s; "
                "treating as no-history", symbol, e,
            )
            return None

        if arrow.num_rows == 0:
            # Either brand-new OR silver history exists but is >60d old
            # (unusual — silver builds nightly; the floor is generous).
            # In either case, Schwab REST's reach is the bound, so
            # treating as "no history" still gives us 48d coverage.
            return None

        ts_array = arrow.column("timestamp")
        max_ts = max(ts_array.to_pylist())
        if max_ts is None:
            return None
        if max_ts.tzinfo is None:
            max_ts = max_ts.replace(tzinfo=timezone.utc)
        return max_ts

    # ─────────────────────────────────────────────────────────────────
    # The main path
    # ─────────────────────────────────────────────────────────────────

    async def tip_fill(
        self, symbol: str, *, now: Optional[datetime] = None,
    ) -> TipFillResult:
        """Fetch the silver-watermark → live gap and dual-write to
        bronze + CH. Idempotent — re-running is safe.

        Returns a TipFillResult with per-stage row counts so the caller
        (add_streamed_symbol orchestrator, operator CLI, tests) can see
        what succeeded and what failed.

        `now` is a deterministic test hook. Production callers leave
        it None to use the real wall clock.
        """
        sym = (symbol or "").strip().upper()
        result = TipFillResult(
            symbol=sym, started_at=datetime.now(timezone.utc),
        )
        if not sym:
            result.error = "ValueError: symbol is required"
            result.finished_at = datetime.now(timezone.utc)
            return result

        try:
            watermark, gap_start, gap_end = self.compute_gap(sym, now=now)
            result.silver_watermark = watermark
            result.gap_start = gap_start
            result.gap_end = gap_end

            # Bounded sanity-check: gap_start can be >= gap_end if the
            # symbol's silver watermark is already at the live edge.
            if gap_start >= gap_end:
                logger.info(
                    "tip_fill: %s gap is empty (silver up-to-date); "
                    "watermark=%s gap_end=%s",
                    sym, watermark, gap_end,
                )
                result.finished_at = datetime.now(timezone.utc)
                return result

            logger.info(
                "tip_fill: %s fetching Schwab REST %s..%s "
                "(silver_watermark=%s)",
                sym, gap_start, gap_end, watermark,
            )

            # 1. Schwab REST fetch (single call covers the whole gap).
            df = await self._get_schwab().historical_df(
                sym, gap_start, gap_end, timeframe="1Min",
            )
            if df is None or df.empty:
                logger.info(
                    "tip_fill: %s Schwab returned 0 bars for %s..%s; "
                    "nothing to write", sym, gap_start, gap_end,
                )
                result.finished_at = datetime.now(timezone.utc)
                return result

            canonical = self._to_canonical(df, sym)
            result.bars_fetched = len(canonical)
            if canonical.empty:
                result.finished_at = datetime.now(timezone.utc)
                return result

            # 2. Bronze write (per-day) — preserves the archive.
            try:
                bronze_written = await self._write_equities(canonical)
                result.bars_written_bronze = bronze_written
            except Exception as e:
                logger.exception(
                    "tip_fill: %s bronze write failed: %s", sym, e,
                )
                result.error = f"BronzeWriteError: {type(e).__name__}: {e}"
                result.finished_at = datetime.now(timezone.utc)
                # Abort — don't write to CH if bronze archive failed.
                return result

            # 3. CH write — immediate chart availability.
            try:
                ch_written = self._write_ch(canonical)
                result.bars_written_ch = ch_written
            except Exception as e:
                logger.exception(
                    "tip_fill: %s CH write failed: %s "
                    "(bronze write succeeded; next nightly silver chain "
                    "will repair CH)", sym, e,
                )
                result.error = (
                    f"ChWriteError: {type(e).__name__}: {e} "
                    f"(bronze ok with {result.bars_written_bronze} rows)"
                )
                result.finished_at = datetime.now(timezone.utc)
                return result

            logger.info(
                "tip_fill: %s done — fetched=%d bronze=%d ch=%d",
                sym, result.bars_fetched,
                result.bars_written_bronze, result.bars_written_ch,
            )
        except Exception as e:
            logger.exception("tip_fill: %s failed: %s", sym, e)
            result.error = f"{type(e).__name__}: {e}"

        result.finished_at = datetime.now(timezone.utc)
        return result

    # ─────────────────────────────────────────────────────────────────
    # Frame conversion
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _to_canonical(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """Convert Schwab `historical_df()` output → canonical bronze
        + CH frame with `source = "schwab-tipfill"`.

        Mirrors `scripts/schwab_history_backfill._schwab_1m_to_canonical`
        but stamps a different source tag.
        """
        if df is None or df.empty:
            return pd.DataFrame()

        x = df.reset_index()
        if "timestamp" not in x.columns:
            if len(x.columns) < 6:
                return pd.DataFrame()
            x = x.rename(columns={x.columns[0]: "timestamp"})

        x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True)
        clean = x.dropna(
            subset=["timestamp", "open", "high", "low", "close", "volume"],
        )
        if clean.empty:
            return pd.DataFrame()

        return pd.DataFrame({
            "symbol": symbol.upper().strip(),
            "timestamp": clean["timestamp"],
            "open": clean["open"].astype("float64"),
            "high": clean["high"].astype("float64"),
            "low": clean["low"].astype("float64"),
            "close": clean["close"].astype("float64"),
            "volume": clean["volume"].astype("float64"),
            "vwap": pd.NA,           # Schwab doesn't return vwap
            "trade_count": pd.NA,    # Schwab doesn't return trade counts
            "source": TIP_FILL_SOURCE_TAG,
        })

    # ─────────────────────────────────────────────────────────────────
    # Writers
    # ─────────────────────────────────────────────────────────────────

    async def _write_equities(self, canonical: pd.DataFrame) -> int:
        """Write the canonical frame to equities.schwab_universe, per-day
        (EquitiesIcebergSink.write expects a `file_date`). Returns total
        rows written across days."""
        if canonical.empty:
            return 0

        # Group by UTC calendar day.
        per_day_groups = canonical.groupby(
            canonical["timestamp"].dt.date,
        )
        total = 0
        sink = self._get_equities_sink()
        for day, frame in per_day_groups:
            res = await sink.write(
                frame.copy(),
                file_date=day,
                kind="minute",
                provider="schwab",
            )
            # SinkResult.bars_written is the source of truth.
            written = getattr(res, "bars_written", 0) or 0
            total += int(written)
        return total

    def _write_ch(self, canonical: pd.DataFrame) -> int:
        """Bulk-insert canonical rows into CH ohlcv_1m."""
        if canonical.empty:
            return 0
        rows = []
        for _, r in canonical.iterrows():
            rows.append({
                "symbol": r["symbol"],
                "timestamp": r["timestamp"].to_pydatetime()
                    if hasattr(r["timestamp"], "to_pydatetime") else r["timestamp"],
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
                "vwap": 0.0,           # Schwab doesn't provide it
                "trade_count": 0,      # Schwab doesn't provide it
                "source": TIP_FILL_SOURCE_TAG,
            })
        self._get_ch_insert()(rows)
        return len(rows)
