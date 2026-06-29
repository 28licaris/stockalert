from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from app.services.options.hot_sink import (
    CONTRACT_COLUMNS,
    CONTRACTS_TABLE,
    GEX_COLUMNS,
    GEX_TABLE,
    OptionsClickHouseSink,
)
from app.services.options.parser import aggregate_gamma_exposure, parse_schwab_option_chain


FIXTURE = Path(__file__).parent / "fixtures" / "schwab_chain_aapl.json"


class _Client:
    def __init__(self, raises: Exception | None = None) -> None:
        self.raises = raises
        self.inserts = []

    def insert(self, table, rows, column_names):
        if self.raises:
            raise self.raises
        self.inserts.append((table, rows, column_names))


def _parse():
    payload = json.loads(FIXTURE.read_text())
    return parse_schwab_option_chain(
        payload,
        snapshot_ts=datetime(2026, 6, 27, 14, 30, tzinfo=timezone.utc),
        ingestion_run_id="run-hot",
    )


def test_hot_sink_writes_contracts_and_gex_rows() -> None:
    client = _Client()
    sink = OptionsClickHouseSink(client=client)
    parsed = _parse()
    gamma_rows = aggregate_gamma_exposure(parsed.contracts, ingestion_run_id="run-hot")

    result = asyncio.run(sink.write_parse_result(parsed, gamma_rows=gamma_rows))

    assert result.status == "ok"
    assert result.bars_written == 9
    assert result.metadata["rows"] == {"contracts": 3, "gex": 6}
    assert client.inserts[0][0] == CONTRACTS_TABLE
    assert client.inserts[0][2] == CONTRACT_COLUMNS
    assert client.inserts[1][0] == GEX_TABLE
    assert client.inserts[1][2] == GEX_COLUMNS
    assert client.inserts[0][1][0][0] == "AAPL"
    assert client.inserts[1][1][0][0] == "AAPL"


def test_hot_sink_skips_empty_rows() -> None:
    client = _Client()
    sink = OptionsClickHouseSink(client=client)

    result = asyncio.run(sink.write_parse_result(None))

    assert result.status == "skipped"
    assert client.inserts == []


def test_hot_sink_returns_error_when_insert_fails() -> None:
    sink = OptionsClickHouseSink(client=_Client(raises=RuntimeError("ch down")))

    result = asyncio.run(sink.write_parse_result(_parse()))

    assert result.status == "error"
    assert "ch down" in (result.error or "")
    assert result.metadata["rows_prepared"]["contracts"] == 3
