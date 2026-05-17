"""
MCP tools — watchlist read access (read-only).

Watchlists are the platform's "named universe" abstraction: a screener
defines one, the live monitor streams its symbols, the dashboard
charts them. Agents need read access for universe-aware queries
("what's in my swing-trade list right now?").

Mutation tools (create/add/remove) live in `tools/writes.py` — a
separate file with an allowlist gate. The structural test in
`test_mcp_layering.py` (to be added) enforces that this file
imports no watchlist mutation methods.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.mcp.middleware import tool_call
from app.mcp.server import mcp
from app.services.readers.schemas import (
    WatchlistDetail,
    WatchlistSummary,
    WatchlistsResponse,
)
from app.services.live.watchlist_service import watchlist_service

logger = logging.getLogger(__name__)


def _to_summary(wl: dict) -> WatchlistSummary:
    return WatchlistSummary(
        name=wl["name"],
        kind=wl.get("kind", "user"),
        description=wl.get("description") or "",
        is_active=wl.get("is_active", True),
        member_count=wl.get("member_count", 0) or 0,
        updated_at=wl.get("updated_at"),
    )


@mcp.tool()
def list_watchlists(include_inactive: bool = False) -> WatchlistsResponse:
    """All watchlists known to the platform, with member counts.

    USE WHEN: an agent is choosing which universe to query — "what
    named lists exist?", "show me all active watchlists." For the
    members of one list, follow up with `get_watchlist_members`.

    Args:
        include_inactive: When True, include soft-deleted lists too.
            Default False (active only).

    Returns:
        WatchlistsResponse with list[WatchlistSummary] + count.
    """
    with tool_call("list_watchlists", include_inactive=include_inactive):
        rows = watchlist_service.list_watchlists(include_inactive=include_inactive)
        summaries = [_to_summary(r) for r in rows]
        return WatchlistsResponse(watchlists=summaries, count=len(summaries))


@mcp.tool()
def get_watchlist(name: str) -> Optional[WatchlistDetail]:
    """Full detail (members + metadata) for one watchlist by name.

    USE WHEN: an agent is fetching a specific universe — "expand
    'swing-trades' into its symbol list", "what's in 'default'?"

    Args:
        name: Watchlist name (case sensitive).

    Returns:
        WatchlistDetail with members=list[str], or None if no
        watchlist with that name exists.
    """
    with tool_call("get_watchlist", name=name):
        wl = watchlist_service.get_watchlist(name)
        if wl is None:
            return None
        members = watchlist_service.list_members(name)
        return WatchlistDetail(
            name=wl["name"],
            kind=wl.get("kind", "user"),
            description=wl.get("description") or "",
            is_active=wl.get("is_active", True),
            members=members,
            member_count=len(members),
            updated_at=wl.get("updated_at"),
        )


@mcp.tool()
def get_watchlist_members(name: str) -> list[str]:
    """Just the symbol list for one watchlist (no metadata).

    USE WHEN: an agent has the watchlist name and only needs the
    symbol list — feeding it into a screener, building a quote-batch
    request, etc.

    Args:
        name: Watchlist name.

    Returns:
        list[str] of member tickers in insertion order. Empty list
        if the watchlist doesn't exist or has no members (use
        `get_watchlist` to distinguish).
    """
    with tool_call("get_watchlist_members", name=name):
        return watchlist_service.list_members(name)
