"""Schwab option-chain snapshot orchestration."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from app.services.options.parser import (
    aggregate_gamma_exposure,
    parse_schwab_option_chain,
)
from app.services.options.schemas import OptionSnapshotIngestResult

log = logging.getLogger(__name__)

DEFAULT_CHAIN_PARAMS: dict[str, Any] = {
    "contractType": "ALL",
    "strikeCount": 20,
    "includeUnderlyingQuote": True,
    "strategy": "SINGLE",
}


class OptionsSnapshotService:
    """Fetch, parse, and write Schwab option-chain snapshots."""

    def __init__(
        self,
        *,
        provider: Any,
        sink: Any,
        parser: Callable[..., Any] = parse_schwab_option_chain,
    ) -> None:
        if provider is None:
            raise ValueError("OptionsSnapshotService: provider is required")
        if sink is None:
            raise ValueError("OptionsSnapshotService: sink is required")
        self._provider = provider
        self._sink = sink
        self._parser = parser

    @classmethod
    def from_settings(cls) -> "OptionsSnapshotService":
        from app.config import get_provider
        from app.services.options.sink import OptionsIcebergSink

        return cls(provider=get_provider("schwab"), sink=OptionsIcebergSink.from_settings())

    async def ingest_symbol(
        self,
        symbol: str,
        *,
        snapshot_ts: datetime | None = None,
        request_params: dict[str, Any] | None = None,
        ingestion_run_id: str | None = None,
    ) -> OptionSnapshotIngestResult:
        sym = (symbol or "").strip().upper()
        if not sym:
            return OptionSnapshotIngestResult(
                symbol="",
                status="error",
                error="symbol is required",
            )

        params = {**DEFAULT_CHAIN_PARAMS, **(request_params or {})}
        snapshot_ts = _utc(snapshot_ts or datetime.now(timezone.utc))

        try:
            payload = await self._provider.get_option_chains(sym, **params)
        except Exception as e:
            log.exception("options_snapshot: provider fetch failed symbol=%s", sym)
            return OptionSnapshotIngestResult(
                symbol=sym,
                status="error",
                error=str(e),
                snapshot_ts=snapshot_ts,
                metadata={"stage": "provider_fetch", "request_params": params},
            )

        try:
            parsed = self._parser(
                payload,
                snapshot_ts=snapshot_ts,
                request_params={"symbol": sym, **params},
                ingestion_run_id=ingestion_run_id,
            )
            gamma_rows = aggregate_gamma_exposure(
                parsed.contracts,
                ingestion_run_id=ingestion_run_id or parsed.raw_snapshot.ingestion_run_id,
            )
        except Exception as e:
            log.exception("options_snapshot: parse failed symbol=%s", sym)
            return OptionSnapshotIngestResult(
                symbol=sym,
                status="error",
                error=str(e),
                snapshot_ts=snapshot_ts,
                metadata={"stage": "parse", "request_params": params},
            )

        if parsed.contract_count == 0:
            log.info(
                "options_snapshot: zero contracts symbol=%s status=%s",
                sym,
                parsed.raw_snapshot.status,
            )

        try:
            sink_result = await self._sink.write_parse_result(parsed, gamma_rows=gamma_rows)
        except Exception as e:
            log.exception("options_snapshot: sink raised symbol=%s", sym)
            return OptionSnapshotIngestResult(
                symbol=sym,
                status="error",
                contracts_parsed=parsed.contract_count,
                expirations_parsed=len(parsed.expirations),
                gamma_rows=len(gamma_rows),
                error=str(e),
                snapshot_ts=snapshot_ts,
                metadata={"stage": "sink"},
            )

        if sink_result.status == "error":
            status = "error"
        elif parsed.contract_count == 0:
            status = "skipped"
        else:
            status = "ok"

        return OptionSnapshotIngestResult(
            symbol=sym,
            status=status,
            contracts_parsed=parsed.contract_count,
            expirations_parsed=len(parsed.expirations),
            gamma_rows=len(gamma_rows),
            rows_written=sink_result.bars_written,
            sink_status=sink_result.status,
            error=sink_result.error,
            snapshot_ts=snapshot_ts,
            metadata={
                "request_params": params,
                "sink": sink_result.metadata,
                "chain_status": parsed.raw_snapshot.status,
            },
        )


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
