from __future__ import annotations

import argparse
import asyncio

from app.services.options.schemas import OptionSnapshotIngestResult
from scripts.options_chain_snapshot import (
    build_request_params,
    parse_symbols,
    result_line,
    resolve_symbols,
    run_snapshot,
)


def test_parse_symbols_normalizes_dedupes_and_sorts() -> None:
    assert parse_symbols(" msft,AAPL,msft ") == ["AAPL", "MSFT"]


def test_resolve_symbols_accepts_explicit_csv() -> None:
    assert resolve_symbols(" msft,AAPL,msft ") == ["AAPL", "MSFT"]


def test_resolve_symbols_active_uses_injected_resolver() -> None:
    symbols = resolve_symbols(
        "active",
        active_resolver=lambda: ["msft", "AAPL", "MSFT"],
    )

    assert symbols == ["AAPL", "MSFT"]


def test_resolve_symbols_rejects_empty_active_universe() -> None:
    try:
        resolve_symbols("active", active_resolver=lambda: [])
    except ValueError as exc:
        assert "active universe returned no symbols" in str(exc)
    else:
        raise AssertionError("expected empty active universe to fail")


def test_resolve_symbols_watchlist_uses_injected_resolver() -> None:
    calls = []

    def resolver(name: str) -> list[str]:
        calls.append(name)
        return ["tsla", "AAPL", "TSLA"]

    symbols = resolve_symbols("watchlist:momentum", watchlist_resolver=resolver)

    assert symbols == ["AAPL", "TSLA"]
    assert calls == ["momentum"]


def test_resolve_symbols_rejects_unsupported_all() -> None:
    try:
        resolve_symbols("all")
    except ValueError as exc:
        assert "not supported" in str(exc)
    else:
        raise AssertionError("expected all to fail")


def test_build_request_params_applies_cli_overrides() -> None:
    args = argparse.Namespace(
        strike_count=5,
        from_date="2026-07-01",
        to_date="2026-08-01",
        contract_type="CALL",
    )

    params = build_request_params(args)

    assert params["strikeCount"] == 5
    assert params["contractType"] == "CALL"
    assert params["fromDate"] == "2026-07-01"
    assert params["toDate"] == "2026-08-01"
    assert params["includeUnderlyingQuote"] is True


def test_result_line_includes_error_when_present() -> None:
    line = result_line(
        OptionSnapshotIngestResult(
            symbol="AAPL",
            status="error",
            error="failed",
            sink_status="error",
        )
    )

    assert "AAPL: status=error" in line
    assert "sink=error" in line
    assert "error=failed" in line


class _Service:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def ingest_symbol(self, symbol, **kwargs):
        self.calls.append((symbol, kwargs))
        return self.results.pop(0)


def test_run_snapshot_returns_nonzero_when_any_symbol_errors() -> None:
    service = _Service(
        [
            OptionSnapshotIngestResult(symbol="AAPL", status="ok", contracts_parsed=1),
            OptionSnapshotIngestResult(symbol="MSFT", status="error", error="boom"),
        ]
    )

    code = asyncio.run(
        run_snapshot(
            symbols=["AAPL", "MSFT"],
            request_params={"strikeCount": 1},
            dry_run=True,
            service=service,
        )
    )

    assert code == 1
    assert service.calls[0][0] == "AAPL"
    assert service.calls[0][1]["dry_run"] is True
    assert service.calls[0][1]["request_params"] == {"strikeCount": 1}
