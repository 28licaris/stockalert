"""
Tests for AdjustedOhlcvReader + the `/api/silver/...` HTTP routes.

Two surfaces, same Pydantic contract — assert both behave identically
on happy path + cold-start + edge cases.

Pattern mirrors `test_routes_lake.py` and `test_silver_corp_actions.py`:
inject a stub catalog/table via the reader's DI seams, exercise the
handler with FastAPI TestClient + dependency_overrides for the route.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import pyarrow as pa
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_adjusted
from app.api.routes_adjusted import get_adjusted_ohlcv_reader
from app.services.readers.schemas import (
    BarQualityResponse,
    BarQualityRow,
    SilverBarsResponse,
    SymbolCoverageResponse,
)
from app.services.readers.adjusted_ohlcv_reader import AdjustedOhlcvReader


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
        reader = AdjustedOhlcvReader(
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
        reader = AdjustedOhlcvReader(
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
        reader = AdjustedOhlcvReader(
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
        reader = AdjustedOhlcvReader(
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

        reader = AdjustedOhlcvReader(catalog=_BoomCatalog())
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
        reader = AdjustedOhlcvReader(
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
        reader = AdjustedOhlcvReader(
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
        reader = AdjustedOhlcvReader(
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
        reader = AdjustedOhlcvReader(
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

        reader = AdjustedOhlcvReader(catalog=_BoomCatalog())
        resp = reader.get_bar_quality("AAPL")
        assert resp.rows == []
        assert resp.snapshot_id is None


# ─────────────────────────────────────────────────────────────────────
# HTTP route
# ─────────────────────────────────────────────────────────────────────


def _make_adjusted_app() -> FastAPI:
    """Minimal FastAPI app with only the silver router mounted."""
    app = FastAPI()
    app.include_router(routes_adjusted.router, prefix="/api", tags=["Adjusted"])
    return app


class _StubReader:
    """Stand-in for AdjustedOhlcvReader at the route layer."""

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
        self.last_get_bars_union: Optional[dict] = None
        self.last_get_quality: Optional[dict] = None

    def get_bars(self, symbol, start, end):
        self.last_get_bars = {"symbol": symbol, "start": start, "end": end}
        if self._raises:
            raise self._raises
        return self._bars or SilverBarsResponse(
            symbol=symbol.upper(), start=start, end=end,
            snapshot_id=None, bars=[], count=0,
        )

    def get_bars_union(self, symbol, start, end):
        self.last_get_bars_union = {"symbol": symbol, "start": start, "end": end}
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
        app = _make_adjusted_app()
        stub = _StubReader()
        app.dependency_overrides[get_adjusted_ohlcv_reader] = lambda: stub

        with TestClient(app) as client:
            resp = client.get(
                "/api/adjusted/bars/AAPL",
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
        app = _make_adjusted_app()
        app.dependency_overrides[get_adjusted_ohlcv_reader] = lambda: _StubReader()

        with TestClient(app) as client:
            resp = client.get(
                "/api/adjusted/bars/AAPL",
                params={
                    "start": "2024-06-11T00:00:00Z",
                    "end": "2024-06-10T00:00:00Z",
                },
            )
        assert resp.status_code == 400


# CV20: TestRouteGetBarQuality removed — the /api/silver/bar-quality
# endpoint was deleted in this commit. The reader's get_bar_quality
# method is kept (returns empty when no fixture is injected — covered
# by TestV2ReaderTargets below) so future v2-quality additions have a
# place to plug in.


# ─────────────────────────────────────────────────────────────────────
# MCP tool registration
# ─────────────────────────────────────────────────────────────────────


class TestMCPRegistration:
    """Sanity that the new tool module imports cleanly + tools register."""

    def test_adjusted_ohlcv_module_importable(self) -> None:
        # Import via the tool module (registers via @mcp.tool() side effect).
        from app.mcp.tools import adjusted_ohlcv  # noqa: F401
        # The single v2 tool must be a callable.
        assert callable(adjusted_ohlcv.get_adjusted_bars)
        # CV20: get_silver_bar_quality deleted (no v2 backing table).

    def test_adjusted_ohlcv_tool_routes_include_live_to_union(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """CV25: the MCP tool's include_live param must reach the
        reader's get_bars_union path. Default False stays on get_bars."""
        from app.mcp.tools import adjusted_ohlcv

        calls: dict[str, int] = {"get_bars": 0, "get_bars_union": 0}

        class _Stub:
            def get_bars(self, symbol, start, end):
                calls["get_bars"] += 1
                return SilverBarsResponse(
                    symbol=symbol.upper(), start=start, end=end,
                    snapshot_id=None, bars=[], count=0,
                )

            def get_bars_union(self, symbol, start, end):
                calls["get_bars_union"] += 1
                return SilverBarsResponse(
                    symbol=symbol.upper(), start=start, end=end,
                    snapshot_id=None, bars=[], count=0,
                )

        # Clear the lru_cache so the next _reader() call picks up the stub.
        adjusted_ohlcv._reader.cache_clear()
        monkeypatch.setattr(adjusted_ohlcv, "_reader", lambda: _Stub())

        start = datetime(2024, 6, 1, tzinfo=timezone.utc)
        end = datetime(2024, 6, 2, tzinfo=timezone.utc)

        # Default — no include_live → get_bars
        adjusted_ohlcv.get_adjusted_bars("AAPL", start, end)
        assert calls == {"get_bars": 1, "get_bars_union": 0}

        # include_live=True → get_bars_union
        adjusted_ohlcv.get_adjusted_bars(
            "AAPL", start, end, include_live=True,
        )
        assert calls == {"get_bars": 1, "get_bars_union": 1}


# ─────────────────────────────────────────────────────────────────────
# CV11 — v2 cutover regression tests
# ─────────────────────────────────────────────────────────────────────


class TestV2ReaderTargets:
    """Symbolic checks that the reader resolves equities.polygon_adjusted
    (v2) and not silver.ohlcv_1m (v1). Catch accidental rollback."""

    def test_get_bars_loads_equities_polygon_adjusted(self) -> None:
        from unittest.mock import MagicMock
        from app.services.readers.adjusted_ohlcv_reader import AdjustedOhlcvReader

        fake_cat = MagicMock()
        fake_cat.load_table.return_value = MagicMock()
        r = AdjustedOhlcvReader(catalog=fake_cat)
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
        from app.services.readers.adjusted_ohlcv_reader import AdjustedOhlcvReader

        fake_cat = MagicMock()
        fake_cat.load_table.side_effect = AssertionError(
            "must not attempt to load a v2 bar_quality — there is none"
        )
        r = AdjustedOhlcvReader(catalog=fake_cat)
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
        from app.services.readers.adjusted_ohlcv_reader import AdjustedOhlcvReader

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
        bars = AdjustedOhlcvReader._arrow_to_bars(arrow)
        assert len(bars) == 1
        assert bars[0].source_provider == "polygon-adjusted"
        assert bars[0].sources_seen == []  # no v2 equivalent


# ─────────────────────────────────────────────────────────────────────
# CV24 — get_bars_union (polygon_adjusted ∪ schwab_universe)
# ─────────────────────────────────────────────────────────────────────


def _v2_row(symbol: str, ts: datetime, *, source: str, close: float = 100.0) -> dict:
    """v2 polygon_adjusted / schwab_universe row shape."""
    return {
        "symbol": symbol,
        "timestamp": ts,
        "open": close, "high": close, "low": close, "close": close,
        "volume": 1000.0, "vwap": close, "trade_count": 5,
        "source": source,
        "ingestion_ts": datetime(2024, 6, 1, tzinfo=timezone.utc),
        "ingestion_run_id": "run-x",
    }


class TestGetBarsUnion:
    def test_polygon_only_when_schwab_window_empty(self) -> None:
        """If schwab_universe has no rows in window, response is just
        the polygon_adjusted bars."""
        ts1 = datetime(2024, 6, 1, 14, 30, tzinfo=timezone.utc)
        ts2 = datetime(2024, 6, 1, 14, 31, tzinfo=timezone.utc)
        polygon = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row("AAPL", ts1, source="polygon-adjusted"),
            _v2_row("AAPL", ts2, source="polygon-adjusted"),
        ]))
        schwab = _FakeIcebergTable(pa.Table.from_pylist([]))

        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )
        resp = reader.get_bars_union(
            "AAPL",
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            datetime(2024, 6, 2, tzinfo=timezone.utc),
        )
        assert resp.count == 2
        assert [b.timestamp for b in resp.bars] == [ts1, ts2]
        assert all(b.source_provider == "polygon-adjusted" for b in resp.bars)

    def test_schwab_only_when_polygon_window_empty(self) -> None:
        """If polygon_adjusted is empty (cold-start before first Spark
        run), schwab fills the response."""
        ts = datetime(2024, 6, 1, 14, 30, tzinfo=timezone.utc)
        polygon = _FakeIcebergTable(pa.Table.from_pylist([]))
        schwab = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row("AAPL", ts, source="schwab-live"),
        ]))

        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )
        resp = reader.get_bars_union(
            "AAPL",
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            datetime(2024, 6, 2, tzinfo=timezone.utc),
        )
        assert resp.count == 1
        assert resp.bars[0].source_provider == "schwab-live"

    def test_polygon_wins_overlapping_timestamp(self) -> None:
        """When both tables have a row at the same (symbol, ts),
        polygon's row appears in the response. This pins the
        canonical-source rule from the v2 spec."""
        ts = datetime(2024, 6, 1, 14, 30, tzinfo=timezone.utc)
        polygon = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row("AAPL", ts, source="polygon-adjusted", close=150.50),
        ]))
        schwab = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row("AAPL", ts, source="schwab-live", close=150.51),
        ]))

        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )
        resp = reader.get_bars_union(
            "AAPL",
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            datetime(2024, 6, 2, tzinfo=timezone.utc),
        )
        assert resp.count == 1
        assert resp.bars[0].source_provider == "polygon-adjusted"
        assert resp.bars[0].close == 150.50

    def test_disjoint_windows_concatenate_sorted(self) -> None:
        """polygon covers history; schwab covers today. The merged
        response is one continuous timestamp-sorted series."""
        ts_history = datetime(2024, 5, 27, 14, 30, tzinfo=timezone.utc)
        ts_today_a = datetime(2024, 6, 3, 13, 30, tzinfo=timezone.utc)
        ts_today_b = datetime(2024, 6, 3, 13, 31, tzinfo=timezone.utc)
        polygon = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row("AAPL", ts_history, source="polygon-adjusted"),
        ]))
        schwab = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row("AAPL", ts_today_b, source="schwab-live"),
            _v2_row("AAPL", ts_today_a, source="schwab-live"),
        ]))

        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )
        resp = reader.get_bars_union(
            "AAPL",
            datetime(2024, 5, 25, tzinfo=timezone.utc),
            datetime(2024, 6, 4, tzinfo=timezone.utc),
        )
        assert resp.count == 3
        timestamps = [b.timestamp for b in resp.bars]
        assert timestamps == sorted(timestamps), "must be ascending"
        assert timestamps[0] == ts_history
        assert timestamps[1] == ts_today_a
        assert timestamps[2] == ts_today_b

    def test_empty_symbol_short_circuits(self) -> None:
        polygon = _FakeIcebergTable(
            pa.Table.from_pylist([]),
            scan_raises=AssertionError("must not scan when symbol blank"),
        )
        schwab = _FakeIcebergTable(
            pa.Table.from_pylist([]),
            scan_raises=AssertionError("must not scan when symbol blank"),
        )
        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )
        resp = reader.get_bars_union(
            "",
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            datetime(2024, 6, 2, tzinfo=timezone.utc),
        )
        assert resp.count == 0
        assert resp.bars == []

    def test_polygon_scan_failure_falls_back_to_schwab_only(self) -> None:
        """Single-source failure must not break the union — the surviving
        source still flows through. Logs the failure but returns rows."""
        ts = datetime(2024, 6, 1, 14, 30, tzinfo=timezone.utc)
        polygon = _FakeIcebergTable(
            pa.Table.from_pylist([]),
            scan_raises=RuntimeError("S3 timeout"),
        )
        schwab = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row("AAPL", ts, source="schwab-live"),
        ]))
        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )
        resp = reader.get_bars_union(
            "AAPL",
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            datetime(2024, 6, 2, tzinfo=timezone.utc),
        )
        assert resp.count == 1
        assert resp.bars[0].source_provider == "schwab-live"


class TestRouteIncludeLiveParam:
    """The /api/v1/adjusted/bars/{symbol}?include_live=... routing
    correctly chooses get_bars vs get_bars_union."""

    def test_default_uses_get_bars(self) -> None:
        from fastapi.testclient import TestClient

        app = _make_adjusted_app()
        stub = _StubReader(
            bars_response=SilverBarsResponse(
                symbol="AAPL",
                start=datetime(2024, 6, 1, tzinfo=timezone.utc),
                end=datetime(2024, 6, 2, tzinfo=timezone.utc),
                snapshot_id="s1", bars=[], count=0,
            ),
        )
        app.dependency_overrides[get_adjusted_ohlcv_reader] = lambda: stub

        with TestClient(app) as client:
            resp = client.get(
                "/api/adjusted/bars/AAPL",
                params={"start": "2024-06-01T00:00:00Z", "end": "2024-06-02T00:00:00Z"},
            )

        assert resp.status_code == 200
        assert stub.last_get_bars is not None
        assert stub.last_get_bars_union is None

    def test_include_live_true_uses_get_bars_union(self) -> None:
        from fastapi.testclient import TestClient

        app = _make_adjusted_app()
        stub = _StubReader(
            bars_response=SilverBarsResponse(
                symbol="AAPL",
                start=datetime(2024, 6, 1, tzinfo=timezone.utc),
                end=datetime(2024, 6, 2, tzinfo=timezone.utc),
                snapshot_id="s1", bars=[], count=0,
            ),
        )
        app.dependency_overrides[get_adjusted_ohlcv_reader] = lambda: stub

        with TestClient(app) as client:
            resp = client.get(
                "/api/adjusted/bars/AAPL",
                params={
                    "start": "2024-06-01T00:00:00Z",
                    "end": "2024-06-02T00:00:00Z",
                    "include_live": "true",
                },
            )

        assert resp.status_code == 200
        assert stub.last_get_bars is None
        assert stub.last_get_bars_union is not None


# ─────────────────────────────────────────────────────────────────────
# CV26 — get_symbol_coverage (per-symbol coverage across both sources)
# ─────────────────────────────────────────────────────────────────────


class TestGetSymbolCoverage:
    """Coverage now sources exact min/max/count from Athena (lake) +
    ClickHouse (hot cache); the PyIceberg tables are only consulted for
    snapshot_id + existence. These tests patch the Athena + CH helpers
    and assert the three-store response."""

    def _reader(self):
        # Fake tables provide snapshot_id (="12345") + existence only.
        polygon = _FakeIcebergTable(pa.Table.from_pylist([]))
        schwab = _FakeIcebergTable(pa.Table.from_pylist([]))
        return AdjustedOhlcvReader(ohlcv_table=polygon, schwab_table=schwab)

    @staticmethod
    def _patch(monkeypatch, *, athena, ch):
        """athena: dict table_name -> AthenaCoverage|None ; ch: dict."""
        from app.services.equities import athena_coverage as ac
        from app.db import queries

        def fake_athena(table, symbol, **kw):
            return athena.get(table)

        monkeypatch.setattr(ac, "symbol_coverage", fake_athena)
        monkeypatch.setattr(queries, "coverage_all", lambda s: ch)
        return ac

    def test_all_three_sources_populated(self, monkeypatch) -> None:
        from app.services.equities.athena_coverage import AthenaCoverage
        ts_a = datetime(2006, 1, 3, tzinfo=timezone.utc)
        ts_b = datetime(2026, 5, 27, tzinfo=timezone.utc)
        ts_c = datetime(2026, 6, 17, tzinfo=timezone.utc)
        self._patch(
            monkeypatch,
            athena={
                "polygon_adjusted": AthenaCoverage(100, ts_a, ts_b),
                "schwab_universe": AthenaCoverage(5, ts_c, ts_c),
            },
            ch={"symbol": "AAPL", "earliest": ts_a, "latest": ts_c, "bar_count": 105},
        )

        resp = self._reader().get_symbol_coverage("aapl")

        assert resp.symbol == "AAPL"
        # ClickHouse hot cache
        assert resp.clickhouse.table_name == "stocks.ohlcv_1m"
        assert resp.clickhouse.row_count == 105
        assert resp.clickhouse.earliest_timestamp == ts_a
        assert resp.clickhouse.latest_timestamp == ts_c
        # polygon_adjusted lake (Athena), snapshot from the fake table
        assert resp.polygon_adjusted.table_name == "equities.polygon_adjusted"
        assert resp.polygon_adjusted.row_count == 100
        assert resp.polygon_adjusted.earliest_timestamp == ts_a
        assert resp.polygon_adjusted.latest_timestamp == ts_b
        assert resp.polygon_adjusted.snapshot_id == "12345"
        # schwab_universe lake
        assert resp.schwab_universe.row_count == 5
        assert resp.schwab_universe.latest_timestamp == ts_c

    def test_polygon_empty_schwab_populated(self, monkeypatch) -> None:
        """Brand-new symbol — schwab has data, polygon not Spark-adjusted yet."""
        from app.services.equities.athena_coverage import AthenaCoverage
        ts = datetime(2026, 6, 3, tzinfo=timezone.utc)
        self._patch(
            monkeypatch,
            athena={
                "polygon_adjusted": AthenaCoverage(0, None, None),
                "schwab_universe": AthenaCoverage(1, ts, ts),
            },
            ch={"symbol": "NEWSYM", "earliest": ts, "latest": ts, "bar_count": 1},
        )

        resp = self._reader().get_symbol_coverage("NEWSYM")

        assert resp.polygon_adjusted.row_count == 0
        assert resp.polygon_adjusted.earliest_timestamp is None
        assert resp.schwab_universe.row_count == 1
        assert resp.clickhouse.row_count == 1

    def test_all_sources_empty_returns_zeros(self, monkeypatch) -> None:
        from app.services.equities.athena_coverage import AthenaCoverage
        self._patch(
            monkeypatch,
            athena={
                "polygon_adjusted": AthenaCoverage(0, None, None),
                "schwab_universe": AthenaCoverage(0, None, None),
            },
            ch={"symbol": "UNKNOWN", "earliest": None, "latest": None, "bar_count": 0},
        )

        resp = self._reader().get_symbol_coverage("UNKNOWN")
        assert resp.polygon_adjusted.row_count == 0
        assert resp.schwab_universe.row_count == 0
        assert resp.clickhouse.row_count == 0

    def test_athena_failure_degrades_to_empty(self, monkeypatch) -> None:
        """Athena returning None (timeout/error) must not break the
        response — the failed lake source reports row_count=0 but keeps
        its snapshot_id; the other stores still flow through."""
        from app.services.equities.athena_coverage import AthenaCoverage
        ts = datetime(2026, 6, 3, tzinfo=timezone.utc)
        self._patch(
            monkeypatch,
            athena={
                "polygon_adjusted": None,  # Athena failed
                "schwab_universe": AthenaCoverage(1, ts, ts),
            },
            ch={"symbol": "AAPL", "earliest": ts, "latest": ts, "bar_count": 1},
        )

        resp = self._reader().get_symbol_coverage("AAPL")
        assert resp.polygon_adjusted.row_count == 0
        assert resp.polygon_adjusted.snapshot_id == "12345"  # table exists
        assert resp.schwab_universe.row_count == 1
        assert resp.clickhouse.row_count == 1

    def test_sources_filter_queries_only_requested(self, monkeypatch) -> None:
        """`sources='clickhouse'` must NOT touch Athena (the slow path)."""
        from app.services.equities import athena_coverage as ac
        from app.db import queries
        ts = datetime(2026, 6, 3, tzinfo=timezone.utc)

        def boom(table, symbol, **kw):  # must not be called
            raise AssertionError("Athena queried despite sources=clickhouse")

        monkeypatch.setattr(ac, "symbol_coverage", boom)
        monkeypatch.setattr(
            queries, "coverage_all",
            lambda s: {"symbol": s, "earliest": ts, "latest": ts, "bar_count": 9},
        )

        resp = self._reader().get_symbol_coverage("AAPL", sources="clickhouse")
        assert resp.clickhouse.row_count == 9
        # Un-requested lake sources come back as empty placeholders
        assert resp.polygon_adjusted.row_count == 0
        assert resp.schwab_universe.row_count == 0

    def test_empty_symbol_short_circuits(self, monkeypatch) -> None:
        from app.services.equities import athena_coverage as ac
        from app.db import queries

        def boom_a(table, symbol, **kw):
            raise AssertionError("must not query Athena for blank symbol")

        def boom_ch(s):
            raise AssertionError("must not query CH for blank symbol")

        monkeypatch.setattr(ac, "symbol_coverage", boom_a)
        monkeypatch.setattr(queries, "coverage_all", boom_ch)

        resp = self._reader().get_symbol_coverage("   ")
        assert resp.polygon_adjusted.row_count == 0
        assert resp.schwab_universe.row_count == 0
        assert resp.clickhouse.row_count == 0


class TestRouteCoverage:
    def test_route_delegates_to_reader(self) -> None:
        from fastapi.testclient import TestClient

        app = _make_adjusted_app()

        class _CovStub:
            def __init__(self) -> None:
                self.last_symbol: Optional[str] = None
                self.last_sources: Any = "unset"

            def get_symbol_coverage(self, symbol, sources=None):
                self.last_symbol = symbol
                self.last_sources = sources
                from app.services.readers.schemas import SourceCoverage
                return SymbolCoverageResponse(
                    symbol=symbol.upper(),
                    polygon_adjusted=SourceCoverage(
                        table_name="equities.polygon_adjusted",
                        row_count=42,
                        snapshot_id="snap-poly",
                    ),
                    schwab_universe=SourceCoverage(
                        table_name="equities.schwab_universe",
                        row_count=7,
                        snapshot_id="snap-schwab",
                    ),
                    clickhouse=SourceCoverage(
                        table_name="stocks.ohlcv_1m",
                        row_count=99,
                    ),
                )

        stub = _CovStub()
        app.dependency_overrides[get_adjusted_ohlcv_reader] = lambda: stub

        with TestClient(app) as client:
            resp = client.get(
                "/api/adjusted/symbols/AAPL/coverage?sources=clickhouse"
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "AAPL"
        assert body["polygon_adjusted"]["row_count"] == 42
        assert body["schwab_universe"]["row_count"] == 7
        assert body["clickhouse"]["row_count"] == 99
        assert stub.last_symbol == "AAPL"
        assert stub.last_sources == "clickhouse"


# ─────────────────────────────────────────────────────────────────────
# CV27 — get_cross_provider_diff (polygon vs schwab close disagreements)
# ─────────────────────────────────────────────────────────────────────


def _v2_row_with_close(symbol: str, ts: datetime, close: float, source: str) -> dict:
    return {
        "symbol": symbol,
        "timestamp": ts,
        "open": close, "high": close, "low": close, "close": close,
        "volume": 1000.0, "vwap": close, "trade_count": 5,
        "source": source,
        "ingestion_ts": datetime(2024, 6, 1, tzinfo=timezone.utc),
        "ingestion_run_id": "run-x",
    }


class TestGetCrossProviderDiff:
    def test_agreement_under_tolerance_returns_no_rows(self) -> None:
        """Within-tolerance disagreements are NOT surfaced. Test the
        denominator (compared_count) is still reported."""
        ts1 = datetime(2024, 6, 1, 14, 30, tzinfo=timezone.utc)
        ts2 = datetime(2024, 6, 1, 14, 31, tzinfo=timezone.utc)
        polygon = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row_with_close("AAPL", ts1, 150.00, "polygon-adjusted"),
            _v2_row_with_close("AAPL", ts2, 150.10, "polygon-adjusted"),
        ]))
        schwab = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row_with_close("AAPL", ts1, 150.001, "schwab-live"),  # ~0.0007% diff
            _v2_row_with_close("AAPL", ts2, 150.099, "schwab-live"),  # ~0.0007% diff
        ]))
        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )

        resp = reader.get_cross_provider_diff(
            "AAPL",
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            datetime(2024, 6, 2, tzinfo=timezone.utc),
            tolerance=0.005,
        )
        assert resp.compared_count == 2
        assert resp.count == 0
        assert resp.disagreements == []

    def test_above_tolerance_surfaces_with_metrics(self) -> None:
        """When pct_diff exceeds tolerance the row appears with both
        prices + the diff metrics."""
        ts = datetime(2024, 6, 1, 14, 30, tzinfo=timezone.utc)
        polygon = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row_with_close("NVDA", ts, 100.00, "polygon-adjusted"),
        ]))
        schwab = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row_with_close("NVDA", ts, 95.00, "schwab-live"),  # 5% diff
        ]))
        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )

        resp = reader.get_cross_provider_diff(
            "NVDA",
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            datetime(2024, 6, 2, tzinfo=timezone.utc),
            tolerance=0.005,
        )
        assert resp.compared_count == 1
        assert resp.count == 1
        row = resp.disagreements[0]
        assert row.timestamp == ts
        assert row.polygon_close == 100.00
        assert row.schwab_close == 95.00
        assert row.abs_diff == 5.0
        # (100 - 95) / 100 = 0.05 ; polygon-higher sign positive.
        assert row.pct_diff == pytest.approx(0.05)

    def test_single_sided_rows_not_surfaced(self) -> None:
        """Rows only in polygon OR only in schwab are NOT
        disagreements — they're coverage gaps. Get coverage instead."""
        ts_both = datetime(2024, 6, 1, 14, 30, tzinfo=timezone.utc)
        ts_poly_only = datetime(2024, 6, 1, 14, 31, tzinfo=timezone.utc)
        ts_schwab_only = datetime(2024, 6, 1, 14, 32, tzinfo=timezone.utc)
        polygon = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row_with_close("AAPL", ts_both, 150.00, "polygon-adjusted"),
            _v2_row_with_close("AAPL", ts_poly_only, 200.00, "polygon-adjusted"),
        ]))
        schwab = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row_with_close("AAPL", ts_both, 150.50, "schwab-live"),
            _v2_row_with_close("AAPL", ts_schwab_only, 999.99, "schwab-live"),
        ]))
        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )

        resp = reader.get_cross_provider_diff(
            "AAPL",
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            datetime(2024, 6, 2, tzinfo=timezone.utc),
            tolerance=0.001,
        )
        assert resp.compared_count == 1  # only ts_both
        assert resp.count == 1
        assert resp.disagreements[0].timestamp == ts_both

    def test_multiple_disagreements_sorted_by_timestamp(self) -> None:
        ts1 = datetime(2024, 6, 1, 14, 30, tzinfo=timezone.utc)
        ts2 = datetime(2024, 6, 1, 14, 32, tzinfo=timezone.utc)
        ts3 = datetime(2024, 6, 1, 14, 31, tzinfo=timezone.utc)
        polygon = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row_with_close("AAPL", ts1, 100.00, "polygon-adjusted"),
            _v2_row_with_close("AAPL", ts2, 100.00, "polygon-adjusted"),
            _v2_row_with_close("AAPL", ts3, 100.00, "polygon-adjusted"),
        ]))
        schwab = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row_with_close("AAPL", ts1, 105.00, "schwab-live"),
            _v2_row_with_close("AAPL", ts2, 110.00, "schwab-live"),
            _v2_row_with_close("AAPL", ts3, 90.00, "schwab-live"),
        ]))
        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )

        resp = reader.get_cross_provider_diff(
            "AAPL",
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            datetime(2024, 6, 2, tzinfo=timezone.utc),
            tolerance=0.01,
        )
        assert resp.count == 3
        timestamps = [r.timestamp for r in resp.disagreements]
        assert timestamps == sorted(timestamps), "must be ASC"

    def test_empty_symbol_short_circuits(self) -> None:
        polygon = _FakeIcebergTable(
            pa.Table.from_pylist([]),
            scan_raises=AssertionError("must not scan when symbol blank"),
        )
        schwab = _FakeIcebergTable(
            pa.Table.from_pylist([]),
            scan_raises=AssertionError("must not scan when symbol blank"),
        )
        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )
        resp = reader.get_cross_provider_diff(
            "",
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            datetime(2024, 6, 2, tzinfo=timezone.utc),
        )
        assert resp.compared_count == 0
        assert resp.count == 0

    def test_scan_failure_one_side_degrades_to_no_compared(self) -> None:
        """If one source can't be scanned, compared_count=0 — no
        false-positive disagreements from one-sided data."""
        polygon = _FakeIcebergTable(
            pa.Table.from_pylist([]),
            scan_raises=RuntimeError("S3 timeout"),
        )
        schwab = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row_with_close("AAPL", datetime(2024, 6, 1, 14, 30, tzinfo=timezone.utc),
                               150.00, "schwab-live"),
        ]))
        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )
        resp = reader.get_cross_provider_diff(
            "AAPL",
            datetime(2024, 6, 1, tzinfo=timezone.utc),
            datetime(2024, 6, 2, tzinfo=timezone.utc),
        )
        assert resp.compared_count == 0
        assert resp.count == 0


class TestRouteCrossProviderDiff:
    def test_route_delegates_to_reader(self) -> None:
        from fastapi.testclient import TestClient
        from app.services.readers.schemas import (
            CrossProviderDiffResponse,
            CrossProviderDiffRow,
        )

        app = _make_adjusted_app()

        captured: dict = {}

        class _DiffStub:
            def get_cross_provider_diff(
                self, symbol, start, end, *, tolerance,
            ) -> CrossProviderDiffResponse:
                captured.update({
                    "symbol": symbol, "start": start,
                    "end": end, "tolerance": tolerance,
                })
                return CrossProviderDiffResponse(
                    symbol=symbol.upper(),
                    start=start, end=end,
                    tolerance=tolerance,
                    compared_count=10,
                    disagreements=[
                        CrossProviderDiffRow(
                            timestamp=start,
                            polygon_close=100.0,
                            schwab_close=95.0,
                            abs_diff=5.0,
                            pct_diff=0.05,
                        )
                    ],
                    count=1,
                )

        app.dependency_overrides[get_adjusted_ohlcv_reader] = lambda: _DiffStub()

        with TestClient(app) as client:
            resp = client.get(
                "/api/adjusted/symbols/AAPL/diff",
                params={
                    "start": "2024-06-01T13:30:00Z",
                    "end": "2024-06-02T20:00:00Z",
                    "tolerance": "0.01",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == "AAPL"
        assert body["tolerance"] == 0.01
        assert body["compared_count"] == 10
        assert body["count"] == 1
        assert body["disagreements"][0]["abs_diff"] == 5.0
        assert captured["tolerance"] == 0.01

    def test_invalid_window_returns_400(self) -> None:
        from fastapi.testclient import TestClient

        app = _make_adjusted_app()
        app.dependency_overrides[get_adjusted_ohlcv_reader] = lambda: _StubReader()

        with TestClient(app) as client:
            resp = client.get(
                "/api/adjusted/symbols/AAPL/diff",
                params={
                    "start": "2024-07-01T00:00:00Z",
                    "end": "2024-06-01T00:00:00Z",
                },
            )
        assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────
# CV28 — list_symbols (universe discovery via UNION of v2 sources)
# ─────────────────────────────────────────────────────────────────────


class TestListSymbols:
    def test_union_dedupes_overlapping_symbols(self) -> None:
        """A symbol present in BOTH sources appears once in the
        sorted union, not twice."""
        ts = datetime(2024, 6, 1, 14, 30, tzinfo=timezone.utc)
        polygon = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row("AAPL", ts, source="polygon-adjusted"),
            _v2_row("NVDA", ts, source="polygon-adjusted"),
            _v2_row("MSFT", ts, source="polygon-adjusted"),
        ]))
        schwab = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row("AAPL", ts, source="schwab-live"),
            _v2_row("GOOG", ts, source="schwab-live"),
        ]))
        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )

        resp = reader.list_symbols()

        assert resp.symbols == ["AAPL", "GOOG", "MSFT", "NVDA"]
        assert resp.count == 4
        assert set(resp.sources_scanned) == {
            "equities.polygon_adjusted",
            "equities.schwab_universe",
        }

    def test_sources_filter_polygon_only(self) -> None:
        ts = datetime(2024, 6, 1, 14, 30, tzinfo=timezone.utc)
        polygon = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row("AAPL", ts, source="polygon-adjusted"),
            _v2_row("NVDA", ts, source="polygon-adjusted"),
        ]))
        schwab = _FakeIcebergTable(
            pa.Table.from_pylist([
                _v2_row("GOOG", ts, source="schwab-live"),
            ]),
            scan_raises=AssertionError(
                "must not scan when sources excludes schwab"
            ),
        )
        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )

        resp = reader.list_symbols(sources=["polygon_adjusted"])

        assert resp.symbols == ["AAPL", "NVDA"]
        assert resp.sources_scanned == ["equities.polygon_adjusted"]

    def test_unknown_source_is_logged_not_raised(self) -> None:
        polygon = _FakeIcebergTable(pa.Table.from_pylist([]))
        schwab = _FakeIcebergTable(pa.Table.from_pylist([]))
        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )

        resp = reader.list_symbols(
            sources=["typo_table", "polygon_adjusted"],
        )
        assert resp.sources_scanned == ["equities.polygon_adjusted"]

    def test_limit_truncates_after_sort(self) -> None:
        ts = datetime(2024, 6, 1, 14, 30, tzinfo=timezone.utc)
        polygon = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row("ZZZZ", ts, source="polygon-adjusted"),
            _v2_row("AAPL", ts, source="polygon-adjusted"),
            _v2_row("MMMM", ts, source="polygon-adjusted"),
        ]))
        schwab = _FakeIcebergTable(pa.Table.from_pylist([]))
        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )

        resp = reader.list_symbols(limit=2)
        assert resp.symbols == ["AAPL", "MMMM"]

    def test_default_since_is_30d_back(self) -> None:
        polygon = _FakeIcebergTable(pa.Table.from_pylist([]))
        schwab = _FakeIcebergTable(pa.Table.from_pylist([]))
        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )

        before = datetime.now(timezone.utc)
        resp = reader.list_symbols()
        after = datetime.now(timezone.utc)

        expected_low = before - timedelta(days=30, seconds=2)
        expected_high = after - timedelta(days=30) + timedelta(seconds=2)
        assert expected_low <= resp.since <= expected_high

    def test_scan_failure_includes_attempted_source(self) -> None:
        ts = datetime(2024, 6, 1, 14, 30, tzinfo=timezone.utc)
        polygon = _FakeIcebergTable(
            pa.Table.from_pylist([]),
            scan_raises=RuntimeError("S3 timeout"),
        )
        schwab = _FakeIcebergTable(pa.Table.from_pylist([
            _v2_row("AAPL", ts, source="schwab-live"),
        ]))
        reader = AdjustedOhlcvReader(
            ohlcv_table=polygon, schwab_table=schwab,
        )

        resp = reader.list_symbols()
        assert resp.symbols == ["AAPL"]
        # sources_scanned reflects what was attempted (honesty);
        # symbols reflects what came back.
        assert "equities.polygon_adjusted" in resp.sources_scanned
        assert "equities.schwab_universe" in resp.sources_scanned


class TestRouteListAdjustedSymbols:
    def test_default_route_calls_reader_with_defaults(self) -> None:
        from fastapi.testclient import TestClient
        from app.services.readers.schemas import AdjustedSymbolsResponse

        app = _make_adjusted_app()
        captured: dict = {}

        class _Stub:
            def list_symbols(self, *, since=None, sources=None, limit=None):
                captured.update({
                    "since": since, "sources": sources, "limit": limit,
                })
                return AdjustedSymbolsResponse(
                    sources_scanned=[
                        "equities.polygon_adjusted",
                        "equities.schwab_universe",
                    ],
                    since=datetime(2024, 5, 1, tzinfo=timezone.utc),
                    symbols=["AAPL", "NVDA"],
                    count=2,
                )

        app.dependency_overrides[get_adjusted_ohlcv_reader] = lambda: _Stub()

        with TestClient(app) as client:
            resp = client.get("/api/adjusted/symbols")

        assert resp.status_code == 200
        body = resp.json()
        assert body["symbols"] == ["AAPL", "NVDA"]
        assert body["count"] == 2
        assert captured["since"] is None
        assert captured["sources"] is None
        assert captured["limit"] is None

    def test_sources_param_parses_csv(self) -> None:
        from fastapi.testclient import TestClient
        from app.services.readers.schemas import AdjustedSymbolsResponse

        app = _make_adjusted_app()
        captured: dict = {}

        class _Stub:
            def list_symbols(self, *, since=None, sources=None, limit=None):
                captured["sources"] = sources
                return AdjustedSymbolsResponse(
                    sources_scanned=[],
                    since=datetime.now(timezone.utc),
                    symbols=[], count=0,
                )

        app.dependency_overrides[get_adjusted_ohlcv_reader] = lambda: _Stub()

        with TestClient(app) as client:
            client.get(
                "/api/adjusted/symbols",
                params={"sources": "polygon_adjusted, schwab_universe ,"},
            )

        assert captured["sources"] == [
            "polygon_adjusted", "schwab_universe",
        ]

    def test_limit_over_max_returns_422(self) -> None:
        from fastapi.testclient import TestClient

        app = _make_adjusted_app()
        app.dependency_overrides[get_adjusted_ohlcv_reader] = lambda: _StubReader()

        with TestClient(app) as client:
            resp = client.get(
                "/api/adjusted/symbols", params={"limit": "100000"},
            )
        assert resp.status_code == 422
