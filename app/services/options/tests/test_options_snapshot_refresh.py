from __future__ import annotations

import asyncio

from app.services.ingest import options_snapshot_refresh as refresh
from app.services.options.schemas import OptionSnapshotIngestResult


def _enable_snapshot_settings(monkeypatch) -> None:
    monkeypatch.setattr(refresh.settings, "options_snapshot_enabled", True)
    monkeypatch.setattr(refresh.settings, "stock_lake_bucket", "lake")
    monkeypatch.setattr(refresh.settings, "schwab_client_id", "client")
    monkeypatch.setattr(refresh.settings, "schwab_client_secret", "secret")
    monkeypatch.setattr(
        type(refresh.settings),
        "get_schwab_refresh_token",
        lambda _settings: "token",
    )
    monkeypatch.setattr(refresh.settings, "options_snapshot_symbols", "AAPL,MSFT")
    monkeypatch.setattr(refresh.settings, "options_snapshot_strike_count", 7)
    monkeypatch.setattr(refresh.settings, "options_snapshot_contract_type", "CALL")


class _Service:
    def __init__(self) -> None:
        self.calls = []

    async def ingest_symbol(self, symbol: str, **kwargs):
        self.calls.append((symbol, kwargs))
        status = "error" if symbol == "MSFT" else "ok"
        return OptionSnapshotIngestResult(
            symbol=symbol,
            status=status,
            contracts_parsed=3,
            rows_written=10 if status == "ok" else 0,
            error="boom" if status == "error" else None,
        )


def test_refresh_options_snapshots_skips_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(refresh.settings, "options_snapshot_enabled", False)

    result = asyncio.run(refresh.refresh_options_snapshots(service=_Service()))

    assert result == {"skipped": True, "reason": "OPTIONS_SNAPSHOT_ENABLED=false"}


def test_refresh_options_snapshots_runs_symbols_and_aggregates(monkeypatch) -> None:
    _enable_snapshot_settings(monkeypatch)
    service = _Service()

    result = asyncio.run(
        refresh.refresh_options_snapshots(
            service=service,
            symbols_spec="aapl,msft",
            raise_on_error=False,
        )
    )

    assert result["status"] == "error"
    assert result["symbols"] == 2
    assert result["errors"] == 1
    assert result["rows_written"] == 10
    assert [call[0] for call in service.calls] == ["AAPL", "MSFT"]
    assert service.calls[0][1]["request_params"]["strikeCount"] == 7
    assert service.calls[0][1]["request_params"]["contractType"] == "CALL"
    assert service.calls[0][1]["dry_run"] is False
    assert service.calls[0][1]["ingestion_run_id"].startswith("options_snapshot:")


def test_refresh_options_snapshots_raises_when_any_symbol_errors(monkeypatch) -> None:
    _enable_snapshot_settings(monkeypatch)

    try:
        asyncio.run(
            refresh.refresh_options_snapshots(
                service=_Service(),
                symbols_spec="aapl,msft",
            )
        )
    except RuntimeError as exc:
        assert "MSFT" in str(exc)
    else:
        raise AssertionError("expected symbol error to fail the refresh")


def test_request_params_reject_invalid_contract_type(monkeypatch) -> None:
    _enable_snapshot_settings(monkeypatch)
    monkeypatch.setattr(refresh.settings, "options_snapshot_contract_type", "BAD")

    try:
        refresh._options_snapshot_request_params()
    except ValueError as exc:
        assert "OPTIONS_SNAPSHOT_CONTRACT_TYPE" in str(exc)
    else:
        raise AssertionError("expected invalid contract type to fail")


def test_interval_has_one_minute_floor(monkeypatch) -> None:
    monkeypatch.setattr(refresh.settings, "options_snapshot_interval_seconds", 5)

    assert refresh._options_snapshot_interval_seconds() == 60
