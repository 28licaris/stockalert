"""
Tests for the silver OHLCV build orchestrator (TA-5.1.4).

Verifies the end-to-end wiring of:
  bronze read → per-provider normalize → precedence merge →
  bar_quality compute → silver upsert.

Uses fake catalog + fake tables so the test exercises real pipeline
glue without needing S3 / Glue / Iceberg infrastructure.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional
from unittest.mock import patch

import pyarrow as pa
import pytest

from app.services.silver.ohlcv.build import (
    BuildResult,
    SilverOhlcvBuild,
    SliceResult,
    _PROVIDER_ROUTING,
)


# ─────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────


class _FakeScan:
    """Minimal scan that returns a pre-supplied Arrow table from .to_arrow()."""

    def __init__(self, arrow: pa.Table) -> None:
        self._arrow = arrow

    def to_arrow(self) -> pa.Table:
        return self._arrow


class _FakeBronzeTable:
    """Stand-in for a PyIceberg Table; .scan(...) returns _FakeScan."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.scans: list[dict] = []

    def scan(
        self, *, row_filter: Any = None, selected_fields: Any = None, **_: Any,
    ) -> _FakeScan:
        self.scans.append({
            "row_filter": row_filter,
            "selected_fields": selected_fields,
        })
        if not self._rows:
            schema = pa.schema([
                pa.field("symbol", pa.string()),
                pa.field("timestamp", pa.timestamp("us", tz="UTC")),
                pa.field("open", pa.float64()),
                pa.field("high", pa.float64()),
                pa.field("low", pa.float64()),
                pa.field("close", pa.float64()),
                pa.field("volume", pa.int64()),
                pa.field("vwap", pa.float64()),
                pa.field("trade_count", pa.int64()),
                pa.field("source", pa.string()),
            ])
            return _FakeScan(pa.Table.from_pylist([], schema=schema))
        return _FakeScan(pa.Table.from_pylist(self._rows))


class _FakeSilverTable:
    """Captures the upserted Arrow rows so tests can assert on them."""

    def __init__(self) -> None:
        self.upserts: list[pa.Table] = []

    def upsert(self, arrow: pa.Table) -> None:
        self.upserts.append(arrow)


class _FakeCatalog:
    """Returns whichever fake bronze table the test wired up for a given short."""

    def __init__(self, bronze_tables: dict[str, _FakeBronzeTable]) -> None:
        self._bronze_tables = bronze_tables
        self.load_calls: list[Any] = []

    def load_table(self, identifier: Any) -> _FakeBronzeTable:
        self.load_calls.append(identifier)
        # bronze_table_id returns (namespace, table) tuple; map by short suffix.
        if isinstance(identifier, tuple):
            short = identifier[-1]
        else:
            short = str(identifier).split(".")[-1]
        if short in self._bronze_tables:
            return self._bronze_tables[short]
        # Simulate "table not found" for any unmapped short — orchestrator
        # treats this as zero rows for that provider.
        from pyiceberg.exceptions import NoSuchTableError
        raise NoSuchTableError(f"no fake for {short}")


def _bronze_row(
    symbol: str,
    ts: datetime,
    *,
    close: float,
    source: str,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: int = 100,
    vwap: float | None = None,
    trade_count: int = 1,
) -> dict:
    return {
        "symbol": symbol,
        "timestamp": ts,
        "open": open_ if open_ is not None else close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "close": close,
        "volume": volume,
        "vwap": vwap if vwap is not None else close,
        "trade_count": trade_count,
        "source": source,
    }


def _make_build(
    *,
    polygon_rows: list[dict] | None = None,
    schwab_rows: list[dict] | None = None,
    precedence: list[str] | None = None,
) -> tuple[SilverOhlcvBuild, _FakeSilverTable, _FakeSilverTable, _FakeCatalog]:
    """Wire a SilverOhlcvBuild with fake catalog + fake silver tables.

    The corp-actions cache is pre-primed to empty so `_prime_corp_actions_cache`
    does not try to load real silver tables.
    """
    bronze: dict[str, _FakeBronzeTable] = {}
    if polygon_rows is not None:
        bronze["polygon_minute"] = _FakeBronzeTable(polygon_rows)
    if schwab_rows is not None:
        bronze["schwab_minute"] = _FakeBronzeTable(schwab_rows)
    catalog = _FakeCatalog(bronze)
    ohlcv_table = _FakeSilverTable()
    bq_table = _FakeSilverTable()
    build = SilverOhlcvBuild(
        catalog=catalog,
        ohlcv_table=ohlcv_table,
        bar_quality_table=bq_table,
        provider_precedence=precedence or ["polygon", "schwab"],
    )
    # Pre-prime so we don't try to load silver.corp_actions from the fake.
    build._split_index = {}
    build._corp_actions_arrow = pa.table({"symbol": []})
    return build, ohlcv_table, bq_table, catalog


# ─────────────────────────────────────────────────────────────────────
# Provider routing
# ─────────────────────────────────────────────────────────────────────


class TestProviderRouting:
    """The _PROVIDER_ROUTING dict is the pluggability seam — adding a
    new provider == one entry here + bronze schema additions, no
    orchestrator changes. Lock the current keys + values."""

    def test_polygon_and_schwab_are_routed(self) -> None:
        assert "polygon" in _PROVIDER_ROUTING
        assert "schwab" in _PROVIDER_ROUTING

    def test_polygon_routing_points_at_raw_minute(self) -> None:
        r = _PROVIDER_ROUTING["polygon"]
        assert r.bronze_short == "polygon_minute"
        assert r.adjustment_status == "raw"

    def test_schwab_routing_points_at_split_adjusted_minute(self) -> None:
        r = _PROVIDER_ROUTING["schwab"]
        assert r.bronze_short == "schwab_minute"
        assert r.adjustment_status == "split_adjusted"


# ─────────────────────────────────────────────────────────────────────
# Result dataclasses
# ─────────────────────────────────────────────────────────────────────


class TestSliceResult:
    def test_succeeded_when_no_error(self) -> None:
        r = SliceResult(symbol="AAPL", date=date(2024, 1, 2))
        assert r.succeeded is True

    def test_failed_when_error_present(self) -> None:
        r = SliceResult(symbol="AAPL", date=date(2024, 1, 2), error="boom")
        assert r.succeeded is False


class TestBuildResult:
    def test_duration_seconds(self) -> None:
        t0 = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2024, 1, 1, 0, 1, 30, tzinfo=timezone.utc)
        r = BuildResult(run_id="x", started_at=t0, finished_at=t1)
        assert r.duration_seconds == 90.0

    def test_aggregates_silver_rows_and_succeeded_failed_counts(self) -> None:
        t = datetime(2024, 1, 1, tzinfo=timezone.utc)
        r = BuildResult(run_id="x", started_at=t, finished_at=t)
        r.slices = [
            SliceResult(symbol="A", date=date(2024, 1, 2), silver_rows_written=3),
            SliceResult(symbol="B", date=date(2024, 1, 2), silver_rows_written=5),
            SliceResult(symbol="C", date=date(2024, 1, 2), error="x"),
        ]
        assert r.total_silver_rows == 8
        assert r.slices_succeeded == 2
        assert r.slices_failed == 1


# ─────────────────────────────────────────────────────────────────────
# from_settings / precedence parsing
# ─────────────────────────────────────────────────────────────────────


class TestFromSettings:
    def test_parses_csv_precedence(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.config import settings
        monkeypatch.setattr(
            settings, "silver_provider_precedence", "polygon, schwab",
        )
        b = SilverOhlcvBuild.from_settings()
        assert b._get_precedence() == ["polygon", "schwab"]

    def test_empty_precedence_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.config import settings
        monkeypatch.setattr(settings, "silver_provider_precedence", "")
        with pytest.raises(ValueError):
            SilverOhlcvBuild.from_settings()


# ─────────────────────────────────────────────────────────────────────
# build_slice — happy path + edge cases
# ─────────────────────────────────────────────────────────────────────


class TestBuildSliceNoData:
    def test_no_bronze_data_returns_clean_zero_result(self) -> None:
        build, ohlcv, bq, _ = _make_build(polygon_rows=[], schwab_rows=[])
        result = build.build_slice("AAPL", date(2024, 1, 2))

        assert result.succeeded
        assert result.polygon_rows_read == 0
        assert result.schwab_rows_read == 0
        assert result.silver_rows_written == 0
        assert result.quality_row_written is False
        assert ohlcv.upserts == []
        assert bq.upserts == []


class TestBuildSliceSingleProvider:
    """Polygon-only slice: silver row should come from polygon with raw
    passthrough and adj = raw / F (F=1 here since no splits)."""

    def test_polygon_only_writes_one_row(self) -> None:
        ts = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)
        build, ohlcv, bq, _ = _make_build(
            polygon_rows=[_bronze_row(
                "AAPL", ts, close=180.0, source="polygon-flatfiles",
            )],
            schwab_rows=[],
        )
        result = build.build_slice("AAPL", date(2024, 1, 2))

        assert result.succeeded
        assert result.polygon_rows_read == 1
        assert result.schwab_rows_read == 0
        assert result.silver_rows_written == 1
        assert result.quality_row_written

        # One upsert of one ohlcv row, one upsert of one quality row.
        assert len(ohlcv.upserts) == 1
        assert ohlcv.upserts[0].num_rows == 1
        assert len(bq.upserts) == 1
        assert bq.upserts[0].num_rows == 1

        row = ohlcv.upserts[0].to_pylist()[0]
        assert row["symbol"] == "AAPL"
        # F=1 (no future splits) → polygon raw passes through unchanged.
        assert row["close"] == 180.0
        assert row["source_provider"] == "polygon"
        assert row["sources_seen"] == "polygon"


class TestBuildSlicePrecedenceMerge:
    """Both providers contribute; Polygon wins per `precedence`. The
    sources_seen CSV should list both."""

    def test_polygon_wins_over_schwab_when_both_present(self) -> None:
        ts = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)
        build, ohlcv, bq, _ = _make_build(
            polygon_rows=[_bronze_row(
                "AAPL", ts, close=180.0, source="polygon-flatfiles",
            )],
            schwab_rows=[_bronze_row(
                "AAPL", ts, close=180.05, source="schwab-stream",
            )],
        )
        result = build.build_slice("AAPL", date(2024, 1, 2))

        assert result.succeeded
        assert result.polygon_rows_read == 1
        assert result.schwab_rows_read == 1
        assert result.silver_rows_written == 1

        row = ohlcv.upserts[0].to_pylist()[0]
        # Polygon precedes Schwab → polygon's 180.00 wins.
        assert row["close"] == 180.0
        assert row["source_provider"] == "polygon"
        # Both providers contributed to this minute → both in sources_seen.
        seen = row["sources_seen"].split(",")
        assert set(seen) == {"polygon", "schwab"}


class TestBuildSliceErrorIsolation:
    """An exception during upsert is captured per-slice so the surrounding
    window loop keeps going — guard rail for nightly runs."""

    def test_upsert_failure_recorded_as_slice_error(self) -> None:
        ts = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)
        build, ohlcv, bq, _ = _make_build(
            polygon_rows=[_bronze_row(
                "AAPL", ts, close=180.0, source="polygon-flatfiles",
            )],
            schwab_rows=[],
        )

        def _boom(_arrow: pa.Table) -> None:
            raise RuntimeError("simulated upsert failure")

        ohlcv.upsert = _boom  # type: ignore[assignment]
        result = build.build_slice("AAPL", date(2024, 1, 2))

        assert result.succeeded is False
        assert result.error is not None
        assert "RuntimeError" in result.error


# ─────────────────────────────────────────────────────────────────────
# build_window — multi-day, multi-symbol accumulation
# ─────────────────────────────────────────────────────────────────────


class TestBuildWindow:
    def test_iterates_each_day_for_each_symbol(self) -> None:
        # Two symbols × two days = 4 slice calls. Bronze fakes return
        # empty so the silver writes stay zero, but slice_results length
        # tells us iteration happened correctly.
        build, _ohlcv, _bq, _cat = _make_build(
            polygon_rows=[], schwab_rows=[],
        )
        # Suppress the best-effort CH recorder for the test.
        with patch.object(build, "_record_run"):
            result = build.build_window(
                symbols=["AAPL", "MSFT"],
                start_date=date(2024, 1, 2),
                end_date=date(2024, 1, 3),
            )

        assert len(result.slices) == 4
        slice_keys = {(s.symbol, s.date) for s in result.slices}
        assert slice_keys == {
            ("AAPL", date(2024, 1, 2)),
            ("AAPL", date(2024, 1, 3)),
            ("MSFT", date(2024, 1, 2)),
            ("MSFT", date(2024, 1, 3)),
        }
        assert result.slices_succeeded == 4
        assert result.slices_failed == 0
        assert result.total_silver_rows == 0

    def test_clears_caches_after_run(self) -> None:
        build, _o, _bq, _ = _make_build(polygon_rows=[], schwab_rows=[])
        # Pretend the run primed something:
        build._split_index = {"AAPL": [(date(2024, 6, 10), 10.0)]}
        build._corp_actions_arrow = pa.table({"symbol": ["AAPL"]})
        with patch.object(build, "_record_run"):
            build.build_window(["AAPL"], date(2024, 1, 2), date(2024, 1, 2))
        # Caches cleared so the next run reloads fresh.
        assert build._split_index is None
        assert build._corp_actions_arrow is None


# ─────────────────────────────────────────────────────────────────────
# Corp-actions cache priming — graceful when silver.corp_actions is absent
# ─────────────────────────────────────────────────────────────────────


class TestCorpActionsCachePriming:
    """If silver.corp_actions doesn't exist yet (cold start), the build
    must NOT crash. Instead F=1 for every symbol (no adjustment applied)."""

    def test_missing_corp_actions_table_yields_empty_split_index(self) -> None:
        from pyiceberg.exceptions import NoSuchTableError

        class _CatNoCorpActions:
            def load_table(self, _identifier: Any) -> Any:
                raise NoSuchTableError("not yet")

        build = SilverOhlcvBuild(
            catalog=_CatNoCorpActions(),
            ohlcv_table=_FakeSilverTable(),
            bar_quality_table=_FakeSilverTable(),
            provider_precedence=["polygon"],
        )
        # Force priming.
        build._prime_corp_actions_cache()
        assert build._get_split_index() == {}
