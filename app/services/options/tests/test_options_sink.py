from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pyarrow as pa

from app.services.options.parser import parse_schwab_option_chain
from app.services.options.sink import OptionsIcebergSink, _OptionsTables


FIXTURE = Path(__file__).parent / "fixtures" / "schwab_chain_aapl.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text())


def _table(name: str) -> MagicMock:
    table = MagicMock()
    table.name.return_value = name
    table.upsert.side_effect = lambda arrow: MagicMock(
        rows_updated=0,
        rows_inserted=arrow.num_rows,
    )
    snap = MagicMock()
    snap.snapshot_id = 123
    table.current_snapshot.return_value = snap
    return table


def _sink() -> tuple[OptionsIcebergSink, dict[str, MagicMock]]:
    tables = {
        "raw": _table("options.schwab_chain_raw"),
        "contracts": _table("options.schwab_chain_contracts"),
        "expirations": _table("options.schwab_expirations"),
        "gamma": _table("options.gamma_exposure_snapshots"),
    }
    return OptionsIcebergSink(tables=_OptionsTables(**tables)), tables


def _parse():
    return parse_schwab_option_chain(
        _payload(),
        snapshot_ts=datetime(2026, 6, 27, 14, 30, tzinfo=timezone.utc),
        ingestion_run_id="run-1",
    )


def test_options_sink_writes_raw_contract_expiration_and_gamma_tables() -> None:
    sink, tables = _sink()

    result = asyncio.run(sink.write_parse_result(_parse()))

    assert result.status == "ok"
    assert result.bars_written == 11
    assert result.metadata["rows"] == {
        "raw": 1,
        "contracts": 3,
        "expirations": 1,
        "gamma": 6,
    }

    raw_arrow = tables["raw"].upsert.call_args.args[0]
    contract_arrow = tables["contracts"].upsert.call_args.args[0]
    gamma_arrow = tables["gamma"].upsert.call_args.args[0]
    assert isinstance(raw_arrow, pa.Table)
    assert raw_arrow.column("request_params").to_pylist() == ["{}"]
    assert raw_arrow.column("raw_payload").to_pylist()[0].startswith('{"callExpDateMap"')
    assert contract_arrow.num_rows == 3
    assert gamma_arrow.column("aggregation_level").to_pylist().count("total") == 1
    assert "total" in gamma_arrow.column("level_key").to_pylist()


def test_options_sink_skips_none_parse_result() -> None:
    sink, tables = _sink()

    result = asyncio.run(sink.write_parse_result(None))

    assert result.status == "skipped"
    assert result.metadata["reason"] == "empty_parse_result"
    assert all(not table.upsert.called for table in tables.values())


def test_options_sink_writes_raw_only_for_empty_chain() -> None:
    sink, tables = _sink()
    parsed = parse_schwab_option_chain(
        {"symbol": "MSFT", "status": "SUCCESS", "callExpDateMap": {}, "putExpDateMap": {}},
        snapshot_ts=datetime(2026, 6, 27, 14, 30, tzinfo=timezone.utc),
        ingestion_run_id="run-empty",
    )

    result = asyncio.run(sink.write_parse_result(parsed))

    assert result.status == "ok"
    assert result.bars_written == 1
    assert result.metadata["rows"] == {
        "raw": 1,
        "contracts": 0,
        "expirations": 0,
        "gamma": 0,
    }
    tables["raw"].upsert.assert_called_once()
    tables["contracts"].upsert.assert_not_called()
    tables["expirations"].upsert.assert_not_called()
    tables["gamma"].upsert.assert_not_called()


def test_options_sink_returns_error_when_append_fails() -> None:
    sink, tables = _sink()
    tables["contracts"].upsert.side_effect = RuntimeError("upsert exploded")

    result = asyncio.run(sink.write_parse_result(_parse()))

    assert result.status == "error"
    assert "upsert exploded" in (result.error or "")
    assert result.metadata["rows_prepared"] == {"raw": 1}


def test_write_does_not_block_the_event_loop() -> None:
    """Regression (2026-08-06): the Iceberg upserts are synchronous S3+Glue
    round-trips. Called inline in this coroutine they stalled the event
    loop for the whole 40-symbol snapshot sweep, so any HTTP request that
    landed during a sweep hung — the GEX page rendered completely empty.
    A concurrent task must keep running while the sink writes."""
    import asyncio
    import time

    sink, tables = _sink()

    def _slow_upsert(*_a, **_kw):
        time.sleep(0.20)  # stands in for the blocking S3/Glue call
        return MagicMock(rows_upserted=1, rows_inserted=1, rows_updated=0)

    for t in tables.values():
        t.upsert.side_effect = _slow_upsert

    async def _scenario() -> int:
        ticks = 0

        async def _heartbeat() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        beat = asyncio.create_task(_heartbeat())
        result = await sink.write_parse_result(_parse())
        beat.cancel()
        assert result.status == "ok"
        return ticks

    ticks = asyncio.run(_scenario())
    # 4 tables x 0.20s of blocking work: if it ran on the loop the
    # heartbeat would be starved (~0 ticks).
    assert ticks > 5, f"event loop was blocked during the write (ticks={ticks})"
