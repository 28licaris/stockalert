"""
Tests for the Schwab REST tip-fill (TA-5.3.2).

Verifies:
  - compute_gap: silver-watermark + 48-day bound math
  - compute_gap: brand-new symbol → full 48-day fetch
  - compute_gap: silver up-to-date → empty gap
  - _read_silver_watermark: missing table / empty rows → None
  - tip_fill happy path: fetch → bronze → CH all succeed
  - tip_fill empty Schwab response → 0 written, no error
  - tip_fill Schwab error → captured as result.error
  - tip_fill bronze write error → CH not attempted
  - tip_fill CH write error → bronze succeeded, partial result
  - Source tag "schwab-tipfill" propagates to bronze + CH
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import pandas as pd
import pyarrow as pa
import pytest

from app.services.ingest.schwab_tip_fill import (
    SCHWAB_REST_MAX_LOOKBACK_DAYS,
    TIP_FILL_SOURCE_TAG,
    SchwabTipFill,
    TipFillResult,
)


# ─────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────


class _FakeScan:
    def __init__(self, arrow: pa.Table) -> None:
        self._arrow = arrow

    def to_arrow(self) -> pa.Table:
        return self._arrow


class _FakeSilverOhlcvTable:
    """Stand-in for silver.ohlcv_1m — returns whatever timestamps the
    test supplies."""

    def __init__(self, timestamps: list[datetime]) -> None:
        self._timestamps = timestamps
        self.scan_calls: list[Any] = []

    def scan(self, *, row_filter=None, selected_fields=None, **_kw):
        self.scan_calls.append(row_filter)
        if not self._timestamps:
            schema = pa.schema([
                pa.field("timestamp", pa.timestamp("us", tz="UTC")),
            ])
            return _FakeScan(pa.Table.from_pylist([], schema=schema))
        rows = [{"timestamp": ts} for ts in self._timestamps]
        return _FakeScan(pa.Table.from_pylist(rows))


class _FakeCatalog:
    def __init__(self, ohlcv_table: Optional[_FakeSilverOhlcvTable]) -> None:
        self._ohlcv = ohlcv_table

    def load_table(self, _identifier):
        if self._ohlcv is None:
            from pyiceberg.exceptions import NoSuchTableError
            raise NoSuchTableError("missing")
        return self._ohlcv


class _FakeSchwabProvider:
    """Stand-in for SchwabProvider.historical_df."""

    def __init__(self, df: pd.DataFrame, raises: Optional[Exception] = None) -> None:
        self._df = df
        self._raises = raises
        self.last_call: dict = {}

    async def historical_df(self, symbol, start, end, *, timeframe="1Min"):
        self.last_call = {
            "symbol": symbol, "start": start, "end": end, "timeframe": timeframe,
        }
        if self._raises:
            raise self._raises
        return self._df.copy() if self._df is not None else pd.DataFrame()


class _FakeSinkResult:
    def __init__(self, bars_written: int) -> None:
        self.bars_written = bars_written


class _FakeEquitiesSink:
    def __init__(self, raises: Optional[Exception] = None) -> None:
        self._raises = raises
        self.writes: list[dict] = []

    async def write(self, df, *, file_date, kind, provider):
        if self._raises:
            raise self._raises
        self.writes.append({
            "rows": len(df),
            "file_date": file_date,
            "source_values": df["source"].unique().tolist() if not df.empty else [],
        })
        return _FakeSinkResult(bars_written=len(df))


def _schwab_df(start_ts: datetime, n_bars: int) -> pd.DataFrame:
    """Build a Schwab-shaped historical_df output (DatetimeIndex'd OHLCV)."""
    rows = []
    for m in range(n_bars):
        rows.append({
            "timestamp": start_ts + timedelta(minutes=m),
            "open": 100.0 + m * 0.01,
            "high": 100.0 + m * 0.01 + 0.05,
            "low": 100.0 + m * 0.01 - 0.05,
            "close": 100.0 + m * 0.01,
            "volume": 1000 + m,
        })
    df = pd.DataFrame(rows)
    df = df.set_index("timestamp")
    return df


def _make_tip_fill(
    *,
    silver_timestamps: Optional[list[datetime]] = None,
    schwab_df: Optional[pd.DataFrame] = None,
    schwab_raises: Optional[Exception] = None,
    equities_raises: Optional[Exception] = None,
    ch_raises: Optional[Exception] = None,
) -> tuple[SchwabTipFill, _FakeEquitiesSink, list]:
    """Wire a SchwabTipFill with all fakes injected."""
    catalog = _FakeCatalog(
        ohlcv_table=(
            _FakeSilverOhlcvTable(silver_timestamps)
            if silver_timestamps is not None else None
        ),
    )
    schwab = _FakeSchwabProvider(
        df=schwab_df if schwab_df is not None else pd.DataFrame(),
        raises=schwab_raises,
    )
    equities_sink = _FakeEquitiesSink(raises=equities_raises)
    ch_rows: list[dict] = []

    def _ch_insert(rows):
        if ch_raises:
            raise ch_raises
        ch_rows.extend(rows)

    tf = SchwabTipFill(
        schwab_provider=schwab,
        equities_sink=equities_sink,
        ch_insert=_ch_insert,
        catalog=catalog,
    )
    return tf, equities_sink, ch_rows


# ─────────────────────────────────────────────────────────────────────
# compute_gap
# ─────────────────────────────────────────────────────────────────────


class TestComputeGap:
    def test_empty_silver_full_48_day_fetch(self) -> None:
        """Brand-new symbol → gap is the full 48 days."""
        tf, _, _ = _make_tip_fill(silver_timestamps=[])
        now = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)
        watermark, gap_start, gap_end = tf.compute_gap("NVDA", now=now)
        assert watermark is None
        # gap_end is now snapped to minute - 1min.
        assert gap_end == datetime(2026, 6, 1, 14, 29, tzinfo=timezone.utc)
        # gap_start is gap_end - 48 days.
        assert gap_start == gap_end - timedelta(days=48)

    def test_silver_within_48d_resumes_at_watermark_plus_one(self) -> None:
        now = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)
        # Watermark = 10 days ago (well within 48-day reach).
        wm = now - timedelta(days=10)
        tf, _, _ = _make_tip_fill(silver_timestamps=[wm])
        watermark, gap_start, gap_end = tf.compute_gap("NVDA", now=now)
        assert watermark == wm
        # gap_start = watermark + 1 minute (NOT bounded by 48d here).
        assert gap_start == wm + timedelta(minutes=1)

    def test_silver_older_than_48d_bounded_by_max_reach(self) -> None:
        now = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)
        # Watermark from 100 days ago (older than Schwab's 48d reach).
        wm = now - timedelta(days=100)
        tf, _, _ = _make_tip_fill(silver_timestamps=[wm])
        watermark, gap_start, gap_end = tf.compute_gap("NVDA", now=now)
        assert watermark == wm
        # gap_start is bounded at gap_end - 48d (we can't fetch beyond).
        max_reach = gap_end - timedelta(days=48)
        assert gap_start == max_reach

    def test_silver_caught_up_means_empty_gap(self) -> None:
        """If silver is up-to-date (watermark within 1 min of now),
        gap_start can equal or exceed gap_end → empty gap."""
        now = datetime(2026, 6, 1, 14, 30, 0, tzinfo=timezone.utc)
        # Watermark is JUST behind: 14:29 (one minute back).
        wm = datetime(2026, 6, 1, 14, 29, tzinfo=timezone.utc)
        tf, _, _ = _make_tip_fill(silver_timestamps=[wm])
        _, gap_start, gap_end = tf.compute_gap("NVDA", now=now)
        # gap_end = 14:29, gap_start = watermark + 1m = 14:30 → empty gap.
        assert gap_start >= gap_end

    def test_missing_table_treated_as_no_history(self) -> None:
        tf, _, _ = _make_tip_fill(silver_timestamps=None)  # None = no table
        now = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)
        watermark, gap_start, gap_end = tf.compute_gap("NVDA", now=now)
        assert watermark is None
        assert gap_start == gap_end - timedelta(days=48)

    def test_empty_symbol_raises(self) -> None:
        tf, _, _ = _make_tip_fill(silver_timestamps=[])
        with pytest.raises(ValueError):
            tf.compute_gap("")

    def test_symbol_uppercased(self) -> None:
        wm = datetime(2026, 6, 1, tzinfo=timezone.utc)
        tf, _, _ = _make_tip_fill(silver_timestamps=[wm])
        # Lowercase input → still finds watermark (uses uppercased
        # symbol in the scan filter).
        now = datetime(2026, 6, 5, 14, 30, tzinfo=timezone.utc)
        tf.compute_gap("nvda", now=now)


# ─────────────────────────────────────────────────────────────────────
# tip_fill (async)
# ─────────────────────────────────────────────────────────────────────


class TestTipFillHappyPath:
    @pytest.mark.asyncio
    async def test_fetches_and_dual_writes(self) -> None:
        """Schwab returns 5 bars → 5 bronze rows + 5 CH rows. Source
        tag is 'schwab-tipfill' on both."""
        now = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)
        wm = now - timedelta(days=10)
        # 5 bars covering ~5 minutes around the gap start.
        df = _schwab_df(wm + timedelta(minutes=1), 5)
        tf, equities_sink, ch_rows = _make_tip_fill(
            silver_timestamps=[wm], schwab_df=df,
        )
        result = await tf.tip_fill("NVDA", now=now)

        assert result.succeeded
        assert result.bars_fetched == 5
        assert result.bars_written_bronze == 5
        assert result.bars_written_ch == 5
        assert result.silver_watermark == wm
        # All bronze rows tagged with the tip-fill source.
        assert len(equities_sink.writes) >= 1
        for w in equities_sink.writes:
            assert TIP_FILL_SOURCE_TAG in w["source_values"]
        # All CH rows tagged the same.
        for r in ch_rows:
            assert r["source"] == TIP_FILL_SOURCE_TAG
            assert r["symbol"] == "NVDA"
            assert "vwap" in r
            assert "trade_count" in r

    @pytest.mark.asyncio
    async def test_per_day_bronze_writes(self) -> None:
        """Schwab bars spanning 2 UTC days → 2 bronze writes (per-day)."""
        now = datetime(2026, 6, 5, 14, 30, tzinfo=timezone.utc)
        wm = datetime(2026, 6, 1, 23, 50, tzinfo=timezone.utc)
        # 30 bars from 23:51..00:20 (crosses UTC midnight).
        df = _schwab_df(wm + timedelta(minutes=1), 30)
        tf, equities_sink, _ = _make_tip_fill(
            silver_timestamps=[wm], schwab_df=df,
        )
        await tf.tip_fill("NVDA", now=now)
        # Group by date: rows on 2026-06-01 and 2026-06-02.
        dates = {w["file_date"] for w in equities_sink.writes}
        assert len(dates) == 2

    @pytest.mark.asyncio
    async def test_empty_schwab_response_no_writes(self) -> None:
        """Schwab returns nothing → 0 bars written, no error."""
        now = datetime(2026, 6, 5, tzinfo=timezone.utc)
        wm = datetime(2026, 6, 1, tzinfo=timezone.utc)
        tf, equities_sink, ch_rows = _make_tip_fill(
            silver_timestamps=[wm], schwab_df=pd.DataFrame(),
        )
        result = await tf.tip_fill("NVDA", now=now)
        assert result.succeeded
        assert result.bars_fetched == 0
        assert result.bars_written_bronze == 0
        assert result.bars_written_ch == 0
        assert equities_sink.writes == []
        assert ch_rows == []

    @pytest.mark.asyncio
    async def test_empty_gap_skips_schwab(self) -> None:
        """Silver up-to-date → no Schwab call at all."""
        now = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)
        # Watermark equals gap_end (= now - 1m) → gap_start = wm + 1m
        # = now, so gap_start > gap_end → empty gap.
        wm = datetime(2026, 6, 1, 14, 29, tzinfo=timezone.utc)
        tf, equities_sink, ch_rows = _make_tip_fill(
            silver_timestamps=[wm], schwab_df=_schwab_df(now, 1),
        )
        result = await tf.tip_fill("NVDA", now=now)
        assert result.succeeded
        # bars_fetched stays 0 because we never called Schwab.
        assert result.bars_fetched == 0
        assert equities_sink.writes == []
        assert ch_rows == []


# ─────────────────────────────────────────────────────────────────────
# Error handling
# ─────────────────────────────────────────────────────────────────────


class TestTipFillErrors:
    @pytest.mark.asyncio
    async def test_schwab_error_captured(self) -> None:
        now = datetime(2026, 6, 5, tzinfo=timezone.utc)
        wm = datetime(2026, 6, 1, tzinfo=timezone.utc)
        tf, equities_sink, ch_rows = _make_tip_fill(
            silver_timestamps=[wm],
            schwab_raises=RuntimeError("schwab 502"),
        )
        result = await tf.tip_fill("NVDA", now=now)
        assert not result.succeeded
        assert "RuntimeError" in (result.error or "")
        # No writes attempted.
        assert equities_sink.writes == []
        assert ch_rows == []

    @pytest.mark.asyncio
    async def test_bronze_error_aborts_ch(self) -> None:
        """If bronze write fails, we don't try CH (preserve archive
        integrity — the caller can retry the whole tip-fill)."""
        now = datetime(2026, 6, 5, tzinfo=timezone.utc)
        wm = datetime(2026, 6, 1, tzinfo=timezone.utc)
        df = _schwab_df(wm + timedelta(minutes=1), 3)
        tf, _, ch_rows = _make_tip_fill(
            silver_timestamps=[wm],
            schwab_df=df,
            equities_raises=RuntimeError("S3 access denied"),
        )
        result = await tf.tip_fill("NVDA", now=now)
        assert not result.succeeded
        assert "BronzeWriteError" in (result.error or "")
        assert result.bars_written_bronze == 0
        assert result.bars_written_ch == 0
        assert ch_rows == []

    @pytest.mark.asyncio
    async def test_ch_error_after_bronze_success(self) -> None:
        """If CH write fails but bronze succeeded, result records the
        partial state. Bronze archive is safe; next nightly silver
        chain catches up CH."""
        now = datetime(2026, 6, 5, tzinfo=timezone.utc)
        wm = datetime(2026, 6, 1, tzinfo=timezone.utc)
        df = _schwab_df(wm + timedelta(minutes=1), 3)
        tf, equities_sink, _ = _make_tip_fill(
            silver_timestamps=[wm],
            schwab_df=df,
            ch_raises=RuntimeError("CH connection refused"),
        )
        result = await tf.tip_fill("NVDA", now=now)
        assert not result.succeeded
        assert "ChWriteError" in (result.error or "")
        # Bronze write DID succeed.
        assert result.bars_written_bronze > 0
        # CH didn't get any rows.
        assert result.bars_written_ch == 0
        # Bronze archive intact (the message hints at the partial state).
        assert "bronze ok" in (result.error or "")

    @pytest.mark.asyncio
    async def test_empty_symbol_returns_error_no_raise(self) -> None:
        tf, _, _ = _make_tip_fill(silver_timestamps=[])
        result = await tf.tip_fill("")
        assert not result.succeeded
        assert "symbol is required" in (result.error or "")


# ─────────────────────────────────────────────────────────────────────
# Source-tag propagation through normalize.py
# ─────────────────────────────────────────────────────────────────────


# CV14: TestSourceTagPropagation removed. _provider_from_source lived
# in the silver normalize layer (deleted with silver/). v2 doesn't need
# this mapping — equities.schwab_universe rows carry the literal Schwab
# source tag (e.g. "schwab-rest", "schwab-live") and never go through a
# multi-provider precedence merge. The source column is preserved as-is
# in the EquitiesIcebergSink (CV2), and downstream consumers that care
# about provider identity match on the literal string.
