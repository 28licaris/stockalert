from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from app.services.ingest.sinks import SinkResult
from app.services.options.service import DEFAULT_CHAIN_PARAMS, OptionsSnapshotService


FIXTURE = Path(__file__).parent / "fixtures" / "schwab_chain_aapl.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text())


class _Provider:
    def __init__(self, payload=None, raises: Exception | None = None) -> None:
        self.payload = payload if payload is not None else _payload()
        self.raises = raises
        self.calls: list[tuple[str, dict]] = []

    async def get_option_chains(self, symbol: str, **kwargs):
        self.calls.append((symbol, kwargs))
        if self.raises:
            raise self.raises
        return self.payload


class _Sink:
    def __init__(self, result: SinkResult | None = None, raises: Exception | None = None) -> None:
        self.result = result or SinkResult(
            sink="options_iceberg",
            status="ok",
            bars_written=11,
            metadata={"rows": {"raw": 1, "contracts": 3, "expirations": 1, "gamma": 6}},
        )
        self.raises = raises
        self.calls = []

    async def write_parse_result(self, parsed, *, gamma_rows=None):
        self.calls.append((parsed, gamma_rows))
        if self.raises:
            raise self.raises
        return self.result


def test_ingest_symbol_fetches_parses_and_writes_snapshot() -> None:
    provider = _Provider()
    sink = _Sink()
    svc = OptionsSnapshotService(provider=provider, sink=sink)
    ts = datetime(2026, 6, 27, 14, 30, tzinfo=timezone.utc)

    result = asyncio.run(svc.ingest_symbol("aapl", snapshot_ts=ts, ingestion_run_id="run-1"))

    assert result.status == "ok"
    assert result.symbol == "AAPL"
    assert result.contracts_parsed == 3
    assert result.expirations_parsed == 1
    assert result.gamma_rows == 6
    assert result.rows_written == 11
    assert result.sink_status == "ok"
    assert provider.calls == [("AAPL", DEFAULT_CHAIN_PARAMS)]
    parsed, gamma_rows = sink.calls[0]
    assert parsed.raw_snapshot.request_params["symbol"] == "AAPL"
    assert parsed.raw_snapshot.ingestion_run_id == "run-1"
    assert len(gamma_rows) == 6


def test_ingest_symbol_merges_request_params_over_defaults() -> None:
    provider = _Provider()
    svc = OptionsSnapshotService(provider=provider, sink=_Sink())

    asyncio.run(
        svc.ingest_symbol(
            "msft",
            request_params={"strikeCount": 5, "fromDate": "2026-07-01"},
        )
    )

    _, params = provider.calls[0]
    assert params["contractType"] == "ALL"
    assert params["strikeCount"] == 5
    assert params["includeUnderlyingQuote"] is True
    assert params["fromDate"] == "2026-07-01"


def test_ingest_symbol_empty_chain_returns_skipped_but_writes_raw() -> None:
    provider = _Provider(
        payload={"symbol": "MSFT", "status": "SUCCESS", "callExpDateMap": {}, "putExpDateMap": {}}
    )
    sink = _Sink(
        result=SinkResult(
            sink="options_iceberg",
            status="ok",
            bars_written=1,
            metadata={"rows": {"raw": 1, "contracts": 0, "expirations": 0, "gamma": 0}},
        )
    )
    svc = OptionsSnapshotService(provider=provider, sink=sink)

    result = asyncio.run(svc.ingest_symbol("MSFT"))

    assert result.status == "skipped"
    assert result.contracts_parsed == 0
    assert result.rows_written == 1
    assert result.metadata["chain_status"] == "SUCCESS"


def test_ingest_symbol_dry_run_does_not_write_sink() -> None:
    sink = _Sink()
    svc = OptionsSnapshotService(provider=_Provider(), sink=sink)

    result = asyncio.run(svc.ingest_symbol("AAPL", dry_run=True))

    assert result.status == "ok"
    assert result.sink_status == "dry_run"
    assert result.rows_written == 0
    assert result.metadata["dry_run"] is True
    assert sink.calls == []


def test_ingest_symbol_provider_error_returns_result() -> None:
    svc = OptionsSnapshotService(provider=_Provider(raises=RuntimeError("schwab down")), sink=_Sink())

    result = asyncio.run(svc.ingest_symbol("AAPL"))

    assert result.status == "error"
    assert result.error == "schwab down"
    assert result.metadata["stage"] == "provider_fetch"


def test_ingest_symbol_parse_error_returns_result_without_sink_call() -> None:
    sink = _Sink()
    svc = OptionsSnapshotService(provider=_Provider(payload=[]), sink=sink)

    result = asyncio.run(svc.ingest_symbol("AAPL"))

    assert result.status == "error"
    assert result.metadata["stage"] == "parse"
    assert sink.calls == []


def test_ingest_symbol_sink_error_status_returns_result() -> None:
    sink = _Sink(result=SinkResult(sink="options_iceberg", status="error", error="write failed"))
    svc = OptionsSnapshotService(provider=_Provider(), sink=sink)

    result = asyncio.run(svc.ingest_symbol("AAPL"))

    assert result.status == "error"
    assert result.sink_status == "error"
    assert result.error == "write failed"


def test_ingest_symbol_blank_symbol_is_error_without_provider_call() -> None:
    provider = _Provider()
    svc = OptionsSnapshotService(provider=provider, sink=_Sink())

    result = asyncio.run(svc.ingest_symbol(" "))

    assert result.status == "error"
    assert result.error == "symbol is required"
    assert provider.calls == []
