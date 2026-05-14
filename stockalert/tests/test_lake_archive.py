"""
Unit tests for ``app.services.lake_archive.LakeArchiveWriter``.

Both dependencies are mocked at their public surfaces:

  - ``S3LakeClient`` (real class, ``client=MagicMock()`` injected)
  - ``WatermarkRepo`` (real class, ``insert_fn`` / ``query_fn`` injected)

No network, no ClickHouse. Tests verify the full contract:

  - Canonical key shape (provider/kind/year/date partitioning)
  - Idempotency short-circuit (already-archived ``ok`` skips)
  - End-to-end happy path stamps the watermark with bars + s3_key
  - S3 failures stamp ``error`` watermark and raise LakeArchiveError
  - Watermark write failures don't undo the S3 write (data wins)
  - Empty / invalid frames rejected with clear errors
  - Force flag bypasses idempotency
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, List
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.db.lake_watermarks import (
    STATUS_ERROR,
    STATUS_OK,
    WatermarkRepo,
    _day_bounds,
)
from app.services.lake_archive import (
    LakeArchiveError,
    LakeArchiveWriter,
    LakeWriteResult,
)
from app.services.s3_lake_client import S3LakeClient, S3LakeClientError


# ---------- helpers ----------


class _FakeCH:
    """Same in-memory ClickHouse stand-in used by test_lake_watermarks."""
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[list[Any]], list[str]]] = []
        self.next_query_results: list[List[tuple]] = []
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def insert(self, table: str, rows: list[list[Any]], cols: list[str]) -> None:
        self.inserts.append((table, [list(r) for r in rows], list(cols)))

    def query(self, sql: str, params: dict[str, Any]) -> List[tuple]:
        self.queries.append((sql, dict(params)))
        if not self.next_query_results:
            return []
        return self.next_query_results.pop(0)


def _watermark_row(
    *, source: str, table_name: str, period: date, stage: str = "raw",
    status: str = STATUS_OK, bars: int = 1000, s3_key: str = "k",
) -> tuple:
    start, end = _day_bounds(period)
    return (
        source, table_name, stage, start, end,
        bars, s3_key, status, "",
        datetime(2026, 5, 13, 7, 0, tzinfo=timezone.utc),
    )


def _build_writer(
    *,
    bucket: str = "test-lake",
    s3_client_mock: MagicMock | None = None,
    fake_ch: _FakeCH | None = None,
) -> tuple[LakeArchiveWriter, MagicMock, _FakeCH]:
    """Return (writer, mock_boto_s3, fake_clickhouse). Both stubs are
    returned so each test can drive them directly."""
    boto_s3 = s3_client_mock or MagicMock(name="boto3_s3_client")
    fake_ch = fake_ch or _FakeCH()
    s3 = S3LakeClient(bucket=bucket, client=boto_s3)
    repo = WatermarkRepo(insert_fn=fake_ch.insert, query_fn=fake_ch.query)
    writer = LakeArchiveWriter(s3=s3, watermarks=repo)
    return writer, boto_s3, fake_ch


def _minute_frame(rows: int = 3) -> pd.DataFrame:
    """Canonical-shape minute frame (matches what FlatFilesBackfillService
    will hand to LakeSink in C2)."""
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


# ---------- construction / keys ----------


class TestConstructionAndKeys:
    def test_requires_s3_and_watermarks(self):
        repo = WatermarkRepo(insert_fn=lambda *a, **k: None, query_fn=lambda *a, **k: [])
        s3 = S3LakeClient(bucket="b", client=MagicMock())
        with pytest.raises(ValueError, match="s3"):
            LakeArchiveWriter(s3=None, watermarks=repo)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="watermarks"):
            LakeArchiveWriter(s3=s3, watermarks=None)  # type: ignore[arg-type]

    def test_key_for_minute(self):
        w, _, _ = _build_writer()
        key = w.key_for(
            file_date=date(2026, 5, 12),
            kind="minute",
            provider="polygon-flatfiles",
        )
        assert key == (
            "raw/provider=polygon-flatfiles/kind=minute/year=2026/"
            "date=2026-05-12.parquet"
        )

    def test_key_for_daily_different_year(self):
        w, _, _ = _build_writer()
        key = w.key_for(
            file_date=date(2021, 1, 4),
            kind="day",
            provider="polygon-flatfiles",
        )
        assert key == (
            "raw/provider=polygon-flatfiles/kind=day/year=2021/"
            "date=2021-01-04.parquet"
        )

    def test_key_for_rejects_bad_kind(self):
        w, _, _ = _build_writer()
        with pytest.raises(ValueError, match="kind"):
            w.key_for(file_date=date(2026, 5, 12), kind="hour", provider="p")  # type: ignore[arg-type]

    def test_key_for_rejects_bad_provider(self):
        w, _, _ = _build_writer()
        with pytest.raises(ValueError, match="provider"):
            w.key_for(file_date=date(2026, 5, 12), kind="minute", provider="")
        with pytest.raises(ValueError, match="provider"):
            w.key_for(
                file_date=date(2026, 5, 12), kind="minute",
                provider="bad/provider",
            )

    def test_custom_stage_changes_key_prefix(self):
        repo = WatermarkRepo(insert_fn=lambda *a, **k: None, query_fn=lambda *a, **k: [])
        s3 = S3LakeClient(bucket="b", client=MagicMock())
        w = LakeArchiveWriter(s3=s3, watermarks=repo, stage="processed")
        key = w.key_for(
            file_date=date(2026, 5, 12), kind="minute",
            provider="polygon-flatfiles",
        )
        assert key.startswith("processed/provider=polygon-flatfiles/")


# ---------- write_day happy path ----------


class TestWriteDayHappyPath:
    @pytest.mark.asyncio
    async def test_writes_parquet_and_stamps_watermark(self):
        w, boto_s3, fake_ch = _build_writer()
        df = _minute_frame(rows=10)

        result = await w.write_day(
            df, file_date=date(2026, 5, 12),
            kind="minute", provider="polygon-flatfiles",
        )

        assert isinstance(result, LakeWriteResult)
        assert result.status == "ok"
        assert result.bars_written == 10
        assert result.bytes_written > 0
        assert result.s3_key == (
            "raw/provider=polygon-flatfiles/kind=minute/year=2026/"
            "date=2026-05-12.parquet"
        )

        # boto3 was called with the canonical key + bucket and metadata.
        boto_s3.put_object.assert_called_once()
        kwargs = boto_s3.put_object.call_args.kwargs
        assert kwargs["Bucket"] == "test-lake"
        assert kwargs["Key"] == result.s3_key
        assert kwargs["Metadata"]["provider"] == "polygon-flatfiles"
        assert kwargs["Metadata"]["kind"] == "minute"
        assert kwargs["Metadata"]["bars"] == "10"
        # Body is non-empty Parquet bytes.
        assert isinstance(kwargs["Body"], (bytes, bytearray))
        assert len(kwargs["Body"]) > 0

        # Watermark stamp: one row, ok status, bars=10.
        assert len(fake_ch.inserts) == 1
        table, rows, cols = fake_ch.inserts[0]
        assert table == "lake_archive_watermarks"
        r = rows[0]
        assert r[0] == "polygon-flatfiles"
        assert r[1] == "ohlcv_1m"
        assert r[2] == "raw"
        assert r[5] == 10
        assert r[6] == result.s3_key
        assert r[7] == STATUS_OK

    @pytest.mark.asyncio
    async def test_daily_kind_maps_to_ohlcv_daily(self):
        w, _, fake_ch = _build_writer()
        df = _daily_frame(rows=5)

        await w.write_day(
            df, file_date=date(2021, 1, 4),
            kind="day", provider="polygon-flatfiles",
        )
        assert fake_ch.inserts[0][1][0][1] == "ohlcv_daily"


# ---------- idempotency ----------


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_skips_when_ok_watermark_exists(self):
        fake_ch = _FakeCH()
        fake_ch.next_query_results = [
            [_watermark_row(
                source="polygon-flatfiles", table_name="ohlcv_1m",
                period=date(2026, 5, 12), status=STATUS_OK,
            )],
        ]
        w, boto_s3, _ = _build_writer(fake_ch=fake_ch)

        result = await w.write_day(
            _minute_frame(), file_date=date(2026, 5, 12),
            kind="minute", provider="polygon-flatfiles",
        )

        assert result.status == "skipped"
        assert result.bars_written == 0
        # S3 PUT must NOT happen on a successful idempotency hit.
        boto_s3.put_object.assert_not_called()
        # No watermark insert either (we already have a good one).
        assert fake_ch.inserts == []

    @pytest.mark.asyncio
    async def test_does_not_skip_when_prior_status_is_error(self):
        fake_ch = _FakeCH()
        fake_ch.next_query_results = [
            [_watermark_row(
                source="polygon-flatfiles", table_name="ohlcv_1m",
                period=date(2026, 5, 12), status=STATUS_ERROR,
            )],
        ]
        w, boto_s3, _ = _build_writer(fake_ch=fake_ch)

        result = await w.write_day(
            _minute_frame(), file_date=date(2026, 5, 12),
            kind="minute", provider="polygon-flatfiles",
        )
        assert result.status == "ok"
        boto_s3.put_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_force_bypasses_idempotency(self):
        fake_ch = _FakeCH()
        fake_ch.next_query_results = [
            [_watermark_row(
                source="polygon-flatfiles", table_name="ohlcv_1m",
                period=date(2026, 5, 12), status=STATUS_OK,
            )],
        ]
        w, boto_s3, _ = _build_writer(fake_ch=fake_ch)

        result = await w.write_day(
            _minute_frame(), file_date=date(2026, 5, 12),
            kind="minute", provider="polygon-flatfiles",
            force=True,
        )
        assert result.status == "ok"
        boto_s3.put_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_already_archived_helper_reads_watermark(self):
        fake_ch = _FakeCH()
        fake_ch.next_query_results = [
            [_watermark_row(
                source="polygon-flatfiles", table_name="ohlcv_1m",
                period=date(2026, 5, 12), status=STATUS_OK,
            )],
        ]
        w, _, _ = _build_writer(fake_ch=fake_ch)

        assert await w.already_archived(
            file_date=date(2026, 5, 12),
            kind="minute", provider="polygon-flatfiles",
        ) is True

        # Second call: no rows -> False.
        fake_ch.next_query_results = [[]]
        assert await w.already_archived(
            file_date=date(2026, 5, 12),
            kind="minute", provider="polygon-flatfiles",
        ) is False


# ---------- error paths ----------


class TestErrorPaths:
    @pytest.mark.asyncio
    async def test_s3_failure_stamps_error_watermark_then_raises(self):
        # boto3 client raises on put_object: S3LakeClient wraps it as
        # S3LakeClientError; LakeArchiveWriter must catch, stamp ``error``,
        # then raise LakeArchiveError.
        boto_s3 = MagicMock(name="boto3")
        boto_s3.put_object.side_effect = RuntimeError("503 Slow Down")
        w, _, fake_ch = _build_writer(s3_client_mock=boto_s3)

        with pytest.raises(LakeArchiveError, match="503"):
            await w.write_day(
                _minute_frame(), file_date=date(2026, 5, 12),
                kind="minute", provider="polygon-flatfiles",
            )

        # One watermark insert: status=error, bars=0.
        assert len(fake_ch.inserts) == 1
        r = fake_ch.inserts[0][1][0]
        assert r[7] == STATUS_ERROR
        assert "503" in r[8]
        assert r[5] == 0

    @pytest.mark.asyncio
    async def test_watermark_failure_does_not_undo_s3_write(self):
        # S3 PUT succeeds. Watermark insert raises. Result must still
        # report ok (data is in S3, which is the canonical store) and
        # include a non-empty ``error`` field for telemetry.
        fake_ch = _FakeCH()

        def _exploding_insert(*a, **k):
            raise RuntimeError("CH unavailable")

        boto_s3 = MagicMock(name="boto3")
        repo = WatermarkRepo(insert_fn=_exploding_insert, query_fn=fake_ch.query)
        s3 = S3LakeClient(bucket="test-lake", client=boto_s3)
        writer = LakeArchiveWriter(s3=s3, watermarks=repo)

        result = await writer.write_day(
            _minute_frame(), file_date=date(2026, 5, 12),
            kind="minute", provider="polygon-flatfiles",
        )
        assert result.status == "ok"
        assert "watermark" in (result.error or "").lower()
        boto_s3.put_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_frame_is_skipped_without_s3_or_watermark(self):
        w, boto_s3, fake_ch = _build_writer()
        result = await w.write_day(
            pd.DataFrame(), file_date=date(2026, 5, 12),
            kind="minute", provider="polygon-flatfiles",
        )
        assert result.status == "skipped"
        boto_s3.put_object.assert_not_called()
        assert fake_ch.inserts == []

    @pytest.mark.asyncio
    async def test_frame_missing_required_columns_rejected(self):
        w, _, _ = _build_writer()
        df = _minute_frame().drop(columns=["vwap"])
        with pytest.raises(ValueError, match="missing required"):
            await w.write_day(
                df, file_date=date(2026, 5, 12),
                kind="minute", provider="polygon-flatfiles",
            )

    @pytest.mark.asyncio
    async def test_daily_frame_does_not_require_vwap(self):
        # Daily canonical columns don't include vwap/trade_count.
        w, boto_s3, _ = _build_writer()
        df = _daily_frame(rows=2)
        result = await w.write_day(
            df, file_date=date(2026, 5, 12),
            kind="day", provider="polygon-flatfiles",
        )
        assert result.status == "ok"
        boto_s3.put_object.assert_called_once()

    @pytest.mark.asyncio
    async def test_truncates_long_error_messages(self):
        """A monster traceback should not be stored verbatim in a
        LowCardinality(String) column."""
        boto_s3 = MagicMock(name="boto3")
        big_err = "boom-" * 1000  # 5000 chars
        boto_s3.put_object.side_effect = RuntimeError(big_err)
        w, _, fake_ch = _build_writer(s3_client_mock=boto_s3)

        with pytest.raises(LakeArchiveError):
            await w.write_day(
                _minute_frame(), file_date=date(2026, 5, 12),
                kind="minute", provider="polygon-flatfiles",
            )
        stored = fake_ch.inserts[0][1][0][8]
        assert len(stored) <= 1024
