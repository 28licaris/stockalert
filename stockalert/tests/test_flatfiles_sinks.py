"""
Unit tests for ``app.services.flatfiles_sinks``.

Tests both concrete sinks at the SinkResult contract:

  ClickHouseSink
    - canonical-frame -> records conversion (preserves dtypes, NaN-safe)
    - batched insert with custom batch_size
    - failure caught and reported via SinkResult.status='error'

  LakeSink
    - delegates to LakeArchiveWriter.write_day
    - maps LakeWriteResult.status verbatim
    - LakeArchiveError caught and reported via SinkResult.status='error'
    - force flag plumbed through

All tests are pure-async, no I/O, run in milliseconds.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from app.services.flatfiles_sinks import (
    ClickHouseSink,
    LakeSink,
    SinkResult,
    _frame_to_records,
)
from app.services.lake_archive import LakeArchiveError, LakeWriteResult


# ---------- helpers ----------


def _minute_frame(rows: int = 3) -> pd.DataFrame:
    base = datetime(2026, 5, 12, 14, 30, tzinfo=timezone.utc)
    return pd.DataFrame({
        "symbol": [f"S{i}" for i in range(rows)],
        "timestamp": [base for _ in range(rows)],
        "open": [1.0] * rows,
        "high": [1.1] * rows,
        "low": [0.9] * rows,
        "close": [1.05] * rows,
        "volume": [100.0] * rows,
        "vwap": [0.0] * rows,
        "trade_count": [5] * rows,
        "source": ["polygon-flatfiles"] * rows,
    })


def _daily_frame(rows: int = 3) -> pd.DataFrame:
    base = datetime(2026, 5, 12, 0, 0, tzinfo=timezone.utc)
    return pd.DataFrame({
        "symbol": [f"S{i}" for i in range(rows)],
        "timestamp": [base for _ in range(rows)],
        "open": [1.0] * rows,
        "high": [1.1] * rows,
        "low": [0.9] * rows,
        "close": [1.05] * rows,
        "volume": [100.0] * rows,
        "source": ["polygon-flatfiles"] * rows,
    })


# ---------- frame -> records ----------


class TestFrameToRecords:
    def test_minute_frame_yields_canonical_records(self):
        df = _minute_frame(rows=2)
        out = _frame_to_records(df, kind="minute")
        assert len(out) == 2
        r = out[0]
        assert set(r.keys()) == {
            "symbol", "timestamp", "open", "high", "low", "close",
            "volume", "vwap", "trade_count", "source",
        }
        assert isinstance(r["timestamp"], datetime)
        assert r["timestamp"].tzinfo is timezone.utc
        assert r["vwap"] == 0.0
        assert r["trade_count"] == 5

    def test_daily_frame_omits_vwap_and_trade_count(self):
        df = _daily_frame(rows=1)
        out = _frame_to_records(df, kind="day")
        assert "vwap" not in out[0]
        assert "trade_count" not in out[0]

    def test_drops_rows_with_nan_ohlcv(self):
        df = _minute_frame(rows=3)
        df.loc[1, "open"] = float("nan")
        df.loc[2, "volume"] = float("nan")
        out = _frame_to_records(df, kind="minute")
        assert [r["symbol"] for r in out] == ["S0"]

    def test_missing_required_columns_raises(self):
        df = _minute_frame().drop(columns=["vwap"])
        with pytest.raises(ValueError, match="missing columns"):
            _frame_to_records(df, kind="minute")

    def test_empty_frame_returns_empty(self):
        assert _frame_to_records(pd.DataFrame(), kind="minute") == []
        assert _frame_to_records(None, kind="day") == []  # type: ignore[arg-type]


# ---------- ClickHouseSink ----------


class TestClickHouseSink:
    @pytest.mark.asyncio
    async def test_writes_records_via_minute_insert(self):
        minute = AsyncMock(name="insert_minute_fn")
        daily = AsyncMock(name="insert_daily_fn")
        sink = ClickHouseSink(
            insert_minute_fn=minute, insert_daily_fn=daily,
        )
        df = _minute_frame(rows=5)

        result = await sink.write(
            df, file_date=date(2026, 5, 12),
            kind="minute", provider="polygon-flatfiles",
        )
        assert isinstance(result, SinkResult)
        assert result.sink == "clickhouse"
        assert result.status == "ok"
        assert result.bars_written == 5
        minute.assert_awaited_once()
        daily.assert_not_awaited()
        # First (only) batch carries all 5 records.
        rows = minute.await_args_list[0].args[0]
        assert len(rows) == 5

    @pytest.mark.asyncio
    async def test_writes_records_via_daily_insert(self):
        minute = AsyncMock(name="insert_minute_fn")
        daily = AsyncMock(name="insert_daily_fn")
        sink = ClickHouseSink(insert_minute_fn=minute, insert_daily_fn=daily)
        df = _daily_frame(rows=3)

        result = await sink.write(
            df, file_date=date(2026, 5, 12),
            kind="day", provider="polygon-flatfiles",
        )
        assert result.status == "ok"
        assert result.bars_written == 3
        daily.assert_awaited_once()
        minute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_respects_batch_size(self):
        minute = AsyncMock(name="insert_minute_fn")
        daily = AsyncMock(name="insert_daily_fn")
        sink = ClickHouseSink(
            insert_minute_fn=minute, insert_daily_fn=daily, batch_size=2,
        )
        df = _minute_frame(rows=5)

        result = await sink.write(
            df, file_date=date(2026, 5, 12),
            kind="minute", provider="polygon-flatfiles",
        )
        assert result.bars_written == 5
        # 5 records / batch_size=2 = 3 calls: 2, 2, 1.
        assert minute.await_count == 3
        sizes = [len(c.args[0]) for c in minute.await_args_list]
        assert sizes == [2, 2, 1]
        assert result.metadata["batches"] == 3

    @pytest.mark.asyncio
    async def test_empty_frame_returns_skipped(self):
        minute = AsyncMock(); daily = AsyncMock()
        sink = ClickHouseSink(insert_minute_fn=minute, insert_daily_fn=daily)

        result = await sink.write(
            pd.DataFrame(), file_date=date(2026, 5, 12),
            kind="minute", provider="polygon-flatfiles",
        )
        assert result.status == "skipped"
        minute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_insert_failure_returns_error_result(self):
        minute = AsyncMock(side_effect=RuntimeError("CH down"))
        daily = AsyncMock()
        sink = ClickHouseSink(insert_minute_fn=minute, insert_daily_fn=daily)
        df = _minute_frame()

        result = await sink.write(
            df, file_date=date(2026, 5, 12),
            kind="minute", provider="polygon-flatfiles",
        )
        assert result.status == "error"
        assert "CH down" in (result.error or "")
        assert result.bars_written == 0

    def test_construction_rejects_missing_inserts(self):
        with pytest.raises(ValueError, match="required"):
            ClickHouseSink(insert_minute_fn=None, insert_daily_fn=AsyncMock())  # type: ignore[arg-type]


# ---------- LakeSink ----------


class TestLakeSink:
    def _writer_mock(
        self, *, result: LakeWriteResult | None = None,
        raises: Exception | None = None,
    ) -> MagicMock:
        writer = MagicMock(name="LakeArchiveWriter")
        write_day = AsyncMock()
        if raises is not None:
            write_day.side_effect = raises
        else:
            write_day.return_value = result or LakeWriteResult(
                date=date(2026, 5, 12), kind="minute",
                provider="polygon-flatfiles",
                s3_key="raw/provider=polygon-flatfiles/kind=minute/year=2026/date=2026-05-12.parquet",
                bars_written=5, bytes_written=1024, status="ok",
            )
        writer.write_day = write_day
        return writer

    @pytest.mark.asyncio
    async def test_delegates_to_writer_and_maps_result(self):
        writer = self._writer_mock()
        sink = LakeSink(writer=writer)

        result = await sink.write(
            _minute_frame(), file_date=date(2026, 5, 12),
            kind="minute", provider="polygon-flatfiles",
        )
        assert result.sink == "lake"
        assert result.status == "ok"
        assert result.bars_written == 5
        assert result.metadata["s3_key"].endswith("date=2026-05-12.parquet")
        assert result.metadata["bytes_written"] == 1024
        writer.write_day.assert_awaited_once()
        # Force flag default False is plumbed through.
        kwargs = writer.write_day.await_args.kwargs
        assert kwargs["force"] is False
        assert kwargs["kind"] == "minute"
        assert kwargs["provider"] == "polygon-flatfiles"

    @pytest.mark.asyncio
    async def test_force_flag_propagates_to_writer(self):
        writer = self._writer_mock()
        sink = LakeSink(writer=writer, force=True)

        await sink.write(
            _minute_frame(), file_date=date(2026, 5, 12),
            kind="minute", provider="polygon-flatfiles",
        )
        assert writer.write_day.await_args.kwargs["force"] is True

    @pytest.mark.asyncio
    async def test_skipped_result_propagates(self):
        skipped = LakeWriteResult(
            date=date(2026, 5, 12), kind="minute",
            provider="polygon-flatfiles",
            s3_key="k", bars_written=0, bytes_written=0,
            status="skipped",
        )
        sink = LakeSink(writer=self._writer_mock(result=skipped))
        result = await sink.write(
            _minute_frame(), file_date=date(2026, 5, 12),
            kind="minute", provider="polygon-flatfiles",
        )
        assert result.status == "skipped"

    @pytest.mark.asyncio
    async def test_lake_archive_error_returns_error_result(self):
        writer = self._writer_mock(raises=LakeArchiveError("S3 perm denied"))
        sink = LakeSink(writer=writer)
        result = await sink.write(
            _minute_frame(), file_date=date(2026, 5, 12),
            kind="minute", provider="polygon-flatfiles",
        )
        assert result.status == "error"
        assert "S3 perm denied" in (result.error or "")
        assert result.bars_written == 0

    def test_requires_writer(self):
        with pytest.raises(ValueError, match="writer"):
            LakeSink(writer=None)  # type: ignore[arg-type]
