"""
Tests for SilverOhlcvReader + the `/api/silver/...` HTTP routes.

Two surfaces, same Pydantic contract — assert both behave identically
on happy path + cold-start + edge cases.

Pattern mirrors `test_routes_lake.py` and `test_silver_corp_actions.py`:
inject a stub catalog/table via the reader's DI seams, exercise the
handler with FastAPI TestClient + dependency_overrides for the route.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

import pyarrow as pa
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_silver
from app.api.routes_silver import get_silver_ohlcv_reader
from app.services.readers.schemas import (
    BarQualityResponse,
    BarQualityRow,
    SilverBarsResponse,
)
from app.services.readers.silver_ohlcv_reader import SilverOhlcvReader


# ─────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────


class _FakeSnapshot:
    def __init__(self, snapshot_id: int) -> None:
        self.snapshot_id = snapshot_id


class _FakeScan:
    def __init__(self, arrow: pa.Table) -> None:
        self._arrow = arrow

    def to_arrow(self) -> pa.Table:
        return self._arrow


class _FakeIcebergTable:
    """Minimal PyIceberg-table stand-in: .scan(...) returns a fixed Arrow."""

    def __init__(
        self,
        arrow: pa.Table,
        *,
        snapshot_id: Optional[int] = 12345,
        scan_raises: Optional[Exception] = None,
    ) -> None:
        self._arrow = arrow
        self._snapshot_id = snapshot_id
        self._scan_raises = scan_raises
        self.scan_calls: list[Any] = []

    def scan(self, *, row_filter: Any = None, **_: Any) -> _FakeScan:
        self.scan_calls.append(row_filter)
        if self._scan_raises:
            raise self._scan_raises
        return _FakeScan(self._arrow)

    def current_snapshot(self) -> Optional[_FakeSnapshot]:
        if self._snapshot_id is None:
            return None
        return _FakeSnapshot(self._snapshot_id)


def _silver_row(
    symbol: str,
    ts: datetime,
    *,
    close: float = 100.0,
    source_provider: str = "polygon",
    sources_seen: str = "polygon",
    volume: int = 1000,
) -> dict:
    """Silver row in the Arrow-shaped layout (split-adjusted OHLCV)."""
    return {
        "symbol": symbol,
        "timestamp": ts,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": volume,
        "vwap": close,
        "trade_count": 5,
        "source_provider": source_provider,
        "sources_seen": sources_seen,
        "ingestion_ts": datetime(2024, 1, 2, tzinfo=timezone.utc),
        "ingestion_run_id": "run-1",
    }


def _bar_quality_row(
    symbol: str,
    d: date,
    *,
    expected_bars: int = 390,
    actual_bars: int = 388,
    gap_count: int = 2,
    max_gap_minutes: int = 3,
    providers_seen: str = "polygon,schwab",
    disagreement_count: int = 0,
    backfill_attempts: int = 0,
) -> dict:
    return {
        "symbol": symbol,
        "date": d,
        "expected_bars": expected_bars,
        "actual_bars": actual_bars,
        "gap_count": gap_count,
        "max_gap_minutes": max_gap_minutes,
        "providers_seen": providers_seen,
        "disagreement_count": disagreement_count,
        "backfill_attempts": backfill_attempts,
        "ingestion_ts": datetime(2024, 1, 2, tzinfo=timezone.utc),
        "ingestion_run_id": "run-1",
    }


# ─────────────────────────────────────────────────────────────────────
# get_bars
# ─────────────────────────────────────────────────────────────────────


class TestGetBarsHappyPath:
    def test_returns_sorted_bars_with_snapshot(self) -> None:
        t0 = datetime(2024, 6, 10, 13, 30, tzinfo=timezone.utc)
        t1 = datetime(2024, 6, 10, 13, 31, tzinfo=timezone.utc)
        # Insert out-of-order to verify reader sorts ASC by timestamp.
        rows = [
            _silver_row("AAPL", t1, close=190.5),
            _silver_row("AAPL", t0, close=190.0),
        ]
        ohlcv_table = _FakeIcebergTable(pa.Table.from_pylist(rows))
        bq_table = _FakeIcebergTable(pa.Table.from_pylist([]))
        reader = SilverOhlcvReader(
            ohlcv_table=ohlcv_table, bar_quality_table=bq_table,
        )

        start = datetime(2024, 6, 10, tzinfo=timezone.utc)
        end = datetime(2024, 6, 11, tzinfo=timezone.utc)
        resp = reader.get_bars("aapl", start, end)

        assert isinstance(resp, SilverBarsResponse)
        assert resp.symbol == "AAPL"
        assert resp.start == start
        assert resp.end == end
        assert resp.snapshot_id == "12345"
        assert resp.count == 2
        # Sorted by timestamp ascending.
        assert [b.timestamp for b in resp.bars] == [t0, t1]
        # Silver stores split-adjusted OHLCV directly.
        assert resp.bars[0].close == 190.0
        # sources_seen promoted from CSV to list.
        assert resp.bars[0].sources_seen == ["polygon"]
        # source_provider preserved.
        assert resp.bars[0].source_provider == "polygon"

    def test_sources_seen_csv_parses_multi_provider(self) -> None:
        ts = datetime(2024, 6, 10, 13, 30, tzinfo=timezone.utc)
        rows = [_silver_row(
            "AAPL", ts,
            source_provider="polygon",
            sources_seen="polygon,schwab",
        )]
        reader = SilverOhlcvReader(
            ohlcv_table=_FakeIcebergTable(pa.Table.from_pylist(rows)),
            bar_quality_table=_FakeIcebergTable(pa.Table.from_pylist([])),
        )
        resp = reader.get_bars(
            "AAPL",
            datetime(2024, 6, 10, tzinfo=timezone.utc),
            datetime(2024, 6, 11, tzinfo=timezone.utc),
        )
        assert set(resp.bars[0].sources_seen) == {"polygon", "schwab"}


class TestGetBarsEdgeCases:
    def test_empty_symbol_returns_empty(self) -> None:
        reader = SilverOhlcvReader(
            ohlcv_table=_FakeIcebergTable(pa.Table.from_pylist([])),
            bar_quality_table=_FakeIcebergTable(pa.Table.from_pylist([])),
        )
        resp = reader.get_bars(
            "",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        assert resp.bars == []
        assert resp.count == 0

    def test_whitespace_symbol_returns_empty(self) -> None:
        reader = SilverOhlcvReader(
            ohlcv_table=_FakeIcebergTable(pa.Table.from_pylist([])),
            bar_quality_table=_FakeIcebergTable(pa.Table.from_pylist([])),
        )
        resp = reader.get_bars(
            "   ",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        assert resp.bars == []

    def test_missing_table_returns_empty_no_raise(self) -> None:
        """Cold-start: silver.ohlcv_1m doesn't exist yet."""
        class _BoomCatalog:
            def load_table(self, _id: Any) -> Any:
                raise RuntimeError("table not found")

        reader = SilverOhlcvReader(catalog=_BoomCatalog())
        resp = reader.get_bars(
            "AAPL",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        assert resp.bars == []
        assert resp.count == 0
        assert resp.snapshot_id is None

    def test_scan_failure_returns_empty_no_raise(self) -> None:
        """If a scan blows up mid-query, return empty rather than 500ing."""
        reader = SilverOhlcvReader(
            ohlcv_table=_FakeIcebergTable(
                pa.Table.from_pylist([]),
                scan_raises=RuntimeError("scan exploded"),
            ),
            bar_quality_table=_FakeIcebergTable(pa.Table.from_pylist([])),
        )
        resp = reader.get_bars(
            "AAPL",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        assert resp.bars == []
        assert resp.snapshot_id is None  # no snapshot when scan fails

    def test_naive_datetime_upgraded_to_utc(self) -> None:
        reader = SilverOhlcvReader(
            ohlcv_table=_FakeIcebergTable(pa.Table.from_pylist([])),
            bar_quality_table=_FakeIcebergTable(pa.Table.from_pylist([])),
        )
        resp = reader.get_bars(
            "AAPL",
            datetime(2024, 1, 1),                # naive
            datetime(2024, 1, 2),                # naive
        )
        assert resp.start.tzinfo is not None
        assert resp.end.tzinfo is not None

    def test_skip_rows_with_null_ohlc(self) -> None:
        """Rows missing OHLC are skipped (defensive — would be upstream bug)."""
        ts = datetime(2024, 6, 10, 13, 30, tzinfo=timezone.utc)
        good = _silver_row("AAPL", ts, close=190.0)
        bad = _silver_row(
            "AAPL", ts.replace(minute=31), close=190.5,
        )
        bad["close"] = None
        rows = [good, bad]
        reader = SilverOhlcvReader(
            ohlcv_table=_FakeIcebergTable(pa.Table.from_pylist(rows)),
            bar_quality_table=_FakeIcebergTable(pa.Table.from_pylist([])),
        )
        resp = reader.get_bars(
            "AAPL",
            datetime(2024, 6, 10, tzinfo=timezone.utc),
            datetime(2024, 6, 11, tzinfo=timezone.utc),
        )
        assert resp.count == 1


# ─────────────────────────────────────────────────────────────────────
# get_bar_quality
# ─────────────────────────────────────────────────────────────────────


class TestGetBarQuality:
    def test_returns_rows_sorted_by_date(self) -> None:
        rows = [
            _bar_quality_row("AAPL", date(2024, 6, 11)),
            _bar_quality_row("AAPL", date(2024, 6, 10)),
        ]
        reader = SilverOhlcvReader(
            ohlcv_table=_FakeIcebergTable(pa.Table.from_pylist([])),
            bar_quality_table=_FakeIcebergTable(pa.Table.from_pylist(rows)),
        )
        resp = reader.get_bar_quality(
            "AAPL",
            since=date(2024, 6, 1), until=date(2024, 6, 30),
        )
        assert isinstance(resp, BarQualityResponse)
        assert resp.count == 2
        assert [r.date for r in resp.rows] == [date(2024, 6, 10), date(2024, 6, 11)]
        # providers_seen CSV → list.
        assert resp.rows[0].providers_seen == ["polygon", "schwab"]
        assert resp.rows[0].expected_bars == 390

    def test_missing_table_returns_empty(self) -> None:
        class _BoomCatalog:
            def load_table(self, _id: Any) -> Any:
                raise RuntimeError("not found")

        reader = SilverOhlcvReader(catalog=_BoomCatalog())
        resp = reader.get_bar_quality("AAPL")
        assert resp.rows == []
        assert resp.snapshot_id is None


# ─────────────────────────────────────────────────────────────────────
# HTTP route
# ─────────────────────────────────────────────────────────────────────


def _make_silver_app() -> FastAPI:
    """Minimal FastAPI app with only the silver router mounted."""
    app = FastAPI()
    app.include_router(routes_silver.router, prefix="/api", tags=["Silver"])
    return app


class _StubReader:
    """Stand-in for SilverOhlcvReader at the route layer."""

    def __init__(
        self,
        *,
        bars_response: SilverBarsResponse | None = None,
        bq_response: BarQualityResponse | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._bars = bars_response
        self._bq = bq_response
        self._raises = raises
        self.last_get_bars: Optional[dict] = None
        self.last_get_quality: Optional[dict] = None

    def get_bars(self, symbol, start, end):
        self.last_get_bars = {"symbol": symbol, "start": start, "end": end}
        if self._raises:
            raise self._raises
        return self._bars or SilverBarsResponse(
            symbol=symbol.upper(), start=start, end=end,
            snapshot_id=None, bars=[], count=0,
        )

    def get_bar_quality(self, symbol, *, since=None, until=None):
        self.last_get_quality = {
            "symbol": symbol, "since": since, "until": until,
        }
        if self._raises:
            raise self._raises
        return self._bq or BarQualityResponse(
            symbol=symbol.upper(), since=since, until=until,
            snapshot_id=None, rows=[], count=0,
        )


class TestRouteGetBars:
    def test_route_delegates_and_returns_response(self) -> None:
        app = _make_silver_app()
        stub = _StubReader()
        app.dependency_overrides[get_silver_ohlcv_reader] = lambda: stub

        with TestClient(app) as client:
            resp = client.get(
                "/api/silver/bars/AAPL",
                params={
                    "start": "2024-06-10T13:30:00Z",
                    "end": "2024-06-10T20:00:00Z",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "AAPL"
        assert body["count"] == 0
        assert stub.last_get_bars is not None
        assert stub.last_get_bars["symbol"] == "AAPL"

    def test_start_after_end_returns_400(self) -> None:
        app = _make_silver_app()
        app.dependency_overrides[get_silver_ohlcv_reader] = lambda: _StubReader()

        with TestClient(app) as client:
            resp = client.get(
                "/api/silver/bars/AAPL",
                params={
                    "start": "2024-06-11T00:00:00Z",
                    "end": "2024-06-10T00:00:00Z",
                },
            )
        assert resp.status_code == 400


class TestRouteGetBarQuality:
    def test_route_delegates_to_reader(self) -> None:
        app = _make_silver_app()
        bq_row = BarQualityRow(
            symbol="AAPL", date=date(2024, 6, 10),
            expected_bars=390, actual_bars=388,
            gap_count=1, max_gap_minutes=2,
            providers_seen=["polygon", "schwab"],
            disagreement_count=0, backfill_attempts=0,
        )
        stub = _StubReader(
            bq_response=BarQualityResponse(
                symbol="AAPL",
                since=date(2024, 6, 1), until=date(2024, 6, 30),
                snapshot_id="abc", rows=[bq_row], count=1,
            ),
        )
        app.dependency_overrides[get_silver_ohlcv_reader] = lambda: stub

        with TestClient(app) as client:
            resp = client.get(
                "/api/silver/bar-quality/AAPL",
                params={"since": "2024-06-01", "until": "2024-06-30"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["rows"][0]["actual_bars"] == 388
        assert body["rows"][0]["providers_seen"] == ["polygon", "schwab"]
        assert stub.last_get_quality is not None
        assert stub.last_get_quality["since"] == date(2024, 6, 1)
        assert stub.last_get_quality["until"] == date(2024, 6, 30)

    def test_since_after_until_returns_400(self) -> None:
        app = _make_silver_app()
        app.dependency_overrides[get_silver_ohlcv_reader] = lambda: _StubReader()

        with TestClient(app) as client:
            resp = client.get(
                "/api/silver/bar-quality/AAPL",
                params={"since": "2024-07-01", "until": "2024-06-01"},
            )
        assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────
# MCP tool registration
# ─────────────────────────────────────────────────────────────────────


class TestMCPRegistration:
    """Sanity that the new tool module imports cleanly + tools register."""

    def test_silver_ohlcv_module_importable(self) -> None:
        # Import via the tool module (registers via @mcp.tool() side effect).
        from app.mcp.tools import silver_ohlcv  # noqa: F401
        # Both tools must be callables.
        assert callable(silver_ohlcv.get_silver_bars)
        assert callable(silver_ohlcv.get_silver_bar_quality)


# ─────────────────────────────────────────────────────────────────────
# CV11 — v2 cutover regression tests
# ─────────────────────────────────────────────────────────────────────


class TestV2ReaderTargets:
    """Symbolic checks that the reader resolves equities.polygon_adjusted
    (v2) and not silver.ohlcv_1m (v1). Catch accidental rollback."""

    def test_get_bars_loads_equities_polygon_adjusted(self) -> None:
        from unittest.mock import MagicMock
        from app.services.readers.silver_ohlcv_reader import SilverOhlcvReader

        fake_cat = MagicMock()
        fake_cat.load_table.return_value = MagicMock()
        r = SilverOhlcvReader(catalog=fake_cat)
        r._get_ohlcv_table()

        args, _ = fake_cat.load_table.call_args
        assert args[0].endswith(".polygon_adjusted"), (
            f"reader must load equities.polygon_adjusted, got {args[0]!r}"
        )

    def test_get_bar_quality_short_circuits_to_empty_without_v2_table(
        self,
    ) -> None:
        """No v2 equivalent for silver.bar_quality. Reader returns an
        empty BarQualityResponse without trying to load anything."""
        from unittest.mock import MagicMock
        from app.services.readers.silver_ohlcv_reader import SilverOhlcvReader

        fake_cat = MagicMock()
        fake_cat.load_table.side_effect = AssertionError(
            "must not attempt to load a v2 bar_quality — there is none"
        )
        r = SilverOhlcvReader(catalog=fake_cat)
        resp = r.get_bar_quality("AAPL")
        assert resp.count == 0
        assert resp.rows == []
        # The fake's side_effect would have fired if load_table was called.
        fake_cat.load_table.assert_not_called()

    def test_arrow_to_bars_accepts_v2_source_column(self) -> None:
        """v2 polygon_adjusted has `source` (not `source_provider`).
        Reader's conversion must read `source` so v2 rows produce
        valid SilverBar objects."""
        import pyarrow as pa
        from app.services.readers.silver_ohlcv_reader import SilverOhlcvReader

        arrow = pa.table({
            "symbol": ["AAPL"],
            "timestamp": pa.array(
                [datetime(2024, 5, 15, 14, 30, tzinfo=timezone.utc)],
                type=pa.timestamp("us", tz="UTC"),
            ),
            "open": [150.0],
            "high": [151.0],
            "low": [149.0],
            "close": [150.5],
            "volume": [1000],
            "vwap": [150.25],
            "trade_count": [10],
            # NB: v2 schema uses `source`, not `source_provider`.
            "source": ["polygon-adjusted"],
            "ingestion_ts": pa.array([None], type=pa.timestamp("us", tz="UTC")),
            "ingestion_run_id": pa.array([None], type=pa.string()),
            "adj_factor": [1.0],
        })
        bars = SilverOhlcvReader._arrow_to_bars(arrow)
        assert len(bars) == 1
        assert bars[0].source_provider == "polygon-adjusted"
        assert bars[0].sources_seen == []  # no v2 equivalent
