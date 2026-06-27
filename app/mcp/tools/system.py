"""
MCP tools — system observability for agent self-diagnosis.

Lets an agent ask "is this platform actually working right now?"
before running a job, and surface diagnostics when its calls fail.
Critical for autonomous agent loops — if bronze is stale or CH is
down, the agent should know that without having to interpret a
500 from a tool call.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.mcp.middleware import tool_call
from app.mcp.server import mcp
from app.services.readers.schemas import (
    LakeFreshnessReport,
    ServiceStatus,
    SystemHealthReport,
)

logger = logging.getLogger(__name__)


async def _ping_clickhouse() -> tuple[bool, str | None]:
    """Quick CH liveness check. Returns (ok, error_detail)."""
    try:
        from app.db.client import ping as _ping
        ok = await asyncio.to_thread(_ping)
        return (bool(ok), None if ok else "ping returned False")
    except Exception as exc:  # noqa: BLE001
        return (False, f"{type(exc).__name__}: {exc}")


async def _ping_iceberg_catalog() -> tuple[bool, str | None]:
    """Iceberg catalog liveness — does `list_namespaces` succeed."""
    def _check() -> tuple[bool, str | None]:
        try:
            from app.services.iceberg_catalog import get_catalog
            get_catalog().list_namespaces()
            return (True, None)
        except Exception as exc:  # noqa: BLE001
            return (False, f"{type(exc).__name__}: {exc}")
    return await asyncio.to_thread(_check)


@mcp.tool()
async def get_health() -> SystemHealthReport:
    """Aggregate platform health snapshot.

    USE WHEN: an agent is checking whether it's safe to run a job —
    "is CH up?", "is the lake reachable?", "what's degraded right
    now?" Cheap to call; intended as a pre-flight check.

    Returns:
        SystemHealthReport with:
          - `status`: 'ok' (all green), 'degraded' (some failures),
            'down' (CH AND Iceberg both unreachable).
          - `clickhouse`: bool, live tier reachable.
          - `iceberg_catalog`: bool, cold tier reachable.
          - `services`: list of per-subsystem ServiceStatus rows.
          - `as_of`: timestamp of the check.

    Cost: under 100ms typical. Two parallel ping calls.
    """
    with tool_call("get_health"):
        ch_ok, ch_err = await _ping_clickhouse()
        ice_ok, ice_err = await _ping_iceberg_catalog()

        services: list[ServiceStatus] = [
            ServiceStatus(
                name="clickhouse",
                healthy=ch_ok,
                detail=ch_err if not ch_ok else None,
            ),
            ServiceStatus(
                name="iceberg_catalog",
                healthy=ice_ok,
                detail=ice_err if not ice_ok else None,
            ),
        ]

        if ch_ok and ice_ok:
            status = "ok"
        elif not ch_ok and not ice_ok:
            status = "down"
        else:
            status = "degraded"

        return SystemHealthReport(
            status=status,
            clickhouse=ch_ok,
            iceberg_catalog=ice_ok,
            services=services,
            as_of=datetime.now(timezone.utc),
        )


@mcp.tool()
def get_lake_freshness() -> LakeFreshnessReport:
    """Latest trading day in each lake table.

    USE WHEN: an agent (or an operator / cockpit tile) needs to verify
    "is the lake caught up?" before running a training job or trusting a
    chart's deep history. Reports the per-provider raw tables AND the
    adjusted + futures tiers — critically `polygon_adjusted`, built by
    the WEEKLY external Spark job, whose staleness no in-process
    auto-catchup can heal (so this is the only place it shows up).

    Returns:
        LakeFreshnessReport with:
          - `tables`: dict[short_name -> date | null] for each
            configured provider table (polygon_minute, schwab_minute).
            Null = empty table or unreachable.
          - `as_of`: when the check ran (UTC).

    Cost: under 100ms typical. Metadata-only — no data scan.
    """
    with tool_call("get_lake_freshness"):
        from app.services.readers.bronze_reader import BronzeReader

        reader = BronzeReader.from_settings()
        results: dict = {}
        for provider in ("polygon", "schwab"):
            try:
                latest = reader.latest_trading_day(provider=provider)
            except Exception as exc:  # noqa: BLE001
                logger.warning("get_lake_freshness(%s) failed: %s", provider, exc)
                latest = None
            results[f"{provider}_minute"] = latest

        # Surface the tiers the per-provider raw check misses — above all
        # `polygon_adjusted`, built by the WEEKLY *external* Spark job
        # (no in-process auto-catchup), so a stalled adjustment is
        # otherwise invisible until a chart looks wrong. Plus the futures
        # lake tables. Each degrades to None on its own; never raises.
        results.update(_iceberg_table_freshness())

        return LakeFreshnessReport(
            tables=results,
            as_of=datetime.now(timezone.utc),
        )


def _iceberg_table_freshness() -> dict:
    """Latest ET trading day for lake tables the per-provider raw check
    doesn't cover. Generous lookback so a genuinely stale tier surfaces
    its real (old) date instead of masquerading as empty (None)."""
    from app.services.equities.gaps import latest_loaded_date
    from app.services.equities.schemas import equities_table_id
    from app.services.futures.schemas import futures_table_id
    from app.services.iceberg_catalog import get_catalog

    specs = {
        # The external weekly Spark tier — the key freshness blind spot.
        "polygon_adjusted": equities_table_id("polygon_adjusted"),
        "futures_schwab": futures_table_id("schwab_futures"),
        "futures_continuous": futures_table_id("polygon_continuous"),
    }
    try:
        cat = get_catalog()
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_lake_freshness: catalog unavailable: %s", exc)
        return {k: None for k in specs}

    out: dict = {}
    for short, table_id in specs.items():
        try:
            out[short] = latest_loaded_date(cat.load_table(table_id), lookback_days=35)
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_lake_freshness(%s) failed: %s", short, exc)
            out[short] = None
    return out
