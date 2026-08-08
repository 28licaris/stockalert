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
from pathlib import Path
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


class StreamSummary(BaseModel):
    """Live Schwab subscription state — drives the cockpit's streaming tile."""

    started: bool = False
    provider: str = ""
    provider_ready: bool = False
    provider_error: Optional[str] = None
    streaming_count: int = 0
    universe_count: int = 0


class FreshnessRowModel(BaseModel):
    """One data source's freshness — 'is data still arriving?'"""

    key: str
    label: str
    group: str = Field(..., description="Market data | Options | Platform.")
    state: Literal["ok", "warn", "error", "idle", "unknown"] = Field(
        ...,
        description=(
            "ok=fresh, warn=late, error=badly late DURING its expected "
            "window, idle=stale but outside its window (normal), "
            "unknown=probe failed."
        ),
    )
    last_data_at: Optional[str] = Field(None, description="UTC ISO of newest data.")
    age_seconds: Optional[float] = None
    cadence_seconds: float = Field(..., description="How often data is expected.")
    expected_fresh: bool = Field(
        ..., description="Is this source expected to be producing data right now?"
    )
    detail: str = ""


class HealthFreshnessResponse(BaseModel):
    """Freshness of every monitored data source, worst-first."""

    server_time: str
    rows: list[FreshnessRowModel]
    worst_state: Literal["ok", "warn", "error", "idle", "unknown"]


class HealthServicesResponse(BaseModel):
    """Composite health snapshot for the cockpit Status page."""

    server_time: str = Field(
        ..., description="UTC ISO timestamp of when this snapshot was taken."
    )
    services: list[ServiceHealth]
    backfill: BackfillQueueSummary
    monitors: MonitorSummary
    stream: StreamSummary = Field(default_factory=StreamSummary)


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


SCHWAB_TOKEN_WARN_DAYS = 5.0
SCHWAB_TOKEN_ERROR_DAYS = 6.5


def _schwab_token_age_days() -> Optional[float]:
    """Age of the Schwab refresh token in days.

    Works for BOTH sources. File-supplied tokens age from the file's mtime
    (the OAuth script rewrites it on re-auth). Env-supplied tokens (the
    common case here — SCHWAB_REFRESH_TOKEN in .env) have no mtime, so we
    keep a tiny first-seen ledger keyed by the token's hash: a new token
    value stamps a new first-seen time, and age counts from there. The
    ledger stores only a hash, never the token.
    """
    import hashlib
    import json
    import os
    from datetime import datetime, timezone

    from app.config import settings

    now = datetime.now(timezone.utc)
    env_token = (settings.schwab_refresh_token or "").strip()
    if not env_token:
        path = settings.schwab_refresh_token_file
        if not path or not os.path.isfile(path):
            return None
        mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
        return (now - mtime).total_seconds() / 86400.0

    digest = hashlib.sha256(env_token.encode()).hexdigest()[:16]
    ledger = Path(settings.schwab_refresh_token_file or "data/.schwab_refresh_token").parent
    ledger = ledger / ".schwab_token_seen.json"
    try:
        state = json.loads(ledger.read_text()) if ledger.is_file() else {}
    except (OSError, ValueError):
        state = {}
    if state.get("hash") != digest:
        state = {"hash": digest, "first_seen": now.isoformat()}
        try:
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.write_text(json.dumps(state))
        except OSError as exc:  # non-fatal: age just stays unknown
            logger.warning("schwab token ledger unwritable (%s): %s", ledger, exc)
            return None
    try:
        first_seen = datetime.fromisoformat(state["first_seen"])
    except (KeyError, ValueError):
        return None
    return (now - first_seen).total_seconds() / 86400.0


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

    # Token AGE is the thing that actually breaks: Schwab refresh tokens
    # live ~7 days, and an expired one looks identical to a valid one from
    # here (present, non-empty). Two silent expiries (2026-07-03, 07-20)
    # froze the intraday tier for days and cost the hourly FOMC paper
    # strategy its first live meeting. Age it from the token file's mtime,
    # which the OAuth script rewrites on every re-auth.
    age_days = _schwab_token_age_days()
    if age_days is None:
        return ServiceHealth(
            name="Schwab",
            state="ok",
            detail="client + refresh token present (age unknown: token from env, not file)",
        )
    detail = f"refresh token {age_days:.1f}d old (~7d life)"
    if age_days >= SCHWAB_TOKEN_ERROR_DAYS:
        return ServiceHealth(
            name="Schwab", state="error",
            detail=f"{detail} — LIKELY EXPIRED, re-run scripts/schwab_get_refresh_token.py",
        )
    if age_days >= SCHWAB_TOKEN_WARN_DAYS:
        return ServiceHealth(
            name="Schwab", state="warn",
            detail=f"{detail} — re-auth due, run scripts/schwab_get_refresh_token.py",
        )
    return ServiceHealth(name="Schwab", state="ok", detail=detail)


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


async def _stream_summary() -> StreamSummary:
    try:
        from app.services.stream import stream_service

        s = await asyncio.to_thread(stream_service.status)
        return StreamSummary(
            started=bool(s.get("started", False)),
            provider=str(s.get("provider") or ""),
            provider_ready=bool(s.get("provider_ready", False)),
            provider_error=s.get("provider_error"),
            streaming_count=int(s.get("streaming_count", 0) or 0),
            universe_count=int(s.get("universe_count", 0) or 0),
        )
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.debug("stream summary probe failed: %s", exc)
        return StreamSummary()


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

    services, backfill, monitors, stream = await asyncio.gather(
        asyncio.gather(
            _check_clickhouse(),
            _check_iceberg(),
            _check_schwab(),
            _check_polygon(),
        ),
        _backfill_summary(),
        _monitor_summary(),
        _stream_summary(),
    )

    return HealthServicesResponse(
        server_time=datetime.now(timezone.utc).isoformat(),
        services=list(services),
        backfill=backfill,
        monitors=monitors,
        stream=stream,
    )


# Per-probe ceiling. A probe that hangs must never hold the response:
# on 2026-08-06 blocking lake work inside a coroutine froze every
# endpoint for 40+ minutes, so this surface is built to fail fast and
# degrade to "unknown" rather than wait.
#
# 12s, not 5s: the Iceberg probes need a Glue client on the FIRST call
# of a fresh process (~6-8s cold, ~1.5s warm), and a 5s ceiling made the
# lake rows read "probe timed out" on every restart — an unknown row
# where real data exists is its own kind of lie. Still bounded, still
# well under any sane page-load budget.
_FRESHNESS_PROBE_TIMEOUT_S = 12.0
_STATE_RANK = {"error": 0, "warn": 1, "unknown": 2, "idle": 3, "ok": 4}


@router.get(
    "/health/freshness",
    response_model=HealthFreshnessResponse,
    summary="Is data still arriving? (distinct from 'can I connect?')",
)
async def health_freshness() -> HealthFreshnessResponse:
    from datetime import datetime, timezone

    from app.services.health.freshness import SOURCES, FreshnessRow, classify

    async def _one(src) -> FreshnessRow:
        try:
            last = await asyncio.wait_for(
                asyncio.to_thread(src.probe), timeout=_FRESHNESS_PROBE_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            logger.warning("freshness probe %s timed out", src.key)
            return FreshnessRow(
                key=src.key, label=src.label, group=src.group, state="unknown",
                last_data_at=None, age_seconds=None,
                cadence_seconds=src.cadence_seconds, expected_fresh=False,
                detail=f"probe timed out after {_FRESHNESS_PROBE_TIMEOUT_S:.0f}s",
            )
        except Exception as exc:  # noqa: BLE001 — one bad probe ≠ an outage
            logger.warning("freshness probe %s failed: %s", src.key, exc)
            return FreshnessRow(
                key=src.key, label=src.label, group=src.group, state="unknown",
                last_data_at=None, age_seconds=None,
                cadence_seconds=src.cadence_seconds, expected_fresh=False,
                detail=f"probe failed: {type(exc).__name__}",
            )
        return classify(src, last)

    rows = await asyncio.gather(*(_one(s) for s in SOURCES))
    ordered = sorted(rows, key=lambda r: (_STATE_RANK.get(r.state, 9), r.label))
    worst = ordered[0].state if ordered else "ok"
    return HealthFreshnessResponse(
        server_time=datetime.now(timezone.utc).isoformat(),
        rows=[FreshnessRowModel(**vars(r)) for r in ordered],
        worst_state=worst,
    )
