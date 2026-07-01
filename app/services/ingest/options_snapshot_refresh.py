"""Scheduled Schwab option-chain snapshot refresh."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.services.options.schemas import OptionSnapshotIngestResult
from app.services.options.service import DEFAULT_CHAIN_PARAMS, OptionsSnapshotService
from app.services.options.universe import resolve_options_symbol_spec

logger = logging.getLogger(__name__)

OPTIONS_SNAPSHOT_DEFAULT_INTERVAL_SECONDS = 300


def _options_snapshot_interval_seconds() -> int:
    raw = int(
        getattr(
            settings,
            "options_snapshot_interval_seconds",
            OPTIONS_SNAPSHOT_DEFAULT_INTERVAL_SECONDS,
        )
    )
    return max(60, raw)


def _options_snapshot_request_params() -> dict[str, Any]:
    params = dict(DEFAULT_CHAIN_PARAMS)
    params["strikeCount"] = int(getattr(settings, "options_snapshot_strike_count", 20))
    contract_type = str(
        getattr(settings, "options_snapshot_contract_type", "ALL")
    ).upper()
    if contract_type not in {"CALL", "PUT", "ALL"}:
        raise ValueError(
            "OPTIONS_SNAPSHOT_CONTRACT_TYPE must be CALL, PUT, or ALL; "
            f"got {contract_type!r}"
        )
    params["contractType"] = contract_type
    return params


def _options_snapshot_gated() -> tuple[bool, str]:
    if not getattr(settings, "options_snapshot_enabled", False):
        return True, "OPTIONS_SNAPSHOT_ENABLED=false"
    if not (settings.stock_lake_bucket or "").strip():
        return True, "STOCK_LAKE_BUCKET is empty"
    if not (settings.schwab_client_id or "").strip():
        return True, "SCHWAB_CLIENT_ID missing"
    if not (settings.schwab_client_secret or "").strip():
        return True, "SCHWAB_CLIENT_SECRET missing"
    if not settings.get_schwab_refresh_token():
        return True, "Schwab refresh token missing"
    return False, ""


async def refresh_options_snapshots(
    *,
    service: OptionsSnapshotService | None = None,
    symbols_spec: str | None = None,
    dry_run: bool = False,
    raise_on_error: bool = True,
) -> dict[str, Any]:
    gated, why = _options_snapshot_gated()
    if gated:
        logger.info("options_snapshot_refresh: skipping — %s", why)
        return {"skipped": True, "reason": why}

    spec = symbols_spec or getattr(settings, "options_snapshot_symbols", "active")
    symbols = resolve_options_symbol_spec(spec)
    request_params = _options_snapshot_request_params()
    service = service or OptionsSnapshotService.from_settings(dry_run=dry_run)
    snapshot_ts = datetime.now(timezone.utc)
    ingestion_run_id = f"options_snapshot:{snapshot_ts.isoformat()}"

    logger.info(
        "options_snapshot_refresh: starting symbols=%d dry_run=%s params=%s",
        len(symbols),
        dry_run,
        request_params,
    )

    results: list[OptionSnapshotIngestResult] = []
    for symbol in symbols:
        result = await service.ingest_symbol(
            symbol,
            snapshot_ts=snapshot_ts,
            request_params=request_params,
            ingestion_run_id=ingestion_run_id,
            dry_run=dry_run,
        )
        results.append(result)
        logger.info(
            "options_snapshot_refresh: symbol_complete=%s status=%s contracts=%d",
            result.symbol,
            result.status,
            result.contracts_parsed,
        )

    errors = [result for result in results if result.status == "error"]
    skipped = [result for result in results if result.status == "skipped"]
    rows_written = sum(result.rows_written for result in results)
    status = "error" if errors else "ok"
    logger.info(
        "options_snapshot_refresh: complete status=%s symbols=%d errors=%d rows_written=%d",
        status,
        len(results),
        len(errors),
        rows_written,
    )
    summary = {
        "status": status,
        "symbols": len(results),
        "errors": len(errors),
        "skipped": len(skipped),
        "rows_written": rows_written,
        "ingestion_run_id": ingestion_run_id,
        "results": [
            {
                "symbol": result.symbol,
                "status": result.status,
                "contracts": result.contracts_parsed,
                "rows_written": result.rows_written,
                "error": result.error,
            }
            for result in results
        ],
    }
    if errors and raise_on_error:
        failed = ", ".join(result.symbol for result in errors[:10])
        raise RuntimeError(
            "options_snapshot_refresh failed for "
            f"{len(errors)} symbol(s): {failed}"
        )
    return summary


async def run_options_snapshot_loop() -> None:
    gated, why = _options_snapshot_gated()
    if gated:
        logger.info("options_snapshot_refresh: loop not started — %s", why)
        return

    interval = _options_snapshot_interval_seconds()
    logger.info(
        "options_snapshot_refresh: loop armed (every %ds; symbols=%s)",
        interval,
        getattr(settings, "options_snapshot_symbols", "active"),
    )

    while True:
        try:
            await asyncio.sleep(interval)
            from app.services.jobs.service import audit_run
            async with audit_run("options_snapshot_refresh", frequent=True) as rec:
                rec.result = await refresh_options_snapshots()
        except asyncio.CancelledError:
            logger.info("options_snapshot_refresh: loop cancelled")
            raise
        except Exception as e:
            logger.exception("options_snapshot_refresh: unexpected error: %s", e)
            await asyncio.sleep(60)
