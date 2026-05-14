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


# ---------- C2: multi-sink fan-out ----------


from app.services.flatfiles_sinks import Sink, SinkResult  # noqa: E402


class _RecorderSink:
    """Sink test double that records every frame it sees and returns a
    canned ``SinkResult``. Captures the canonical frame so tests can
    assert on the column shape both sinks receive."""

    def __init__(
        self,
        *,
        name: str = "recorder",
        status: str = "ok",
        bars: int = 0,
        error: str | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.name = name
        self._status = status
        self._bars = bars
        self._error = error
        self._raises = raises
        self.seen_frames: list[pd.DataFrame] = []
        self.calls: list[tuple] = []

    async def write(
        self,
        df: pd.DataFrame,
        *,
        file_date,
        kind,
        provider,
    ) -> SinkResult:
        self.seen_frames.append(df.copy())
        self.calls.append((file_date, kind, provider))
        if self._raises is not None:
            raise self._raises
        return SinkResult(
            sink=self.name,
            status=self._status,
            bars_written=self._bars if self._status == "ok" else 0,
            error=self._error,
        )


class TestMultiSinkFanOut:
    @pytest.mark.asyncio
    async def test_two_sinks_both_receive_canonical_frame(self):
        target = date(2026, 5, 12)
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key="x", file_date=target, size=1)],
            minute_by_date={target: _minute_df([
                {"ticker": "AAPL"}, {"ticker": "MSFT"},
            ])},
        )
        a = _RecorderSink(name="sink_a", status="ok", bars=2)
        b = _RecorderSink(name="sink_b", status="ok", bars=2)
        svc = FlatFilesBackfillService(flat_files=flat, sinks=[a, b])

        result = await svc.backfill_range(["AAPL", "MSFT"], target, target)

        assert result.days_ok == 1
        assert result.days_errored == 0
        assert result.bars_persisted == 2

        # Both sinks were called exactly once with the same frame.
        assert len(a.seen_frames) == 1 and len(b.seen_frames) == 1
        df_a, df_b = a.seen_frames[0], b.seen_frames[0]
        # Canonical column set.
        assert list(df_a.columns) == [
            "symbol", "timestamp", "open", "high", "low", "close",
            "volume", "vwap", "trade_count", "source",
        ]
        # Symbols are uppercased in canonical frame.
        assert sorted(df_a["symbol"].tolist()) == ["AAPL", "MSFT"]
        # Both sinks see identical content (no mutation between sinks).
        pd.testing.assert_frame_equal(df_a, df_b)
        # Per-sink results recorded on the DayResult.
        day = result.days[0]
        assert set(day.sink_results.keys()) == {"sink_a", "sink_b"}
        assert day.sink_results["sink_a"].status == "ok"
        assert day.sink_results["sink_b"].status == "ok"

    @pytest.mark.asyncio
    async def test_one_ok_one_error_classifies_as_partial(self):
        target = date(2026, 5, 12)
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key="x", file_date=target, size=1)],
            minute_by_date={target: _minute_df([{"ticker": "AAPL"}])},
        )
        ok = _RecorderSink(name="ok_sink", status="ok", bars=1)
        bad = _RecorderSink(name="bad_sink", status="error",
                            error="lake PUT denied")
        svc = FlatFilesBackfillService(flat_files=flat, sinks=[ok, bad])

        result = await svc.backfill_range(["AAPL"], target, target)

        assert result.days_partial == 1
        assert result.days_ok == 0
        assert result.days_errored == 0
        # Partial days do NOT count toward the errored bucket; the data
        # IS in at least one persistent store.
        assert result.bars_persisted == 1
        day = result.days[0]
        assert day.status == "partial"
        assert "bad_sink" in (day.error or "")
        assert "lake PUT denied" in (day.error or "")

    @pytest.mark.asyncio
    async def test_all_sinks_fail_classifies_as_error(self):
        target = date(2026, 5, 12)
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key="x", file_date=target, size=1)],
            minute_by_date={target: _minute_df([{"ticker": "AAPL"}])},
        )
        a = _RecorderSink(name="a", status="error", error="ch down")
        b = _RecorderSink(name="b", status="error", error="s3 down")
        svc = FlatFilesBackfillService(flat_files=flat, sinks=[a, b])

        result = await svc.backfill_range(["AAPL"], target, target)

        assert result.days_errored == 1
        assert result.days_ok == 0
        assert result.days_partial == 0
        assert result.bars_persisted == 0
        day = result.days[0]
        assert day.status == "error"
        assert "ch down" in (day.error or "") and "s3 down" in (day.error or "")

    @pytest.mark.asyncio
    async def test_no_sinks_configured_marks_day_skipped(self):
        target = date(2026, 5, 12)
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key="x", file_date=target, size=1)],
            minute_by_date={target: _minute_df([{"ticker": "AAPL"}])},
        )
        svc = FlatFilesBackfillService(flat_files=flat, sinks=[])

        result = await svc.backfill_range(["AAPL"], target, target)

        assert result.days_skipped == 1
        assert result.bars_persisted == 0

    @pytest.mark.asyncio
    async def test_dry_run_skips_before_calling_sinks(self):
        target = date(2026, 5, 12)
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key="x", file_date=target, size=1)],
            minute_by_date={target: _minute_df([{"ticker": "AAPL"}])},
        )
        sink = _RecorderSink(name="cap", status="ok", bars=1)
        svc = FlatFilesBackfillService(flat_files=flat, sinks=[sink])

        result = await svc.backfill_range(
            ["AAPL"], target, target, dry_run=True,
        )

        assert result.days_skipped == 1
        assert result.bars_persisted == 0
        # Dry-run must short-circuit BEFORE any sink fires.
        assert sink.seen_frames == []

    @pytest.mark.asyncio
    async def test_sink_raising_exception_is_isolated(self):
        """Defence-in-depth: a sink that raises (instead of returning
        SinkResult(status='error')) must NOT take down the run. The
        backfill service catches and converts it to an error result."""
        target = date(2026, 5, 12)
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key="x", file_date=target, size=1)],
            minute_by_date={target: _minute_df([{"ticker": "AAPL"}])},
        )
        ok = _RecorderSink(name="good", status="ok", bars=1)
        bomb = _RecorderSink(name="bomb", raises=RuntimeError("kaboom"))
        svc = FlatFilesBackfillService(flat_files=flat, sinks=[ok, bomb])

        result = await svc.backfill_range(["AAPL"], target, target)

        assert result.days_partial == 1
        day = result.days[0]
        assert day.sink_results["good"].status == "ok"
        assert day.sink_results["bomb"].status == "error"
        assert "kaboom" in (day.sink_results["bomb"].error or "")

    @pytest.mark.asyncio
    async def test_legacy_constructor_still_works(self):
        """C1+C2 must preserve the pre-C2 constructor surface so the
        BackfillService dispatch path and existing tests do not need
        modification."""
        target = date(2026, 5, 12)
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key="x", file_date=target, size=1)],
            minute_by_date={target: _minute_df([{"ticker": "AAPL"}])},
        )
        minute = AsyncMock(name="insert_minute_fn")
        daily = AsyncMock(name="insert_daily_fn")
        svc = FlatFilesBackfillService(
            flat_files=flat,
            insert_minute_fn=minute,
            insert_daily_fn=daily,
        )
        # Internally we built a single ClickHouseSink.
        assert len(svc.sinks) == 1

        result = await svc.backfill_range(["AAPL"], target, target)
        assert result.days_ok == 1
        minute.assert_awaited()


class TestCanonicalizeFrame:
    def test_minute_canonicalisation_shape(self):
        svc = FlatFilesBackfillService(flat_files=MagicMock(), sinks=[])
        df = _minute_df([{"ticker": "aapl", "transactions": 7}])
        out = svc._canonicalize_frame(df, kind="minute")
        assert list(out.columns) == [
            "symbol", "timestamp", "open", "high", "low", "close",
            "volume", "vwap", "trade_count", "source",
        ]
        assert out.iloc[0]["symbol"] == "AAPL"
        assert out.iloc[0]["vwap"] == 0.0
        assert int(out.iloc[0]["trade_count"]) == 7
        assert out.iloc[0]["source"] == "polygon-flatfiles"
        # Timestamp must be tz-aware UTC.
        assert out["timestamp"].dt.tz is not None

    def test_daily_canonicalisation_drops_transactions(self):
        svc = FlatFilesBackfillService(flat_files=MagicMock(), sinks=[])
        df = _daily_df([{"ticker": "AAPL"}])
        out = svc._canonicalize_frame(df, kind="day")
        assert list(out.columns) == [
            "symbol", "timestamp", "open", "high", "low", "close",
            "volume", "source",
        ]

    def test_nan_rows_dropped_in_canonical(self):
        svc = FlatFilesBackfillService(flat_files=MagicMock(), sinks=[])
        df = _minute_df([
            {"ticker": "AAPL"},
            {"ticker": "BAD", "open": float("nan")},
        ])
        out = svc._canonicalize_frame(df, kind="minute")
        assert out["symbol"].tolist() == ["AAPL"]

    def test_missing_required_column_raises(self):
        svc = FlatFilesBackfillService(flat_files=MagicMock(), sinks=[])
        df = _minute_df([{"ticker": "AAPL"}]).drop(columns=["open"])
        with pytest.raises(ValueError, match="missing"):
            svc._canonicalize_frame(df, kind="minute")

    def test_empty_in_empty_out(self):
        svc = FlatFilesBackfillService(flat_files=MagicMock(), sinks=[])
        assert svc._canonicalize_frame(pd.DataFrame(), kind="minute").empty
        assert svc._canonicalize_frame(None, kind="day").empty  # type: ignore[arg-type]


# ---------- C3: concurrency + skip_dates ----------


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrency_one_matches_serial_output(self):
        """Sanity: concurrency=1 must produce the same DayResult set
        and ordering as the pre-C3 serial loop."""
        dates = [date(2026, 5, d) for d in (11, 12, 13)]
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key=f"k{i}", file_date=d, size=1)
                       for i, d in enumerate(dates)],
            minute_by_date={d: _minute_df([{"ticker": "AAPL"}]) for d in dates},
        )
        svc, insert_minute, _ = _build_service(flat)

        result = await svc.backfill_range(
            ["AAPL"], dates[0], dates[-1], concurrency=1,
        )
        assert result.days_ok == 3
        assert [d.file_date for d in result.days] == dates  # sorted ascending

    @pytest.mark.asyncio
    async def test_concurrency_many_processes_all_days(self):
        """concurrency > 1 must still process every day exactly once
        and produce a date-sorted result.days regardless of completion
        order."""
        dates = [date(2026, 5, d) for d in (11, 12, 13, 14, 15)]
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key=f"k{i}", file_date=d, size=1)
                       for i, d in enumerate(dates)],
            minute_by_date={d: _minute_df([{"ticker": "AAPL"}]) for d in dates},
        )
        svc, insert_minute, _ = _build_service(flat)

        result = await svc.backfill_range(
            ["AAPL"], dates[0], dates[-1], concurrency=4,
        )
        assert result.days_listed == 5
        assert result.days_ok == 5
        assert result.bars_persisted == 5
        # Sorted ascending regardless of internal completion order.
        assert [d.file_date for d in result.days] == dates
        # Every day was inserted (1 batch each).
        assert insert_minute.await_count == 5

    @pytest.mark.asyncio
    async def test_concurrency_progress_fires_once_per_day(self):
        dates = [date(2026, 5, d) for d in (11, 12, 13)]
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key=f"k{i}", file_date=d, size=1)
                       for i, d in enumerate(dates)],
            minute_by_date={d: _minute_df([{"ticker": "AAPL"}]) for d in dates},
        )
        svc, _, _ = _build_service(flat)

        seen: list[DayResult] = []
        await svc.backfill_range(
            ["AAPL"], dates[0], dates[-1],
            concurrency=3, on_progress=seen.append,
        )
        assert len(seen) == 3
        # Order may vary under concurrency; just assert the set matches.
        assert {d.file_date for d in seen} == set(dates)

    @pytest.mark.asyncio
    async def test_concurrency_isolates_per_day_errors(self):
        """One failing day must not stop the others under concurrency."""
        d_ok1 = date(2026, 5, 11)
        d_bad = date(2026, 5, 12)
        d_ok2 = date(2026, 5, 13)
        flat = _flatfiles_mock(
            available=[
                FlatFileInfo(key=f"k{i}", file_date=d, size=1)
                for i, d in enumerate([d_ok1, d_bad, d_ok2])
            ],
            minute_by_date={
                d_ok1: _minute_df([{"ticker": "AAPL"}]),
                d_ok2: _minute_df([{"ticker": "AAPL"}]),
            },
            raise_on_minute={d_bad: PolygonFlatFilesError("boom")},
        )
        svc, _, _ = _build_service(flat)

        result = await svc.backfill_range(
            ["AAPL"], d_ok1, d_ok2, concurrency=3,
        )
        assert result.days_ok == 2
        assert result.days_errored == 1
        # All three dates present in result.days, date-sorted.
        assert [d.file_date for d in result.days] == [d_ok1, d_bad, d_ok2]

    @pytest.mark.asyncio
    async def test_invalid_concurrency_rejected(self):
        svc, _, _ = _build_service(_flatfiles_mock())
        with pytest.raises(ValueError, match="concurrency"):
            await svc.backfill_range(
                [], date(2026, 5, 1), date(2026, 5, 2), concurrency=0,
            )


class TestSkipDates:
    @pytest.mark.asyncio
    async def test_skip_dates_filters_listing_before_download(self):
        """Days in ``skip_dates`` must not be downloaded or counted in
        any bucket — they vanish from the run entirely."""
        dates = [date(2026, 5, d) for d in (11, 12, 13)]
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key=f"k{i}", file_date=d, size=1)
                       for i, d in enumerate(dates)],
            minute_by_date={d: _minute_df([{"ticker": "AAPL"}]) for d in dates},
        )
        svc, insert_minute, _ = _build_service(flat)

        result = await svc.backfill_range(
            ["AAPL"], dates[0], dates[-1],
            skip_dates={dates[0], dates[2]},  # skip first + last
        )
        assert result.days_listed == 1
        assert result.days_ok == 1
        assert [d.file_date for d in result.days] == [dates[1]]
        # Downloads only fired for the un-skipped date.
        flat.download_minute_aggs.assert_called_once()
        assert flat.download_minute_aggs.call_args.args[0] == dates[1]

    @pytest.mark.asyncio
    async def test_skip_dates_all_listed_returns_clean_zero(self):
        dates = [date(2026, 5, 11), date(2026, 5, 12)]
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key=f"k{i}", file_date=d, size=1)
                       for i, d in enumerate(dates)],
            minute_by_date={d: _minute_df([{"ticker": "AAPL"}]) for d in dates},
        )
        svc, insert_minute, _ = _build_service(flat)

        result = await svc.backfill_range(
            ["AAPL"], dates[0], dates[-1],
            skip_dates=set(dates),
        )
        assert result.days_listed == 0
        assert result.days_ok == 0
        insert_minute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skip_dates_with_concurrency(self):
        dates = [date(2026, 5, d) for d in (11, 12, 13, 14)]
        flat = _flatfiles_mock(
            available=[FlatFileInfo(key=f"k{i}", file_date=d, size=1)
                       for i, d in enumerate(dates)],
            minute_by_date={d: _minute_df([{"ticker": "AAPL"}]) for d in dates},
        )
        svc, _, _ = _build_service(flat)

        result = await svc.backfill_range(
            ["AAPL"], dates[0], dates[-1],
            concurrency=2,
            skip_dates={dates[1]},
        )
        assert result.days_listed == 3
        assert result.days_ok == 3
        assert [d.file_date for d in result.days] == [dates[0], dates[2], dates[3]]
