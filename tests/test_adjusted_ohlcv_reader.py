"""
Tests for AdjustedOhlcvReader + the `/api/silver/...` HTTP routes.

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

from app.api import routes_adjusted
from app.api.routes_adjusted import get_adjusted_ohlcv_reader
from app.services.readers.schemas import (
    BarQualityResponse,
    BarQualityRow,
    SilverBarsResponse,
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
