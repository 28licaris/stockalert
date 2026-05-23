"""
Lake (Iceberg bronze) read routes — the CH-independent surface.

These routes are the **first agent-facing read surface** that does not
depend on ClickHouse. Historical data for ML training, backtesting,
and LLM-agent context flows through here. The same `BronzeReader`
service backs the future MCP tools (Pre-Phase 3 Step 3), so the
Pydantic response shape is the contract both surfaces share.

Design rules (see `docs/standards/platform_design.md`):

  - Route handlers contain ZERO business logic. They wrap a service
    method, convert exceptions to HTTP status codes, and return the
    service's response object.
  - `BronzeReader` is injected via FastAPI `Depends`. Tests override
    via `app.dependency_overrides[get_bronze_reader] = ...`.
  - No CH imports anywhere in the call path. The CH-independence
    claim is preserved by construction — if you find yourself adding
    `from app.db import ...` here, stop and re-think.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.services.readers.bronze_reader import BronzeReader
from app.services.readers.lake_metadata_reader import LakeMetadataReader
from app.services.readers.schemas import (
    BronzeBarsResponse,
    LakeLatestDayResponse,
    LakeSnapshotsResponse,
    LakeSymbolsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@lru_cache(maxsize=1)
def _build_reader() -> BronzeReader:
    """
    Memoized reader instance. The underlying catalog is already
    `@lru_cache`'d in `iceberg_catalog.get_catalog()`, but caching the
    reader itself avoids re-running `from_settings()` on every request.
    """
    return BronzeReader.from_settings()


@lru_cache(maxsize=1)
def _build_metadata_reader() -> LakeMetadataReader:
    return LakeMetadataReader.from_settings()


def get_metadata_reader() -> LakeMetadataReader:
    """FastAPI dependency provider — override in tests."""
    return _build_metadata_reader()


def get_bronze_reader() -> BronzeReader:
    """FastAPI dependency provider — override in tests."""
    return _build_reader()


@router.get("/lake/bars", response_model=BronzeBarsResponse)
def get_lake_bars(
    symbol: str = Query(..., description="Ticker symbol, e.g. 'AAPL'."),
    start: datetime = Query(
        ...,
        description=(
            "Window start (inclusive, ISO 8601). Naive datetimes treated as UTC."
        ),
    ),
    end: datetime = Query(
        ...,
        description=(
            "Window end (exclusive, ISO 8601). Naive datetimes treated as UTC."
        ),
    ),
    provider: str = Query(
        "polygon",
        description="Bronze provider table to read from. 'polygon' or 'schwab'.",
    ),
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=50_000,
        description=(
            "If set, return at most this many bars (the MOST RECENT N "
            "from within the window). Useful for capping payload size "
            "when an agent asks for a wide range."
        ),
    ),
    reader: BronzeReader = Depends(get_bronze_reader),
) -> BronzeBarsResponse:
    """
    Return bronze minute bars for `symbol` in the half-open window
    `[start, end)` from the named provider's bronze Iceberg table.

    **Does not touch ClickHouse.** This route works when CH is stopped,
    redeployed, or wiped — that's intentional and load-bearing for
    ML-reproducibility and agent-readiness.

    Response shape matches `BronzeBarsResponse`: the request echo
    (symbol/start/end/provider) plus `bars: list[BronzeBar]` and
    `count: int`. MCP tools will return identical shape.

    Status codes:
      - 200 — query succeeded; `bars` may be empty if no rows in window.
      - 400 — unknown provider (programmer error / bad client input).
      - 500 — infra failure reading from Iceberg / S3 / Glue.
    """
    try:
        bars = reader.get_bars(
            symbol, start, end, provider=provider, limit=limit
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — boundary; convert to 500
        logger.exception("lake.get_bars failed for %s [%s..%s]", symbol, start, end)
        raise HTTPException(
            status_code=500,
            detail=f"bronze read failed: {type(exc).__name__}: {exc}",
        ) from exc

    return BronzeBarsResponse(
        symbol=symbol,
        start=start,
        end=end,
        provider=provider,
        bars=bars,
        count=len(bars),
    )


@router.get("/lake/symbols", response_model=LakeSymbolsResponse)
def get_lake_symbols(
    provider: str = Query(
        "polygon",
        description="Bronze provider table to scan. 'polygon' or 'schwab'.",
    ),
    since: Optional[datetime] = Query(
        None,
        description=(
            "Only return symbols with at least one bar at-or-after this "
            "timestamp. Naive datetimes treated as UTC. Defaults to "
            "30 days back if omitted — keeps the scan bounded against a "
            "2B-row bronze table."
        ),
    ),
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=50_000,
        description="Cap on number of symbols returned. Sorted alphabetically before truncation.",
    ),
    reader: BronzeReader = Depends(get_bronze_reader),
) -> LakeSymbolsResponse:
    """
    Distinct symbols known to bronze within the time window.
    Universe discovery for screeners and agents.

    **Does not touch ClickHouse.**

    Status codes:
      - 200 — query succeeded; `symbols` may be empty if no rows.
      - 400 — unknown provider.
      - 500 — infra failure.
    """
    try:
        symbols = reader.list_symbols(provider=provider, since=since, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — boundary; convert to 500
        logger.exception("lake.list_symbols failed for provider=%s since=%s", provider, since)
        raise HTTPException(
            status_code=500,
            detail=f"bronze read failed: {type(exc).__name__}: {exc}",
        ) from exc

    # Echo the effective `since` back so consumers can record what was queried.
    effective_since = since if since is not None else (
        datetime.now(timezone.utc) - timedelta(days=30)
    )

    return LakeSymbolsResponse(
        provider=provider,
        since=effective_since,
        symbols=symbols,
        count=len(symbols),
    )


@router.get("/lake/last-day", response_model=LakeLatestDayResponse)
def get_lake_last_day(
    provider: str = Query(
        "polygon",
        description="Bronze provider table to inspect. 'polygon' or 'schwab'.",
    ),
    lookback_days: int = Query(
        14,
        ge=1,
        le=365,
        description=(
            "How far back to scan for the most-recent bar. Defaults to 14 — "
            "long enough to span weekends and short trading-day gaps, "
            "short enough to keep the metadata scan cheap."
        ),
    ),
    reader: BronzeReader = Depends(get_bronze_reader),
) -> LakeLatestDayResponse:
    """
    Most recent trading day (ET basis) with at least one bar in
    bronze. Useful for gap detection and for agents anchoring queries
    to "freshest available."

    Returns `latest_trading_day: null` if no rows are present within
    `lookback_days`. Does NOT touch ClickHouse.

    Status codes:
      - 200 — query succeeded (date may be null).
      - 400 — unknown provider.
      - 500 — infra failure.
    """
    try:
        latest = reader.latest_trading_day(provider=provider, lookback_days=lookback_days)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("lake.latest_trading_day failed for provider=%s", provider)
        raise HTTPException(
            status_code=500,
            detail=f"bronze read failed: {type(exc).__name__}: {exc}",
        ) from exc

    return LakeLatestDayResponse(provider=provider, latest_trading_day=latest)


@router.get("/lake/snapshots", response_model=LakeSnapshotsResponse)
def list_lake_snapshots(
    tables: Optional[str] = Query(
        None,
        description=(
            "Comma-separated subset of "
            "['polygon_raw', 'polygon_adjusted', 'schwab_universe', "
            "'market_corp_actions']. Omit for all four."
        ),
    ),
    limit: int = Query(
        20, ge=1, le=500,
        description=(
            "Per-table cap on returned snapshots (most recent first). "
            "Default 20 covers ~3 weeks of nightly commits or ~100 min "
            "of the 5-min live writer."
        ),
    ),
    reader: LakeMetadataReader = Depends(get_metadata_reader),
) -> LakeSnapshotsResponse:
    """Recent Iceberg snapshots across the v2 equities tables.

    Use this to answer:
      - When did the nightly cron last run? (most-recent snapshot's
        committed_at on polygon_raw / schwab_universe)
      - What did the last polygon_adjustment_job add? (most-recent
        polygon_adjusted snapshot's `added_records`)
      - I want a deterministic backtest — what snapshot_id should I
        pin? (any snapshot_id from this list)
      - Bad data in latest commit — what's the previous good snapshot?
        (parent_snapshot_id, walked back)

    Returns merged-and-sorted snapshots (DESC by committed_at) across
    all requested tables. A table that fails to load is logged + its
    snapshots excluded; the rest still flow through. Does NOT scan
    data files — metadata-only read.
    """
    parsed: Optional[list[str]] = None
    if tables is not None:
        parsed = [t.strip() for t in tables.split(",") if t.strip()]
        if not parsed:
            parsed = None
    return reader.list_snapshots(tables=parsed, limit=limit)
