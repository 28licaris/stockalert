"""Unit tests for the futures CH reconcile path (F3).

``reconcile_ch_from_futures`` must read ``futures.schwab_futures`` and
bulk-insert into ``stocks.futures_ohlcv_1m`` with a futures-specific source
tag. Mocked lake table + CH client keep the suite offline.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pyarrow as pa
import pytest

pytest.importorskip("pyiceberg")

from app.services.ingest import ch_reconcile  # noqa: E402


def _futures_lake_arrow() -> pa.Table:
    return pa.table({
        "symbol": ["/ES", "/ES", "/MES"],
        "timestamp": pd.to_datetime(
            ["2026-06-16T14:30:00Z", "2026-06-16T14:31:00Z", "2026-06-16T14:30:00Z"]
        ),
        "open": [7559.5, 7559.75, 7559.5],
        "high": [7560.0, 7560.25, 7560.0],
        "low": [7559.0, 7559.5, 7559.0],
        "close": [7559.75, 7560.0, 7559.75],
        "volume": [1200.0, 90.0, 50.0],
        "vwap": [None, None, None],
        "trade_count": [None, None, None],
        "source": ["schwab", "schwab", "schwab"],
    })


def test_reconcile_futures_targets_futures_ch_table(monkeypatch):
    table = MagicMock()
    table.name.return_value = "lake.futures.schwab_futures"
    table.scan.return_value.to_arrow.return_value = _futures_lake_arrow()
    monkeypatch.setattr(
        "app.services.futures.tables.ensure_schwab_futures", lambda: table
    )

    client = MagicMock()
    monkeypatch.setattr("app.db.client.get_client", lambda: client)

    result = ch_reconcile.reconcile_ch_from_futures(lookback_days=7)

    assert result["rows"] == 3
    assert result["symbols"] == 2  # /ES, /MES
    client.insert.assert_called_once()
    args, kwargs = client.insert.call_args
    assert args[0] == "stocks.futures_ohlcv_1m"
    inserted_rows = args[1]
    assert len(inserted_rows) == 3
    # source tag is futures-specific; version is identical across the batch.
    assert all(r[-2] == "lake-reconcile-schwab_futures" for r in inserted_rows)
    assert len({r[-1] for r in inserted_rows}) == 1
    assert kwargs["column_names"] == ch_reconcile._CH_COLUMNS


def test_reconcile_futures_empty_lake_is_noop(monkeypatch):
    table = MagicMock()
    table.name.return_value = "lake.futures.schwab_futures"
    table.scan.return_value.to_arrow.return_value = _futures_lake_arrow().slice(0, 0)
    monkeypatch.setattr(
        "app.services.futures.tables.ensure_schwab_futures", lambda: table
    )
    client = MagicMock()
    monkeypatch.setattr("app.db.client.get_client", lambda: client)

    result = ch_reconcile.reconcile_ch_from_futures(lookback_days=7)

    assert result["rows"] == 0
    client.insert.assert_not_called()


def test_reconcile_futures_table_load_failure_returns_error(monkeypatch):
    def _boom():
        raise RuntimeError("Glue NoSuchTable")

    monkeypatch.setattr(
        "app.services.futures.tables.ensure_schwab_futures", _boom
    )
    result = ch_reconcile.reconcile_ch_from_futures(lookback_days=7)
    assert result["rows"] == 0
    assert "Glue NoSuchTable" in result["error"]
