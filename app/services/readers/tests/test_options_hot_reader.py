from __future__ import annotations

from datetime import date, datetime, timezone

from app.services.readers.options_hot_reader import OptionsHotReader


class _Result:
    def __init__(self, rows):
        self.result_rows = rows


class _Client:
    def __init__(self, rows=None, raises: Exception | None = None) -> None:
        self.rows = rows or []
        self.raises = raises
        self.calls = []

    def query(self, sql, parameters):
        self.calls.append((sql, parameters))
        if self.raises:
            raise self.raises
        return _Result(self.rows)


def _contract_row():
    return (
        "AAPL",
        "AAPL  260717C00150000",
        datetime(2026, 6, 27, 14, 30, tzinfo=timezone.utc),
        "CALL",
        date(2026, 7, 17),
        150.0,
        149.5,
        20,
        1.1,
        1.2,
        1.15,
        1.15,
        100,
        1000,
        0.5,
        0.02,
        -0.01,
        0.12,
        0.01,
        0.3,
        0,
        100.0,
        "schwab-chain",
        datetime(2026, 6, 27, 14, 31, tzinfo=timezone.utc),
        "run-1",
    )


def _gex_row():
    return (
        "AAPL",
        datetime(2026, 6, 27, 14, 30, tzinfo=timezone.utc),
        "total",
        "total",
        None,
        None,
        None,
        149.5,
        1000.0,
        1200.0,
        -200.0,
        1000.0,
        100,
        50,
        10,
        "stockalert-schwab-gex-v1",
        "stockalert-schwab-gex",
        "run-1",
        datetime(2026, 6, 27, 14, 31, tzinfo=timezone.utc),
        "run-1",
    )


def test_get_latest_contracts_reads_clickhouse_rows() -> None:
    client = _Client(rows=[_contract_row()])
    reader = OptionsHotReader(client=client)

    response = reader.get_latest_contracts(
        "aapl",
        expiration_date=date(2026, 7, 17),
        put_call="call",
        limit=5,
    )

    assert response.underlying_symbol == "AAPL"
    assert response.count == 1
    assert response.contracts[0].option_symbol == "AAPL  260717C00150000"
    assert response.contracts[0].put_call == "CALL"
    assert client.calls[0][1]["symbol"] == "AAPL"
    assert client.calls[0][1]["expiration_date"] == date(2026, 7, 17)
    assert client.calls[0][1]["put_call"] == "CALL"
    assert client.calls[0][1]["limit"] == 5


def test_get_latest_gex_reads_clickhouse_rows() -> None:
    client = _Client(rows=[_gex_row()])
    reader = OptionsHotReader(client=client)

    response = reader.get_latest_gamma_exposure(
        "aapl",
        aggregation_level="total",
        limit=3,
    )

    assert response.underlying_symbol == "AAPL"
    assert response.aggregation_level == "total"
    assert response.count == 1
    assert response.rows[0].level_key == "total"
    assert client.calls[0][1]["aggregation_level"] == "total"
    assert client.calls[0][1]["limit"] == 3


def test_hot_reader_returns_empty_on_query_failure() -> None:
    reader = OptionsHotReader(client=_Client(raises=RuntimeError("ch down")))

    response = reader.get_latest_gamma_exposure("AAPL")

    assert response.count == 0
    assert response.rows == []


def test_hot_reader_blank_symbol_does_not_query() -> None:
    client = _Client(rows=[_contract_row()])
    reader = OptionsHotReader(client=client)

    response = reader.get_latest_contracts(" ")

    assert response.count == 0
    assert client.calls == []
