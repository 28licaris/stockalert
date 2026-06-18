"""Unit tests for the futures Iceberg sink (F3).

Futures reuse the generic ``EquitiesIcebergSink`` writer but with the
no-adjustment futures arrow schema. Mocked PyIceberg tables keep the suite
offline.
"""
from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pyarrow as pa
import pytest

pytest.importorskip("pyiceberg")

from app.services.equities.sink import _POLYGON_RAW_ARROW  # noqa: E402
from app.services.futures.sink import (  # noqa: E402
    _SCHWAB_FUTURES_ARROW,
    futures_iceberg_sink,
)


def _make_table_mock(name: str = "lake.futures.schwab_futures"):
    table = MagicMock()
    table.name.return_value = name
    snap = MagicMock()
    snap.snapshot_id = 999
    table.current_snapshot.return_value = snap
    return table


def _futures_frame(rows: int = 2) -> pd.DataFrame:
    """Minimal canonical OHLCV frame for futures roots (vwap/trade_count
    NaN — Schwab pricehistory returns neither)."""
    return pd.DataFrame({
        "symbol": ["/ES", "/MES"][:rows],
        "timestamp": pd.to_datetime(
            ["2026-06-16 14:30:00", "2026-06-16 14:31:00"][:rows], utc=True,
        ),
        "open": [7559.5, 7559.75][:rows],
        "high": [7560.0, 7560.25][:rows],
        "low": [7559.0, 7559.5][:rows],
        "close": [7559.75, 7560.0][:rows],
        "volume": [1200.0, 90.0][:rows],
        "vwap": [pd.NA, pd.NA][:rows],
        "trade_count": [pd.NA, pd.NA][:rows],
        "source": ["schwab"] * rows,
    })


# ── Arrow-schema contract ────────────────────────────────────────────

def test_futures_arrow_has_12_columns_no_adj_factor():
    assert "adj_factor" not in _SCHWAB_FUTURES_ARROW.names
    assert len(_SCHWAB_FUTURES_ARROW) == 12


def test_futures_arrow_matches_polygon_raw_canonical_shape():
    """Futures share the canonical 12-col layout (no adj_factor) so the
    same row builder + CH insert path works for both asset classes."""
    assert _SCHWAB_FUTURES_ARROW.names == _POLYGON_RAW_ARROW.names


# ── Factory wiring ───────────────────────────────────────────────────

def test_futures_factory_wires_schema_providers_no_adj(monkeypatch):
    fake_table = _make_table_mock()
    monkeypatch.setattr("app.services.futures.sink.get_catalog", lambda: MagicMock())
    monkeypatch.setattr(
        "app.services.futures.sink.ensure_schwab_futures", lambda catalog: fake_table
    )

    sink = futures_iceberg_sink()

    assert sink.name == "futures_schwab_futures"
    assert sink.table is fake_table
    assert sink._static_adj_factor is None
    assert ("schwab", "minute") in sink._accepted_providers
    assert "adj_factor" not in sink._arrow_schema.names


# ── write() happy path — no adj_factor column emitted ────────────────

def test_futures_write_appends_without_adj_factor():
    from app.services.equities.sink import EquitiesIcebergSink

    table = _make_table_mock()
    sink = EquitiesIcebergSink(
        table=table,
        name="futures_schwab_futures",
        arrow_schema=_SCHWAB_FUTURES_ARROW,
        accepted_providers={("schwab", "minute")},
        static_adj_factor=None,
    )
    result = asyncio.run(
        sink.write(_futures_frame(rows=2), file_date=date(2026, 6, 16),
                   kind="minute", provider="schwab")
    )

    assert result.status == "ok"
    assert result.bars_written == 2
    appended = table.append.call_args.args[0]
    assert isinstance(appended, pa.Table)
    assert "adj_factor" not in appended.column_names
    # vwap/trade_count came in as NA → NULL.
    assert appended.column("vwap").to_pylist() == [None, None]
    assert appended.column("trade_count").to_pylist() == [None, None]
