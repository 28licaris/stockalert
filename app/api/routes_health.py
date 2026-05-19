"""
Composite health endpoint for the cockpit Status page.

Aggregates the existing per-subsystem checks (ClickHouse, Iceberg,
Schwab credentials, Polygon credentials, backfill queue, monitor
service) into one round-trip so the Status page renders without
fan-out from the browser.

This endpoint is **read-only and best-effort**: any individual
subsystem check failing produces a `state: "error"` entry rather than
a 5xx. The page should always be reachable; failures are visible
through the state field instead.

Shape is stable; new fields are additive. See
[docs/frontend_plan.md §5.1](../../docs/frontend_plan.md) for
expected fields.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


HealthState = Literal["ok", "warn", "error", "unknown"]


class ServiceHealth(BaseModel):
    name: str = Field(..., description="Human-friendly subsystem name.")
    state: HealthState = Field(
        ...,
        description=(
            "Traffic-light state: ok=green, warn=yellow, error=red, "
            "unknown=gray (not configured / not checked)."
        ),
    )
    detail: str = Field("", description="Short message shown on hover.")
    latency_ms: Optional[float] = Field(
        None,
        description="Round-trip time for the probe in ms (when applicable).",
    )


class BackfillQueueSummary(BaseModel):
    queued: int = 0
    in_flight: int = 0
    completed_recent: int = 0


class MonitorSummary(BaseModel):
    started: int = 0
    errors: int = 0


class HealthServicesResponse(BaseModel):
    """Composite health snapshot for the cockpit Status page."""

    server_time: str = Field(
        ..., description="UTC ISO timestamp of when this snapshot was taken."
    )
    services: list[ServiceHealth]
    backfill: BackfillQueueSummary
    monitors: MonitorSummary


# ─────────────────────────────────────────────────────────────────────
# Per-subsystem probes — each isolated; failures become "error" rows.
# ─────────────────────────────────────────────────────────────────────


async def _check_clickhouse() -> ServiceHealth:
    from app.db import ping

    t0 = time.perf_counter()
    try:
        ok = await asyncio.to_thread(ping)
        ms = (time.perf_counter() - t0) * 1000
        if ok:
            return ServiceHealth(
                name="ClickHouse", state="ok", detail="ping ok", latency_ms=ms
            )
        return ServiceHealth(
            name="ClickHouse",
            state="error",
            detail="ping returned false",
            latency_ms=ms,
        )
    except Exception as exc:  # noqa: BLE001 — boundary; surface as error state
        return ServiceHealth(
            name="ClickHouse", state="error", detail=str(exc)[:200]
        )


async def _check_iceberg() -> ServiceHealth:
    """
    Iceberg health is 'can we obtain a catalog handle?' — does NOT
    list tables (S3/Glue round-trip is too slow for a status probe).
    """

    def _probe() -> ServiceHealth:
        try:
            from app.services.iceberg_catalog import get_catalog

            t0 = time.perf_counter()
            get_catalog()
            ms = (time.perf_counter() - t0) * 1000
            return ServiceHealth(
                name="Iceberg",
                state="ok",
                detail="catalog handle",
                latency_ms=ms,
            )
        except Exception as exc:  # noqa: BLE001 — boundary; surface as error state
            msg = str(exc)
            # Distinguish "not configured" (unknown / gray) from "broken" (error / red)
            if "credentials" in msg.lower() or "no such bucket" in msg.lower():
                return ServiceHealth(
                    name="Iceberg",
                    state="unknown",
                    detail="not configured: " + msg[:200],
                )
            return ServiceHealth(
                name="Iceberg", state="error", detail=msg[:200]
            )

    return await asyncio.to_thread(_probe)


async def _check_schwab() -> ServiceHealth:
    """Schwab is healthy if credentials are configured (we don't burn an OAuth round-trip on every status poll)."""
    from app.config import settings

    cid = (settings.schwab_client_id or "").strip()
    csec = (settings.schwab_client_secret or "").strip()
    if not cid or not csec:
        return ServiceHealth(
            name="Schwab", state="unknown", detail="credentials not configured"
        )
    refresh = settings.get_schwab_refresh_token() if hasattr(settings, "get_schwab_refresh_token") else None
    if not refresh:
        return ServiceHealth(
            name="Schwab",
            state="warn",
            detail="client configured; refresh token missing",
        )
    return ServiceHealth(
        name="Schwab", state="ok", detail="client + refresh token present"
    )


async def _check_polygon() -> ServiceHealth:
    from app.config import settings

    key = (getattr(settings, "polygon_api_key", "") or "").strip()
    if not key:
        return ServiceHealth(
            name="Polygon", state="unknown", detail="api key not configured"
        )
    return ServiceHealth(
        name="Polygon", state="ok", detail="api key present"
    )


async def _backfill_summary() -> BackfillQueueSummary:
    try:
        from app.services.ingest.backfill_service import backfill_service

        raw = await asyncio.to_thread(backfill_service.status)
        # backfill_service.status() returns a structured dict; we shape a
        # subset for the cockpit. Unknown fields stay safe defaults.
        return BackfillQueueSummary(
            queued=int(raw.get("queued", 0) or 0),
            in_flight=int(raw.get("in_flight", 0) or 0),
            completed_recent=int(raw.get("completed_recent", 0) or 0),
        )
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.debug("backfill summary probe failed: %s", exc)
        return BackfillQueueSummary()


async def _monitor_summary() -> MonitorSummary:
    try:
        from app.services.live.monitor_manager import monitor_manager

        # monitor_manager exposes `started` / `error_count` as readable attrs
        # or via a list; whichever exists, fall back to zero.
        started = 0
        errors = 0
        if hasattr(monitor_manager, "started_count"):
            started = int(monitor_manager.started_count())
        elif hasattr(monitor_manager, "list_started"):
            started = len(monitor_manager.list_started())
        if hasattr(monitor_manager, "error_count"):
            errors = int(monitor_manager.error_count())
        return MonitorSummary(started=started, errors=errors)
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.debug("monitor summary probe failed: %s", exc)
        return MonitorSummary()


@router.get(
    "/health/services",
    response_model=HealthServicesResponse,
    summary="Composite subsystem health for the cockpit Status page.",
)
async def health_services() -> HealthServicesResponse:
    from datetime import datetime, timezone

    services, backfill, monitors = await asyncio.gather(
        asyncio.gather(
            _check_clickhouse(),
            _check_iceberg(),
            _check_schwab(),
            _check_polygon(),
        ),
        _backfill_summary(),
        _monitor_summary(),
    )

    return HealthServicesResponse(
        server_time=datetime.now(timezone.utc).isoformat(),
        services=list(services),
        backfill=backfill,
        monitors=monitors,
    )
