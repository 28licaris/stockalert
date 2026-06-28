from __future__ import annotations

from datetime import date, datetime, timezone

import pyarrow as pa

from app.services.readers.options_reader import OptionsReader


class _Snapshot:
    snapshot_id = 777


class _Scan:
    def __init__(self, arrow):
        self._arrow = arrow

    def to_arrow(self):
        return self._arrow


class _Table:
    def __init__(self, arrow, *, raises: Exception | None = None) -> None:
        self.arrow = arrow
        self.raises = raises
        self.scan_calls = []

    def scan(self, **kwargs):
        self.scan_calls.append(kwargs)
        if self.raises:
            raise self.raises
        return _Scan(self.arrow)

    def current_snapshot(self):
        return _Snapshot()


class _Catalog:
    def __init__(self, table=None, raises: Exception | None = None) -> None:
        self.table = table
        self.raises = raises
        self.loaded = []

    def load_table(self, table_id: str):
        self.loaded.append(table_id)
        if self.raises:
            raise self.raises
        return self.table


def _contract_row(**overrides):
    row = {
        "underlying_symbol": "AAPL",
        "option_symbol": "AAPL  260717C00150000",
        "snapshot_ts": datetime(2026, 6, 27, 14, 30, tzinfo=timezone.utc),
        "put_call": "CALL",
        "expiration_date": date(2026, 7, 17),
        "strike": 150.0,
        "underlying_price": 149.5,
        "days_to_expiration": 20,
        "bid": 1.1,
        "ask": 1.2,
        "last": 1.15,
        "mark": 1.15,
        "bid_size": 10,
        "ask_size": 11,
        "last_size": 1,
        "volume": 100,
        "open_interest": 1000,
        "quote_time": None,
        "trade_time": None,
        "delta": 0.5,
        "gamma": 0.02,
        "theta": -0.01,
        "vega": 0.12,
        "rho": 0.01,
        "volatility": 0.3,
        "theoretical_value": 1.2,
        "intrinsic_value": 0.0,
        "time_value": 1.15,
        "in_the_money": False,
        "mini": False,
        "non_standard": False,
        "penny_pilot": True,
        "multiplier": 100.0,
        "settlement_type": "P",
        "expiration_type": "S",
        "source": "schwab-chain",
        "ingestion_ts": datetime(2026, 6, 27, 14, 31, tzinfo=timezone.utc),
        "ingestion_run_id": "run-1",
    }
    row.update(overrides)
    return row


def _gamma_row(**overrides):
    row = {
        "underlying_symbol": "AAPL",
        "snapshot_ts": datetime(2026, 6, 27, 14, 30, tzinfo=timezone.utc),
        "expiration_date": None,
        "strike": None,
        "put_call": None,
        "underlying_price": 149.5,
        "gamma_exposure": 1000.0,
        "call_gamma_exposure": 1200.0,
        "put_gamma_exposure": -200.0,
        "net_gamma_exposure": 1000.0,
        "open_interest": 100,
        "volume": 50,
        "contract_count": 10,
        "aggregation_level": "total",
        "level_key": "total",
        "methodology": "stockalert-schwab-gex-v1",
        "source": "stockalert-schwab-gex",
        "source_snapshot_id": "run-1",
        "ingestion_ts": datetime(2026, 6, 27, 14, 31, tzinfo=timezone.utc),
        "ingestion_run_id": "run-1",
    }
    row.update(overrides)
    return row


def test_get_contracts_reads_sorts_limits_and_echoes_snapshot() -> None:
    table = _Table(
        pa.Table.from_pylist(
            [
                _contract_row(
                    option_symbol="AAPL  260717C00155000",
                    strike=155.0,
                    snapshot_ts=datetime(2026, 6, 27, 14, 31, tzinfo=timezone.utc),
                ),
                _contract_row(
                    option_symbol="AAPL  260717P00145000",
                    put_call="PUT",
                    strike=145.0,
                    snapshot_ts=datetime(2026, 6, 27, 14, 30, tzinfo=timezone.utc),
                ),
            ]
        )
    )
    reader = OptionsReader(contracts_table=table)

    response = reader.get_contracts(
        "aapl",
        datetime(2026, 6, 27, 14, 0, tzinfo=timezone.utc),
        datetime(2026, 6, 27, 15, 0, tzinfo=timezone.utc),
        put_call="call",
        limit=1,
    )

    assert response.underlying_symbol == "AAPL"
    assert response.snapshot_id == "777"
    assert response.count == 1
    assert response.contracts[0].option_symbol == "AAPL  260717C00155000"
    scan_kwargs = table.scan_calls[0]
    assert "row_filter" in scan_kwargs
    assert "option_symbol" in scan_kwargs["selected_fields"]


def test_get_contracts_returns_empty_when_table_not_loadable() -> None:
    reader = OptionsReader(catalog=_Catalog(raises=RuntimeError("missing")))

    response = reader.get_contracts(
        "AAPL",
        datetime(2026, 6, 27, tzinfo=timezone.utc),
        datetime(2026, 6, 28, tzinfo=timezone.utc),
    )

    assert response.contracts == []
    assert response.count == 0


def test_get_contracts_blank_symbol_does_not_load_table() -> None:
    catalog = _Catalog(raises=RuntimeError("should not load"))
    reader = OptionsReader(catalog=catalog)

    response = reader.get_contracts(
        " ",
        datetime(2026, 6, 27, tzinfo=timezone.utc),
        datetime(2026, 6, 28, tzinfo=timezone.utc),
    )

    assert response.count == 0
    assert catalog.loaded == []


def test_get_gamma_exposure_reads_pinned_snapshot() -> None:
    table = _Table(
        pa.Table.from_pylist(
            [
                _gamma_row(
                    level_key="strike:150",
                    aggregation_level="strike",
                    strike=150.0,
                ),
                _gamma_row(level_key="total", aggregation_level="total"),
            ]
        )
    )
    reader = OptionsReader(gamma_table=table)

    response = reader.get_gamma_exposure(
        "aapl",
        datetime(2026, 6, 27, 14, 0, tzinfo=timezone.utc),
        datetime(2026, 6, 27, 15, 0, tzinfo=timezone.utc),
        aggregation_level="total",
        snapshot_id="42",
        limit=1,
    )

    assert response.underlying_symbol == "AAPL"
    assert response.aggregation_level == "total"
    assert response.snapshot_id == "42"
    assert response.count == 1
    assert response.rows[0].level_key == "total"
    assert table.scan_calls[0]["snapshot_id"] == 42


def test_get_gamma_exposure_returns_empty_on_scan_failure() -> None:
    table = _Table(
        pa.Table.from_pylist([_gamma_row()]),
        raises=RuntimeError("scan failed"),
    )
    reader = OptionsReader(gamma_table=table)

    response = reader.get_gamma_exposure(
        "AAPL",
        datetime(2026, 6, 27, tzinfo=timezone.utc),
        datetime(2026, 6, 28, tzinfo=timezone.utc),
    )

    assert response.rows == []
    assert response.count == 0
