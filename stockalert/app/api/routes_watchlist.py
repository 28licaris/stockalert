"""HTTP API for live-bar ingestion watchlists.

Two URL families coexist here:

  - Legacy single-watchlist:  `/watchlist`, `/watchlist/add`, `/watchlist/remove`,
    `/watchlist/snapshot`. These operate on the implicit `default` watchlist
    via a shim on `WatchlistService`. Kept for back-compat with older clients;
    the dashboard now uses the multi-list family below.

  - Multi-watchlist (Phase 1.3): `/api/watchlists` and `/api/watchlists/{name}/*`.
    Full CRUD over named watchlists plus per-list members and snapshot.
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import queries, watchlist_repo
from app.services.live.watchlist_service import watchlist_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _ts(v):
    """ISO-format a datetime, forcing a UTC marker so JS `new Date(...)` doesn't
    interpret naive ClickHouse timestamps as local time."""
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        # ClickHouse returns tz-naive DateTime64 even on UTC columns. Treat
        # naive values as UTC and stamp them with `Z` so the wire format is
        # unambiguous.
        if getattr(v, "tzinfo", None) is None:
            return v.isoformat() + "Z"
        return v.isoformat()
    return str(v)


def _serialize_wl(wl: dict, *, with_members: bool = False) -> dict:
    """Normalize a watchlist row for the HTTP response (consistent shape across endpoints)."""
    out = {
        "name": wl["name"],
        "kind": wl["kind"],
        "description": wl.get("description") or "",
        "is_active": wl.get("is_active", True),
        "updated_at": _ts(wl["updated_at"]) if wl.get("updated_at") else None,
    }
    if with_members:
        members = watchlist_service.list_members(wl["name"])
        out["members"] = members
        out["member_count"] = len(members)
    return out


async def _snapshot_for(symbols: list[str]) -> list[dict]:
    rows = await queries.latest_bar_per_symbol_async(symbols) if symbols else []
    by_symbol = {r["symbol"]: r for r in rows}
    snapshot = []
    for s in symbols:
        r = by_symbol.get(s)
        if r is None:
            snapshot.append({"symbol": s, "bar_count": 0, "ts": None})
        else:
            snapshot.append({
                "symbol": s,
                "ts": _ts(r["ts"]),
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["volume"],
                "bar_count": r["bar_count"],
            })
    return snapshot


class SymbolsRequest(BaseModel):
    symbols: List[str] = Field(..., description="Stock symbols, e.g. ['SPY', 'AAPL']")


# ============================================================================
# Legacy single-watchlist routes  (operate on the implicit `default` watchlist)
# ============================================================================


@router.get("/watchlist")
async def get_watchlist():
    """Return the current (default) watchlist and stream status."""
    return watchlist_service.status()


@router.post("/watchlist/add")
async def add_to_watchlist(req: SymbolsRequest):
    """Add one or more symbols and immediately subscribe to their live bars."""
    try:
        return watchlist_service.add(req.symbols)
    except Exception as e:
        logger.error("Watchlist add failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/watchlist/remove")
async def remove_from_watchlist(req: SymbolsRequest):
    """Remove one or more symbols and unsubscribe from their live bars."""
    try:
        return watchlist_service.remove(req.symbols)
    except Exception as e:
        logger.error("Watchlist remove failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/watchlist/snapshot")
async def watchlist_snapshot():
    """Latest bar for each symbol in the default watchlist."""
    return await _snapshot_for(watchlist_service.list_symbols())


# ============================================================================
# Multi-watchlist routes (Phase 1.3) — `/api/watchlists` family
# ============================================================================


class CreateWatchlistRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    kind: str = Field("user", description="One of: user, baseline, adhoc")
    description: str = Field("", max_length=500)


class RenameWatchlistRequest(BaseModel):
    new_name: str = Field(..., min_length=1, max_length=64)


@router.get("/api/watchlists")
async def list_watchlists_endpoint(include_inactive: bool = False, with_members: bool = True):
    """List all watchlists. By default returns active ones with their member lists."""
    wls = watchlist_service.list_watchlists(include_inactive=include_inactive)
    return [_serialize_wl(wl, with_members=with_members) for wl in wls]


@router.post("/api/watchlists", status_code=201)
async def create_watchlist_endpoint(req: CreateWatchlistRequest):
    """Create (or reactivate) a watchlist. Idempotent."""
    if req.kind not in watchlist_repo.VALID_KINDS:
        raise HTTPException(400, f"invalid kind '{req.kind}'; allowed: {sorted(watchlist_repo.VALID_KINDS)}")
    try:
        wl = watchlist_service.create_watchlist(req.name, kind=req.kind, description=req.description)
        return _serialize_wl(wl, with_members=True)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/watchlists/{name}")
async def get_watchlist_endpoint(name: str):
    """Single watchlist with its members."""
    wl = watchlist_service.get_watchlist(name)
    if wl is None or not wl.get("is_active"):
        raise HTTPException(404, f"watchlist '{name}' not found")
    return _serialize_wl(wl, with_members=True)


@router.patch("/api/watchlists/{name}")
async def rename_watchlist_endpoint(name: str, req: RenameWatchlistRequest):
    """Rename a watchlist (members move with it)."""
    if name == "default":
        # Keep `default` as a stable shim target so the legacy /watchlist routes
        # don't break. Users who want to rename it should create a new list and
        # migrate manually.
        raise HTTPException(400, "the 'default' watchlist cannot be renamed")
    try:
        wl = watchlist_service.rename_watchlist(name, req.new_name)
        return _serialize_wl(wl, with_members=True)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/api/watchlists/{name}")
async def delete_watchlist_endpoint(name: str):
    """Soft-delete a watchlist. Members move with it (refcount on subscriptions decrements)."""
    if name == "default":
        raise HTTPException(400, "the 'default' watchlist cannot be deleted")
    if not watchlist_service.delete_watchlist(name):
        raise HTTPException(404, f"watchlist '{name}' not found or already inactive")
    return {"deleted": name}


@router.get("/api/watchlists/{name}/members")
async def list_members_endpoint(name: str):
    """List active members of a watchlist."""
    wl = watchlist_service.get_watchlist(name)
    if wl is None or not wl.get("is_active"):
        raise HTTPException(404, f"watchlist '{name}' not found")
    return watchlist_service.list_members(name)


@router.post("/api/watchlists/{name}/members")
async def add_members_endpoint(name: str, req: SymbolsRequest):
    """Add symbols to a watchlist (auto-creates it). Idempotent. Triggers backfill for newly-added symbols."""
    try:
        return watchlist_service.add_members(name, req.symbols)
    except Exception as e:
        logger.error("Watchlist add_members failed: %s", e, exc_info=True)
        raise HTTPException(500, str(e))


@router.delete("/api/watchlists/{name}/members")
async def remove_members_endpoint(name: str, req: SymbolsRequest):
    """Remove symbols from a watchlist. Idempotent."""
    wl = watchlist_service.get_watchlist(name)
    if wl is None or not wl.get("is_active"):
        raise HTTPException(404, f"watchlist '{name}' not found")
    try:
        return watchlist_service.remove_members(name, req.symbols)
    except Exception as e:
        logger.error("Watchlist remove_members failed: %s", e, exc_info=True)
        raise HTTPException(500, str(e))


@router.get("/api/watchlists/{name}/snapshot")
async def watchlist_snapshot_endpoint(name: str):
    """Latest bar for each symbol in the named watchlist."""
    wl = watchlist_service.get_watchlist(name)
    if wl is None or not wl.get("is_active"):
        raise HTTPException(404, f"watchlist '{name}' not found")
    return await _snapshot_for(watchlist_service.list_members(name))
