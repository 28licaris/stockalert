"""HTTP API for the stream universe + live subscription state.

Mounted at `/api/v1/stream/*`. The CH `stream_universe` table is the
source of truth; mutations here subscribe / unsubscribe Schwab
directly via `StreamService` (no longer indirected through
WatchlistService — see docs/frontend_api_contracts.md §10.1 locked
sticky-universe model).

`/api/v1/seed/*` remains mounted (`routes_seed.py`) and returns the
same data via the back-compat `seed_service` alias; new clients
should target `/stream/*`.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from app.api.schemas.stream import (
    AddStreamRequest,
    ImportStreamRequest,
    StreamMutationResponse,
    StreamStatusResponse,
    StreamUniverseEntry,
    StreamUniverseResponse,
)
from app.services.stream import stream_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _entries(items: list[dict]) -> list[StreamUniverseEntry]:
    return [StreamUniverseEntry(**i) for i in items]


@router.get("/stream", response_model=StreamUniverseResponse)
async def list_stream_universe() -> StreamUniverseResponse:
    """List the active stream universe.

    On first read after the CH table is created, bootstraps from
    `SEED_SYMBOLS ∪ active-watchlist members` so the cockpit doesn't
    show an empty list out of the box.
    """
    bootstrapped, _ = await asyncio.to_thread(stream_service.bootstrap_if_empty)
    items = await asyncio.to_thread(stream_service.list_universe)
    return StreamUniverseResponse(
        items=_entries(items),
        count=len(items),
        bootstrapped=bootstrapped,
    )


@router.get("/stream/status", response_model=StreamStatusResponse)
async def stream_status() -> StreamStatusResponse:
    """Live subscription state. Polled by the cockpit's status tile."""
    s = await asyncio.to_thread(stream_service.status)
    return StreamStatusResponse(**s)


@router.post("/stream", response_model=StreamMutationResponse, status_code=201)
async def add_to_stream(req: AddStreamRequest) -> StreamMutationResponse:
    """Promote one symbol into the stream universe. Subscribes Schwab
    immediately + queues a silver-derived warmup (if enabled).

    Idempotent: re-adding an already-active symbol returns `changed=[]`.
    """
    try:
        result = await asyncio.to_thread(
            stream_service.add,
            req.symbol,
            asset_type=req.asset_type or "",
            notes=req.notes or "",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001 — boundary
        logger.error("stream add(%s) failed: %s", req.symbol, e, exc_info=True)
        raise HTTPException(500, str(e))
    return StreamMutationResponse(
        operation=result["operation"],
        changed=result["changed"],
        items=_entries(result["items"]),
        count=result["count"],
    )


@router.delete("/stream/{symbol}", response_model=StreamMutationResponse)
async def remove_from_stream(symbol: str) -> StreamMutationResponse:
    """Remove a symbol from the stream universe + unsubscribe Schwab.

    This is the ONLY API path that strips a symbol from the live stream;
    watchlist deletes are sticky (do not affect streaming).
    """
    try:
        result = await asyncio.to_thread(stream_service.remove, symbol)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001 — boundary
        logger.error("stream remove(%s) failed: %s", symbol, e, exc_info=True)
        raise HTTPException(500, str(e))
    return StreamMutationResponse(
        operation=result["operation"],
        changed=result["changed"],
        items=_entries(result["items"]),
        count=result["count"],
    )


@router.post("/stream/import", response_model=StreamMutationResponse)
async def import_stream(req: ImportStreamRequest) -> StreamMutationResponse:
    """Bulk-import symbols into the stream universe. Idempotent."""
    try:
        result = await asyncio.to_thread(
            stream_service.import_bulk,
            req.symbols,
            notes=req.notes or "",
        )
    except Exception as e:  # noqa: BLE001 — boundary
        logger.error("stream import(%s) failed: %s", req.symbols, e, exc_info=True)
        raise HTTPException(500, str(e))
    return StreamMutationResponse(
        operation=result["operation"],
        changed=result["changed"],
        items=_entries(result["items"]),
        count=result["count"],
    )
