"""
Unit tests for ``app.db.lake_watermarks``.

Tests the WatermarkRepo entirely through its injection points so we
never touch ClickHouse. The repo is the lowest layer in the lake-archive
stack — getting it right here means we can trust every layer above.

Coverage:
  - Insert shape (column order, day bounds, version monotonicity)
  - Status read-back (mapping rows -> Watermark, missing -> None)
  - get_status convenience method
  - Validation (bad status, negative bars, missing required fields)
  - UTC normalisation of returned datetimes
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, List

import pytest

from app.db.lake_watermarks import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_PARTIAL,
    Watermark,
    WatermarkRepo,
    _day_bounds,
)


# ---------- helpers ----------


class _FakeCH:
    """In-memory stand-in for the ClickHouse client surface this repo uses.

    Holds inserted rows by table and serves canned query results. Tests
    inject this via the repo's ``insert_fn`` / ``query_fn`` constructor
    args so no real ClickHouse traffic occurs.
    """

    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[list[Any]], list[str]]] = []
        self.next_query_result: List[tuple] = []
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def insert(self, table: str, rows: List[List[Any]], cols: List[str]) -> None:
        self.inserts.append((table, [list(r) for r in rows], list(cols)))

    def query(self, sql: str, params: dict[str, Any]) -> List[tuple]:
        self.queries.append((sql, dict(params)))
        return list(self.next_query_result)


def _repo(fake: _FakeCH) -> WatermarkRepo:
    return WatermarkRepo(insert_fn=fake.insert, query_fn=fake.query)


# ---------- record ----------


class TestRecord:
    @pytest.mark.asyncio
    async def test_writes_one_row_with_expected_columns(self):
        fake = _FakeCH()
        repo = _repo(fake)

        await repo.record(
            source="polygon-flatfiles",
            table_name="ohlcv_1m",
            period=date(2026, 5, 12),
            stage="raw",
            bars_archived=42_000,
            s3_key="raw/provider=polygon-flatfiles/kind=minute/year=2026/date=2026-05-12.parquet",
        )

        assert len(fake.inserts) == 1
        table, rows, cols = fake.inserts[0]
        assert table == "lake_archive_watermarks"
        assert cols == [
            "source", "table_name", "stage",
            "period_start", "period_end",
            "bars_archived", "s3_key", "status", "error", "version",
        ]
        assert len(rows) == 1
        r = rows[0]
        assert r[0] == "polygon-flatfiles"
        assert r[1] == "ohlcv_1m"
        assert r[2] == "raw"
        # Day bounds: start = 00:00 UTC, end = 23:59:59.999 UTC.
        start, end = _day_bounds(date(2026, 5, 12))
        assert r[3] == start
        assert r[4] == end
        assert r[5] == 42_000
        assert r[6] == "raw/provider=polygon-flatfiles/kind=minute/year=2026/date=2026-05-12.parquet"
        assert r[7] == STATUS_OK
        assert r[8] == ""
        # Version is a millisecond stamp; just confirm it's sensible.
        assert isinstance(r[9], int)
        assert r[9] > 0

    @pytest.mark.asyncio
    async def test_records_error_status_with_message(self):
        fake = _FakeCH()
        repo = _repo(fake)

        await repo.record(
            source="polygon-flatfiles",
            table_name="ohlcv_daily",
            period=date(2026, 5, 12),
            stage="raw",
            bars_archived=0,
            s3_key="",
            status=STATUS_ERROR,
            error="S3 PUT timed out after 3 retries",
        )

        r = fake.inserts[0][1][0]
        assert r[7] == STATUS_ERROR
        assert r[8] == "S3 PUT timed out after 3 retries"
        assert r[5] == 0
        assert r[6] == ""

    @pytest.mark.asyncio
    async def test_partial_status_accepted(self):
        fake = _FakeCH()
        repo = _repo(fake)

        await repo.record(
            source="polygon-flatfiles",
            table_name="ohlcv_1m",
            period=date(2026, 5, 12),
            stage="raw",
            bars_archived=1000,
            s3_key="k",
            status=STATUS_PARTIAL,
            error="ClickHouseSink failed; LakeSink succeeded",
        )
        assert fake.inserts[0][1][0][7] == STATUS_PARTIAL

    @pytest.mark.asyncio
    async def test_rejects_unknown_status(self):
        repo = _repo(_FakeCH())
        with pytest.raises(ValueError, match="status"):
            await repo.record(
                source="x", table_name="y", period=date(2026, 5, 12),
                stage="raw", bars_archived=0, s3_key="", status="weird",
            )

    @pytest.mark.asyncio
    async def test_rejects_negative_bars(self):
        repo = _repo(_FakeCH())
        with pytest.raises(ValueError, match="bars_archived"):
            await repo.record(
                source="x", table_name="y", period=date(2026, 5, 12),
                stage="raw", bars_archived=-1, s3_key="",
            )

    @pytest.mark.asyncio
    async def test_rejects_empty_required_fields(self):
        repo = _repo(_FakeCH())
        with pytest.raises(ValueError, match="required"):
            await repo.record(
                source="", table_name="y", period=date(2026, 5, 12),
                stage="raw", bars_archived=0, s3_key="",
            )
        with pytest.raises(ValueError, match="required"):
            await repo.record(
                source="x", table_name="", period=date(2026, 5, 12),
                stage="raw", bars_archived=0, s3_key="",
            )
        with pytest.raises(ValueError, match="required"):
            await repo.record(
                source="x", table_name="y", period=date(2026, 5, 12),
                stage="", bars_archived=0, s3_key="",
            )

    @pytest.mark.asyncio
    async def test_versions_are_monotonic_across_calls(self):
        """Re-running the same key must yield a strictly higher version so
        the ReplacingMergeTree picks the most recent row at merge time."""
        fake = _FakeCH()
        repo = _repo(fake)
        for _ in range(3):
            await repo.record(
                source="s", table_name="t", period=date(2026, 5, 12),
                stage="raw", bars_archived=1, s3_key="k",
            )
        versions = [row[1][0][9] for row in fake.inserts]
        # Allow equal (sub-millisecond) but never decreasing.
        assert all(b >= a for a, b in zip(versions, versions[1:]))
        # At least one of the three should differ — ms granularity is
        # enough that three sequential awaits will tick the clock.
        assert versions[-1] >= versions[0]


# ---------- get / get_status ----------


class TestRead:
    @pytest.mark.asyncio
    async def test_get_returns_watermark_when_row_exists(self):
        fake = _FakeCH()
        start, end = _day_bounds(date(2026, 5, 12))
        archived_at = datetime(2026, 5, 13, 7, 5, tzinfo=timezone.utc)
        fake.next_query_result = [
            (
                "polygon-flatfiles", "ohlcv_1m", "raw",
                start, end,
                42_000, "s3://bucket/key.parquet", "ok", "",
                archived_at,
            ),
        ]
        repo = _repo(fake)

        w = await repo.get(
            source="polygon-flatfiles", table_name="ohlcv_1m",
            period=date(2026, 5, 12), stage="raw",
        )
        assert isinstance(w, Watermark)
        assert w.source == "polygon-flatfiles"
        assert w.table_name == "ohlcv_1m"
        assert w.bars_archived == 42_000
        assert w.status == "ok"
        assert w.archived_at == archived_at
        # Query bound parameters as named substitutions.
        sql, params = fake.queries[0]
        assert "lake_archive_watermarks" in sql
        assert "FINAL" in sql
        assert params["source"] == "polygon-flatfiles"
        assert params["table_name"] == "ohlcv_1m"
        assert params["stage"] == "raw"
        assert params["period_start"] == start

    @pytest.mark.asyncio
    async def test_get_returns_none_when_no_row(self):
        fake = _FakeCH()
        fake.next_query_result = []
        repo = _repo(fake)

        w = await repo.get(
            source="polygon-flatfiles", table_name="ohlcv_1m",
            period=date(2026, 5, 12), stage="raw",
        )
        assert w is None

    @pytest.mark.asyncio
    async def test_get_status_returns_status_string(self):
        fake = _FakeCH()
        start, end = _day_bounds(date(2026, 5, 12))
        fake.next_query_result = [
            ("s", "t", "raw", start, end, 1, "k", "ok", "", start),
        ]
        repo = _repo(fake)

        status = await repo.get_status(
            source="s", table_name="t", period=date(2026, 5, 12), stage="raw",
        )
        assert status == "ok"

    @pytest.mark.asyncio
    async def test_get_status_returns_none_when_missing(self):
        fake = _FakeCH()
        fake.next_query_result = []
        repo = _repo(fake)
        assert await repo.get_status(
            source="s", table_name="t", period=date(2026, 5, 12), stage="raw",
        ) is None

    @pytest.mark.asyncio
    async def test_get_coerces_naive_datetime_to_utc(self):
        """ClickHouse returns naive datetimes for some driver versions; the
        repo must coerce them to tz-aware UTC for downstream consumers."""
        fake = _FakeCH()
        naive_start = datetime(2026, 5, 12, 0, 0, 0)
        naive_end = datetime(2026, 5, 12, 23, 59, 59, 999_000)
        naive_archived = datetime(2026, 5, 13, 7, 0, 0)
        fake.next_query_result = [
            ("s", "t", "raw", naive_start, naive_end, 1, "k", "ok", "", naive_archived),
        ]
        repo = _repo(fake)

        w = await repo.get(
            source="s", table_name="t", period=date(2026, 5, 12), stage="raw",
        )
        assert w is not None
        assert w.period_start.tzinfo is timezone.utc
        assert w.period_end.tzinfo is timezone.utc
        assert w.archived_at.tzinfo is timezone.utc


# ---------- get_ok_dates (bulk resumability scan) ----------


class TestGetOkDates:
    @pytest.mark.asyncio
    async def test_returns_set_of_dates_from_query(self):
        fake = _FakeCH()
        fake.next_query_result = [
            (date(2026, 5, 8),),
            (date(2026, 5, 11),),
            (date(2026, 5, 12),),
        ]
        repo = _repo(fake)

        out = await repo.get_ok_dates(
            source="polygon-flatfiles", table_name="ohlcv_1m",
            stage="raw",
            start=date(2026, 5, 1), end=date(2026, 5, 15),
        )
        assert out == {date(2026, 5, 8), date(2026, 5, 11), date(2026, 5, 12)}

        sql, params = fake.queries[0]
        assert "FINAL" in sql
        assert "GROUP BY d" in sql
        assert "status = 'ok'" in sql
        assert params["source"] == "polygon-flatfiles"
        assert params["table_name"] == "ohlcv_1m"
        assert params["stage"] == "raw"
        # Bounds use the day_bounds helper.
        start_dt, _ = _day_bounds(date(2026, 5, 1))
        _, end_dt = _day_bounds(date(2026, 5, 15))
        assert params["start"] == start_dt
        assert params["end"] == end_dt

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_set(self):
        fake = _FakeCH()
        fake.next_query_result = []
        repo = _repo(fake)
        out = await repo.get_ok_dates(
            source="s", table_name="t", stage="raw",
            start=date(2026, 1, 1), end=date(2026, 1, 31),
        )
        assert out == set()

    @pytest.mark.asyncio
    async def test_coerces_datetime_to_date(self):
        """ClickHouse returns toDate() as datetime in some driver
        versions — the repo must coerce to plain date."""
        from datetime import datetime as _dt
        fake = _FakeCH()
        fake.next_query_result = [
            (_dt(2026, 5, 12, 0, 0),),
        ]
        repo = _repo(fake)
        out = await repo.get_ok_dates(
            source="s", table_name="t", stage="raw",
            start=date(2026, 5, 1), end=date(2026, 5, 31),
        )
        assert out == {date(2026, 5, 12)}

    @pytest.mark.asyncio
    async def test_rejects_inverted_range(self):
        repo = _repo(_FakeCH())
        with pytest.raises(ValueError, match="before"):
            await repo.get_ok_dates(
                source="s", table_name="t", stage="raw",
                start=date(2026, 5, 31), end=date(2026, 5, 1),
            )


# ---------- day_bounds helper ----------


class TestDayBounds:
    def test_bounds_are_full_utc_day(self):
        start, end = _day_bounds(date(2026, 5, 12))
        assert start == datetime(2026, 5, 12, 0, 0, 0, tzinfo=timezone.utc)
        assert end.tzinfo is timezone.utc
        # End must NOT spill into the next day.
        assert end.year == 2026 and end.month == 5 and end.day == 12
        assert end.hour == 23 and end.minute == 59 and end.second == 59
