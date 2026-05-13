"""
Unit tests for ``app.services.flatfiles_backfill``.

The service is mocked at the ``PolygonFlatFilesClient`` boundary (no S3
traffic) and the ClickHouse insert functions are swapped for ``AsyncMock``
instances (no DB traffic). Every test runs in milliseconds.

Coverage:

  - DataFrame -> record transform (column subset, defaults, NaN safety)
  - Symbol normalisation (uppercase, dedupe, empty preservation)
  - Single happy day: download + insert + result accounting
  - Multi-day range with mixed outcomes (ok / missing / filtered / error)
  - Dry-run path persists nothing
  - Batch size honoured (multiple inserts when records > batch_size)
  - on_progress callback fired exactly once per processed day
  - Errors in download / insert are captured per-day without aborting
  - Inputs validated (kind, date order, source tag emptiness)
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable, Optional
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from app.providers.polygon_flatfiles import FlatFileInfo, PolygonFlatFilesError
from app.services.flatfiles_backfill import (
    BackfillRangeResult,
    DayResult,
    FlatFilesBackfillService,
)


# ---------- helpers ----------


def _ts(year: int, month: int, day: int, hour: int = 14, minute: int = 30) -> pd.Timestamp:
    return pd.Timestamp(datetime(year, month, day, hour, minute, tzinfo=timezone.utc))


def _minute_df(rows: list[dict]) -> pd.DataFrame:
    """Build a flat-file-shape DataFrame. ``timestamp`` defaults to a
    fixed UTC moment so tests don't depend on real timestamps."""
    out = []
    for r in rows:
        d = dict(r)
        d.setdefault("ticker", "AAPL")
        d.setdefault("volume", 1000.0)
        d.setdefault("open", 100.0)
        d.setdefault("close", 100.5)
        d.setdefault("high", 100.7)
        d.setdefault("low", 99.9)
        d.setdefault("window_start", 0)
        d.setdefault("transactions", 10)
        d.setdefault("timestamp", _ts(2026, 5, 12))
        out.append(d)
    return pd.DataFrame(out)


def _daily_df(rows: list[dict]) -> pd.DataFrame:
    """Daily files don't have intraday timestamps; we still build the
    canonical ``timestamp`` column via the client, so tests mirror that
    contract by providing it directly."""
    return _minute_df(rows).drop(columns=["transactions"], errors="ignore")


def _flatfiles_mock(
    *,
    available: Optional[list[FlatFileInfo]] = None,
    minute_by_date: Optional[dict[date, pd.DataFrame]] = None,
    daily_by_date: Optional[dict[date, pd.DataFrame]] = None,
    raise_on_minute: Optional[dict[date, Exception]] = None,
) -> MagicMock:
    fake = MagicMock(name="flat_files_client")
    fake.available_dates.return_value = available or []

    def _minute(d: date, *, symbols: Optional[Iterable[str]] = None):
        if raise_on_minute and d in raise_on_minute:
            raise raise_on_minute[d]
        df = (minute_by_date or {}).get(d, pd.DataFrame())
        if df.empty or symbols is None:
            return df
        wanted = {s.upper() for s in symbols}
        return df[df["ticker"].isin(wanted)].copy()

    def _daily(d: date, *, symbols: Optional[Iterable[str]] = None):
        df = (daily_by_date or {}).get(d, pd.DataFrame())
        if df.empty or symbols is None:
            return df
        wanted = {s.upper() for s in symbols}
        return df[df["ticker"].isin(wanted)].copy()

    fake.download_minute_aggs.side_effect = _minute
    fake.download_day_aggs.side_effect = _daily
    return fake


def _build_service(
    flat_files: MagicMock,
    *,
    insert_minute: Optional[AsyncMock] = None,
    insert_daily: Optional[AsyncMock] = None,
    source_tag: str = "polygon-flatfiles",
    batch_size: int = 1000,
) -> tuple[FlatFilesBackfillService, AsyncMock, AsyncMock]:
    """Construct a service wired to a mocked S3 client and async insert
    sinks. Returns the trio so each test can assert on all three."""
    insert_minute = insert_minute or AsyncMock(name="insert_minute_fn")
    insert_daily = insert_daily or AsyncMock(name="insert_daily_fn")
    svc = FlatFilesBackfillService(
        flat_files=flat_files,
        insert_minute_fn=insert_minute,
        insert_daily_fn=insert_daily,
        source_tag=source_tag,
        batch_size=batch_size,
    )
    return svc, insert_minute, insert_daily


# ---------- construction / validation ----------


class TestConstruction:
    def test_defaults(self):
        svc = FlatFilesBackfillService(
            flat_files=MagicMock(),
            insert_minute_fn=AsyncMock(),
            insert_daily_fn=AsyncMock(),
        )
        assert svc.source_tag == "polygon-flatfiles"
        assert svc.batch_size == 1000

    def test_empty_source_tag_rejected(self):
        with pytest.raises(ValueError, match="source_tag"):
            FlatFilesBackfillService(
                flat_files=MagicMock(),
                insert_minute_fn=AsyncMock(),
                insert_daily_fn=AsyncMock(),
                source_tag="   ",
            )

    def test_batch_size_floored_to_one(self):
        svc = FlatFilesBackfillService(
            flat_files=MagicMock(),
            insert_minute_fn=AsyncMock(),
            insert_daily_fn=AsyncMock(),
            batch_size=0,
        )
        assert svc.batch_size == 1


# ---------- transform ----------


class TestTransform:
    def test_minute_records_have_canonical_shape(self):
        svc, _, _ = _build_service(_flatfiles_mock())
        df = _minute_df([
            {"ticker": "AAPL", "open": 1.0, "close": 1.5, "high": 1.6,
             "low": 0.9, "volume": 100.0, "transactions": 5},
        ])
        records = svc._df_to_minute_records(df)
        assert len(records) == 1
        r = records[0]
        assert r["symbol"] == "AAPL"
        assert isinstance(r["timestamp"], datetime)
        assert r["timestamp"].tzinfo is timezone.utc
        assert r["open"] == 1.0 and r["close"] == 1.5
        assert r["high"] == 1.6 and r["low"] == 0.9
        assert r["volume"] == 100.0
        assert r["vwap"] == 0.0  # flat files do not carry VWAP
        assert r["trade_count"] == 5
        assert r["source"] == "polygon-flatfiles"

    def test_minute_records_skip_rows_with_nan_ohlc(self):
        svc, _, _ = _build_service(_flatfiles_mock())
        df = _minute_df([
            {"ticker": "AAPL", "open": 1.0},
            {"ticker": "MSFT", "open": float("nan")},
            {"ticker": "GOOG", "close": float("nan")},
            {"ticker": "SPY",  "volume": float("nan")},
        ])
        records = svc._df_to_minute_records(df)
        assert [r["symbol"] for r in records] == ["AAPL"]

    def test_minute_records_default_nan_transactions_to_zero(self):
        svc, _, _ = _build_service(_flatfiles_mock())
        df = _minute_df([{"ticker": "AAPL", "transactions": pd.NA}])
        # Cast through nullable Int64 so pd.NA survives the DataFrame.
        df["transactions"] = df["transactions"].astype("Int64")
        records = svc._df_to_minute_records(df)
        assert len(records) == 1
        assert records[0]["trade_count"] == 0

    def test_minute_records_use_source_tag_override(self):
        svc, _, _ = _build_service(_flatfiles_mock(), source_tag="polygon-test")
        df = _minute_df([{"ticker": "AAPL"}])
        records = svc._df_to_minute_records(df)
        assert records[0]["source"] == "polygon-test"

    def test_daily_records_omit_vwap_and_trade_count(self):
        svc, _, _ = _build_service(_flatfiles_mock())
        df = _daily_df([{"ticker": "AAPL"}])
        records = svc._df_to_daily_records(df)
        assert len(records) == 1
        r = records[0]
        assert "vwap" not in r
        assert "trade_count" not in r
        assert r["symbol"] == "AAPL"

    def test_empty_frame_returns_empty_list(self):
        svc, _, _ = _build_service(_flatfiles_mock())
        assert svc._df_to_minute_records(pd.DataFrame()) == []
        assert svc._df_to_daily_records(pd.DataFrame()) == []


# ---------- normalisation ----------


class TestSymbolNormalisation:
    def test_upper_and_dedup_preserves_order(self):
        out = FlatFilesBackfillService._normalize_symbols(
            ["aapl", "MSFT", " aapl ", "SPY", "spy", "msft"]
        )
        assert out == ["AAPL", "MSFT", "SPY"]

    def test_empty_returns_empty(self):
        assert FlatFilesBackfillService._normalize_symbols([]) == []
        assert FlatFilesBackfillService._normalize_symbols(["", "  "]) == []


# ---------- backfill_range ----------


class TestBackfillRange:
    @pytest.mark.asyncio
    async def test_single_day_happy_path(self):
        target = date(2026, 5, 12)
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key="x", file_date=target, size=1)],
            minute_by_date={target: _minute_df([
                {"ticker": "AAPL"}, {"ticker": "MSFT"},
            ])},
        )
        svc, insert_minute, insert_daily = _build_service(flat)

        result = await svc.backfill_range(["AAPL", "MSFT"], target, target)

        assert isinstance(result, BackfillRangeResult)
        assert result.days_listed == 1
        assert result.days_ok == 1
        assert result.days_errored == 0
        assert result.bars_persisted == 2
        insert_minute.assert_awaited_once()
        # Daily insert path must not be hit on minute backfill.
        insert_daily.assert_not_awaited()

        # Records passed to ClickHouse carry the correct source tag.
        rows = insert_minute.await_args_list[0].args[0]
        assert {r["source"] for r in rows} == {"polygon-flatfiles"}
        assert {r["symbol"] for r in rows} == {"AAPL", "MSFT"}

    @pytest.mark.asyncio
    async def test_multi_day_mixed_outcomes(self):
        d_ok = date(2026, 5, 11)
        d_missing = date(2026, 5, 12)  # listed but client returns empty
        d_filtered = date(2026, 5, 13)  # listed, file present, all symbols filtered out
        d_error = date(2026, 5, 14)     # listed, client raises
        flat = _flatfiles_mock(
            available=[
                FlatFileInfo(key=f"a/{i}", file_date=d, size=1)
                for i, d in enumerate([d_ok, d_missing, d_filtered, d_error])
            ],
            minute_by_date={
                d_ok: _minute_df([{"ticker": "AAPL"}]),
                # d_missing: not in map → empty df
                d_filtered: _minute_df([{"ticker": "ZZZZ"}]),  # filtered out
            },
            raise_on_minute={d_error: PolygonFlatFilesError("boom")},
        )
        svc, insert_minute, _ = _build_service(flat)

        result = await svc.backfill_range(["AAPL"], d_ok, d_error)

        assert result.days_listed == 4
        assert result.days_ok == 1
        # d_missing has the file listed but empty: when a symbol filter is
        # active the service classifies that as "filtered" (the file exists,
        # we just got 0 matches). Both filtered classifications land in the
        # same bucket here.
        assert result.days_filtered == 2
        assert result.days_missing == 0
        assert result.days_errored == 1
        assert result.bars_persisted == 1
        # We only inserted for d_ok; filtered + error did not call insert.
        insert_minute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_symbols_filter_means_full_market(self):
        target = date(2026, 5, 12)
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key="x", file_date=target, size=1)],
            minute_by_date={target: _minute_df([
                {"ticker": "AAPL"}, {"ticker": "MSFT"}, {"ticker": "SPY"},
            ])},
        )
        svc, insert_minute, _ = _build_service(flat)

        result = await svc.backfill_range([], target, target)

        assert result.symbols_requested == 0
        assert result.days_ok == 1
        assert result.bars_persisted == 3
        flat.download_minute_aggs.assert_called_once_with(target, symbols=None)

    @pytest.mark.asyncio
    async def test_dry_run_persists_nothing(self):
        target = date(2026, 5, 12)
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key="x", file_date=target, size=1)],
            minute_by_date={target: _minute_df([{"ticker": "AAPL"}])},
        )
        svc, insert_minute, _ = _build_service(flat)

        result = await svc.backfill_range(
            ["AAPL"], target, target, dry_run=True,
        )

        assert result.days_skipped == 1
        assert result.days_ok == 0
        assert result.bars_persisted == 0
        insert_minute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_batch_size_split_into_multiple_inserts(self):
        target = date(2026, 5, 12)
        # 2_500 rows with batch_size=1000 should be 3 inserts (1000, 1000, 500).
        rows = [
            {"ticker": f"T{i:04d}", "transactions": 1}
            for i in range(2500)
        ]
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key="x", file_date=target, size=1)],
            minute_by_date={target: _minute_df(rows)},
        )
        svc, insert_minute, _ = _build_service(flat, batch_size=1000)

        result = await svc.backfill_range([], target, target)

        assert result.bars_persisted == 2500
        assert insert_minute.await_count == 3
        sizes = [len(call.args[0]) for call in insert_minute.await_args_list]
        assert sizes == [1000, 1000, 500]

    @pytest.mark.asyncio
    async def test_progress_callback_invoked_once_per_listed_day(self):
        d1, d2 = date(2026, 5, 11), date(2026, 5, 12)
        flat = _flatfiles_mock(
            available=[
                FlatFileInfo(key="a", file_date=d1, size=1),
                FlatFileInfo(key="b", file_date=d2, size=1),
            ],
            minute_by_date={
                d1: _minute_df([{"ticker": "AAPL"}]),
                d2: _minute_df([{"ticker": "AAPL"}]),
            },
        )
        svc, _, _ = _build_service(flat)

        seen: list[DayResult] = []
        await svc.backfill_range(["AAPL"], d1, d2, on_progress=seen.append)

        assert [r.file_date for r in seen] == [d1, d2]
        assert all(r.status == "ok" for r in seen)

    @pytest.mark.asyncio
    async def test_progress_callback_errors_do_not_abort(self):
        target = date(2026, 5, 12)
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key="x", file_date=target, size=1)],
            minute_by_date={target: _minute_df([{"ticker": "AAPL"}])},
        )
        svc, _, _ = _build_service(flat)

        def explode(_d: DayResult) -> None:
            raise RuntimeError("don't bomb the run")

        # Should complete without raising despite the callback.
        result = await svc.backfill_range(
            ["AAPL"], target, target, on_progress=explode,
        )
        assert result.days_ok == 1

    @pytest.mark.asyncio
    async def test_insert_error_classified_per_day(self):
        target = date(2026, 5, 12)
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key="x", file_date=target, size=1)],
            minute_by_date={target: _minute_df([{"ticker": "AAPL"}])},
        )
        insert_minute = AsyncMock(side_effect=RuntimeError("clickhouse offline"))
        svc, _, _ = _build_service(flat, insert_minute=insert_minute)

        result = await svc.backfill_range(["AAPL"], target, target)

        assert result.days_errored == 1
        assert result.days_ok == 0
        assert result.bars_persisted == 0
        assert "clickhouse offline" in (result.days[0].error or "")

    @pytest.mark.asyncio
    async def test_daily_kind_uses_daily_path(self):
        target = date(2026, 5, 12)
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key="x", file_date=target, size=1)],
            daily_by_date={target: _daily_df([{"ticker": "AAPL"}])},
        )
        svc, insert_minute, insert_daily = _build_service(flat)

        result = await svc.backfill_range(
            ["AAPL"], target, target, kind="day",
        )

        assert result.days_ok == 1
        assert result.bars_persisted == 1
        insert_daily.assert_awaited_once()
        insert_minute.assert_not_awaited()
        flat.available_dates.assert_called_once_with(target, target, kind="day")

    @pytest.mark.asyncio
    async def test_empty_listing_returns_clean_zero_result(self):
        flat = _flatfiles_mock(available=[])
        svc, insert_minute, _ = _build_service(flat)
        result = await svc.backfill_range(
            ["AAPL"], date(2026, 5, 1), date(2026, 5, 3),
        )
        assert result.days_listed == 0
        assert result.bars_persisted == 0
        insert_minute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_listing_failure_propagates(self):
        flat = MagicMock(name="flat_files")
        flat.available_dates.side_effect = PolygonFlatFilesError("auth")
        svc, _, _ = _build_service(flat)
        with pytest.raises(PolygonFlatFilesError):
            await svc.backfill_range([], date(2026, 5, 1), date(2026, 5, 3))

    @pytest.mark.asyncio
    async def test_invalid_kind_rejected(self):
        svc, _, _ = _build_service(_flatfiles_mock())
        with pytest.raises(ValueError, match="kind"):
            await svc.backfill_range(
                [], date(2026, 5, 1), date(2026, 5, 2), kind="hour",  # type: ignore[arg-type]
            )

    @pytest.mark.asyncio
    async def test_end_before_start_rejected(self):
        svc, _, _ = _build_service(_flatfiles_mock())
        with pytest.raises(ValueError, match="before"):
            await svc.backfill_range(
                [], date(2026, 5, 10), date(2026, 5, 1),
            )
