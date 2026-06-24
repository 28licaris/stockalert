"""Unit tests for `EquitiesIcebergSink` (CV2).

Covers the canonical bar-write path used by Phase 1B writers
(nightly_polygon_refresh, schwab_tip_fill, live_lake_writer) and by
the history-backfill script (CV3). Uses mocked PyIceberg tables so the
suite runs offline.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
import pyarrow as pa
import pytest

pyiceberg = pytest.importorskip("pyiceberg")

from app.services.equities.sink import (  # noqa: E402
    _POLYGON_RAW_ARROW,
    _SCHWAB_UNIVERSE_ARROW,
    EquitiesIcebergSink,
)


def _make_table_mock(name: str = "lake.equities.test"):
    """Build a MagicMock PyIceberg `Table` capable of append + refresh."""
    table = MagicMock()
    table.name.return_value = name
    # `current_snapshot()` returns an object with `.snapshot_id`.
    snap = MagicMock()
    snap.snapshot_id = 12345
    table.current_snapshot.return_value = snap
    return table


def _canonical_frame(rows: int = 3, *, with_null_symbol: bool = False) -> pd.DataFrame:
    """Build a minimal canonical OHLCV frame the sink expects."""
    base = {
        "symbol": ["AAPL", "MSFT", "GOOG"][:rows],
        "timestamp": pd.to_datetime(
            ["2024-01-02 14:30:00", "2024-01-02 14:31:00", "2024-01-02 14:32:00"][:rows],
            utc=True,
        ),
        "open": [150.0, 200.0, 100.0][:rows],
        "high": [151.0, 201.0, 101.0][:rows],
        "low": [149.0, 199.0, 99.0][:rows],
        "close": [150.5, 200.5, 100.5][:rows],
        "volume": [1000.0, 2000.0, 3000.0][:rows],
        "vwap": [150.25, 200.25, 100.25][:rows],
        "trade_count": [10, 20, 30][:rows],
        "source": ["polygon-flatfiles"] * rows,
    }
    df = pd.DataFrame(base)
    if with_null_symbol:
        df.loc[0, "symbol"] = None
    return df


# ─────────────────────────────────────────────────────────────────────
# Arrow-schema parity contracts (regression catchers)
# ─────────────────────────────────────────────────────────────────────

def test_polygon_raw_arrow_has_12_columns_no_adj_factor():
    assert "adj_factor" not in _POLYGON_RAW_ARROW.names
    assert len(_POLYGON_RAW_ARROW) == 12


def test_schwab_arrow_includes_required_adj_factor():
    field = _SCHWAB_UNIVERSE_ARROW.field("adj_factor")
    assert field.type == pa.float64()
    assert field.nullable is False


def test_schwab_and_polygon_arrow_share_canonical_columns():
    """Cross-provider UNION needs identical column names+order for the
    first 12 columns (02_schema.md). adj_factor is the only new col."""
    assert _POLYGON_RAW_ARROW.names == _SCHWAB_UNIVERSE_ARROW.names[:12]


# ─────────────────────────────────────────────────────────────────────
# Factory wiring
# ─────────────────────────────────────────────────────────────────────

def test_for_polygon_raw_factory_wires_schema_and_providers(monkeypatch):
    fake_table = _make_table_mock("lake.equities.polygon_raw")
    monkeypatch.setattr(
        "app.services.equities.sink.get_catalog", lambda: MagicMock()
    )
    monkeypatch.setattr(
        "app.services.equities.sink.ensure_polygon_raw", lambda catalog: fake_table
    )

    sink = EquitiesIcebergSink.for_polygon_raw()

    assert sink.name == "equities_polygon_raw"
    assert sink.table is fake_table
    assert ("polygon", "minute") in sink._accepted_providers
    assert ("polygon-flatfiles", "minute") in sink._accepted_providers
    assert sink._static_adj_factor is None
    assert "adj_factor" not in sink._arrow_schema.names


def test_for_schwab_universe_factory_stamps_unit_adj_factor(monkeypatch):
    fake_table = _make_table_mock("lake.equities.schwab_universe")
    monkeypatch.setattr(
        "app.services.equities.sink.get_catalog", lambda: MagicMock()
    )
    monkeypatch.setattr(
        "app.services.equities.sink.ensure_schwab_universe",
        lambda catalog: fake_table,
    )

    sink = EquitiesIcebergSink.for_schwab_universe()

    assert sink.name == "equities_schwab_universe"
    assert sink._static_adj_factor == 1.0
    assert ("schwab", "minute") in sink._accepted_providers
    assert ("schwab-live", "minute") in sink._accepted_providers
    assert "adj_factor" in sink._arrow_schema.names


# ─────────────────────────────────────────────────────────────────────
# write() contract — skip paths
# ─────────────────────────────────────────────────────────────────────

def test_write_skips_unsupported_provider():
    sink = EquitiesIcebergSink(
        table=_make_table_mock(),
        name="t",
        arrow_schema=_POLYGON_RAW_ARROW,
        accepted_providers={("polygon", "minute")},
    )
    result = asyncio.run(
        sink.write(_canonical_frame(), file_date=date(2024, 1, 2),
                   kind="minute", provider="schwab")
    )
    assert result.status == "skipped"
    assert "unsupported" in result.metadata["reason"]


def test_write_skips_empty_frame():
    sink = EquitiesIcebergSink(
        table=_make_table_mock(), name="t",
        arrow_schema=_POLYGON_RAW_ARROW,
    )
    result = asyncio.run(
        sink.write(pd.DataFrame(), file_date=date(2024, 1, 2),
                   kind="minute", provider="polygon")
    )
    assert result.status == "skipped"
    assert result.metadata["reason"] == "empty_frame"


def test_write_returns_error_when_required_cols_missing():
    sink = EquitiesIcebergSink(
        table=_make_table_mock(), name="t",
        arrow_schema=_POLYGON_RAW_ARROW,
    )
    bad = pd.DataFrame({"symbol": ["AAPL"], "timestamp": [pd.Timestamp.now(tz="UTC")]})
    result = asyncio.run(
        sink.write(bad, file_date=date(2024, 1, 2),
                   kind="minute", provider="polygon")
    )
    assert result.status == "error"
    assert "missing required columns" in result.error


# ─────────────────────────────────────────────────────────────────────
# write() contract — happy path
# ─────────────────────────────────────────────────────────────────────

def test_write_appends_and_stamps_ingestion_columns():
    table = _make_table_mock("lake.equities.polygon_raw")
    sink = EquitiesIcebergSink(
        table=table, name="equities_polygon_raw",
        arrow_schema=_POLYGON_RAW_ARROW,
    )
    result = asyncio.run(
        sink.write(_canonical_frame(rows=3), file_date=date(2024, 1, 2),
                   kind="minute", provider="polygon-flatfiles")
    )

    assert result.status == "ok"
    assert result.bars_written == 3
    table.append.assert_called_once()

    appended = table.append.call_args.args[0]
    assert isinstance(appended, pa.Table)
    assert appended.num_rows == 3
    # Every row must carry the same run_id and a non-null ingestion_ts.
    run_ids = appended.column("ingestion_run_id").to_pylist()
    assert len(set(run_ids)) == 1
    assert run_ids[0] == result.metadata["ingestion_run_id"]
    ts_col = appended.column("ingestion_ts").to_pylist()
    assert all(ts is not None for ts in ts_col)


def test_write_schwab_path_stamps_adj_factor_1_on_every_row():
    table = _make_table_mock("lake.equities.schwab_universe")
    sink = EquitiesIcebergSink(
        table=table, name="equities_schwab_universe",
        arrow_schema=_SCHWAB_UNIVERSE_ARROW,
        static_adj_factor=1.0,
    )
    asyncio.run(
        sink.write(_canonical_frame(rows=3), file_date=date(2024, 1, 2),
                   kind="minute", provider="polygon")  # accept-all sink
    )

    appended = table.append.call_args.args[0]
    adj = appended.column("adj_factor").to_pylist()
    assert adj == [1.0, 1.0, 1.0]


# ─────────────────────────────────────────────────────────────────────
# Data-quality boundary
# ─────────────────────────────────────────────────────────────────────

def test_write_drops_rows_with_null_symbol():
    table = _make_table_mock()
    sink = EquitiesIcebergSink(
        table=table, name="t", arrow_schema=_POLYGON_RAW_ARROW,
    )
    df = _canonical_frame(rows=3, with_null_symbol=True)

    result = asyncio.run(
        sink.write(df, file_date=date(2024, 1, 2),
                   kind="minute", provider="polygon")
    )

    assert result.status == "ok"
    assert result.bars_written == 2
    assert result.metadata["rows_in"] == 3
    assert result.metadata["rows_dropped_null_symbol"] == 1


def test_write_treats_vwap_zero_as_null():
    table = _make_table_mock()
    sink = EquitiesIcebergSink(
        table=table, name="t", arrow_schema=_POLYGON_RAW_ARROW,
    )
    df = _canonical_frame(rows=3)
    df.loc[1, "vwap"] = 0.0

    asyncio.run(sink.write(df, file_date=date(2024, 1, 2),
                           kind="minute", provider="polygon"))

    appended = table.append.call_args.args[0]
    vwap = appended.column("vwap").to_pylist()
    assert vwap[0] == 150.25
    assert vwap[1] is None
    assert vwap[2] == 100.25


def test_write_filtered_to_zero_rows_returns_skipped():
    table = _make_table_mock()
    sink = EquitiesIcebergSink(
        table=table, name="t", arrow_schema=_POLYGON_RAW_ARROW,
    )
    df = _canonical_frame(rows=1, with_null_symbol=True)

    result = asyncio.run(
        sink.write(df, file_date=date(2024, 1, 2),
                   kind="minute", provider="polygon")
    )

    assert result.status == "skipped"
    assert result.metadata["reason"] == "no_valid_rows_after_filter"
    table.append.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# Error path
# ─────────────────────────────────────────────────────────────────────

def test_write_returns_error_when_append_raises():
    table = _make_table_mock()
    table.append.side_effect = RuntimeError("S3 timeout")
    sink = EquitiesIcebergSink(
        table=table, name="t", arrow_schema=_POLYGON_RAW_ARROW,
    )

    result = asyncio.run(
        sink.write(_canonical_frame(rows=2), file_date=date(2024, 1, 2),
                   kind="minute", provider="polygon")
    )

    assert result.status == "error"
    assert "S3 timeout" in result.error
    assert "ingestion_run_id" in result.metadata


def test_write_returns_ok_even_if_snapshot_refresh_fails():
    """A refresh failure post-append must not poison an otherwise
    successful write (matches v1 BronzeIcebergSink contract)."""
    table = _make_table_mock()
    table.refresh.side_effect = RuntimeError("transient")
    sink = EquitiesIcebergSink(
        table=table, name="t", arrow_schema=_POLYGON_RAW_ARROW,
    )

    result = asyncio.run(
        sink.write(_canonical_frame(rows=1), file_date=date(2024, 1, 2),
                   kind="minute", provider="polygon")
    )

    assert result.status == "ok"
    assert result.bars_written == 1
    assert result.metadata.get("snapshot_id_after") is None
