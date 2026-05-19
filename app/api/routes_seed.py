"""HTTP API for the seed universe (FE-CONTRACTS-4).

Cockpit-editable list of symbols permanently part of the streaming
universe. Mutations here drive the existing refcounted subscribe +
backfill machinery through `WatchlistService`. See
[docs/frontend_api_contracts.md §4.4](../../../docs/frontend_api_contracts.md).
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from app.api.schemas.seed import (
    AddSeedRequest,
    ImportSeedRequest,
    SeedEntry,
    SeedMutationResponse,
    SeedUniverseResponse,
)
from app.services.seed import seed_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _entries(items: list[dict]) -> list[SeedEntry]:
    return [SeedEntry(**i) for i in items]


@router.get("/seed", response_model=SeedUniverseResponse)
async def list_seed() -> SeedUniverseResponse:
    """List the active seed universe.

    On first read after the CH table is created, bootstraps from
    `SEED_SYMBOLS` (curated 100) ∪ default-watchlist members so the
    cockpit doesn't show an empty list out of the box.
    """
    bootstrapped, _ = await asyncio.to_thread(seed_service.bootstrap_if_empty)
    items = await asyncio.to_thread(seed_service.list_seed)
    return SeedUniverseResponse(
        items=_entries(items),
        count=len(items),
        bootstrapped=bootstrapped,
    )


@router.post("/seed", response_model=SeedMutationResponse, status_code=201)
async def add_seed(req: AddSeedRequest) -> SeedMutationResponse:
    """Promote a single symbol into the seed universe.

    Idempotent: re-adding an already-active symbol returns
    `changed=[]`. Triggers `WatchlistService.add_members("default", [sym])`
    so the symbol starts streaming + backfill kicks in.
    """
    try:
        result = await asyncio.to_thread(
            seed_service.add,
            req.symbol,
            asset_type=req.asset_type or "",
            notes=req.notes or "",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001 — boundary
        logger.error("seed add(%s) failed: %s", req.symbol, e, exc_info=True)
        raise HTTPException(500, str(e))
    return SeedMutationResponse(
        operation=result["operation"],
        changed=result["changed"],
        items=_entries(result["items"]),
        count=result["count"],
    )


@router.delete("/seed/{symbol}", response_model=SeedMutationResponse)
async def remove_seed(symbol: str) -> SeedMutationResponse:
    """Take a symbol OUT of the seed universe.

    Calls `WatchlistService.remove_members("default", [sym])`, which
    decrements the refcount. Symbols still held by another watchlist
    keep streaming (sticky-universe invariant — only complete removal
    from ALL watchlists fully unsubscribes).
    """
    try:
        result = await asyncio.to_thread(seed_service.remove, symbol)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001 — boundary
        logger.error("seed remove(%s) failed: %s", symbol, e, exc_info=True)
        raise HTTPException(500, str(e))
    return SeedMutationResponse(
        operation=result["operation"],
        changed=result["changed"],
        items=_entries(result["items"]),
        count=result["count"],
    )


@router.post("/seed/import", response_model=SeedMutationResponse)
async def import_seed(req: ImportSeedRequest) -> SeedMutationResponse:
    """Bulk-import symbols into the seed universe. Idempotent."""
    try:
        result = await asyncio.to_thread(
            seed_service.import_bulk,
            req.symbols,
            notes=req.notes or "",
        )
    except Exception as e:  # noqa: BLE001 — boundary
        logger.error("seed import(%s) failed: %s", req.symbols, e, exc_info=True)
        raise HTTPException(500, str(e))
    return SeedMutationResponse(
        operation=result["operation"],
        changed=result["changed"],
        items=_entries(result["items"]),
        count=result["count"],
    )
