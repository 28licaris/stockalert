"""
MCP tools — sector-rotation themes (read + create/delete).

Themes are data (a ClickHouse `sector_themes` store), so an agent can define
a new thematic basket — e.g. "Copper Miners" = FCX, SCCO, TECK, … — and have
it appear on the Sectors page without a code change. Creating a theme also
onboards its constituents into the streaming universe (membership + Schwab
tip-fill + deep history) in the background; nothing is ever removed from the
universe.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.mcp.middleware import tool_call
from app.mcp.server import mcp
from app.services.sectors import service as sectors_service
from app.services.sectors.schemas import (
    ThemeCreateRequest,
    ThemeMutationResponse,
    ThemeRecord,
)

logger = logging.getLogger(__name__)


@mcp.tool()
def list_sector_themes() -> list[ThemeRecord]:
    """List the thematic baskets on the Sector Rotation page (data-driven).

    Each theme has an id, name, short chart label, and its constituent
    tickers (holdings). Built-in S&P sector ETFs are not included here.
    """
    with tool_call("list_sector_themes"):
        return sectors_service.list_themes()


@mcp.tool()
async def create_sector_theme(
    name: str,
    members: list[str],
    label: Optional[str] = None,
    weights: Optional[dict[str, float]] = None,
    benchmark: str = "SPY",
) -> ThemeMutationResponse:
    """Create (or replace) a thematic basket on the Sector Rotation page.

    Args:
      name: display name, e.g. "Copper Miners". The theme id is derived from it.
      members: constituent tickers, e.g. ["FCX","SCCO","TECK","ERO","HBM"].
      label: short chart label for the scatter dot (defaults from name).
      weights: optional {ticker: weight} map; omit for equal weight.
      benchmark: comparison symbol (default SPY).

    The composite is measured vs the benchmark on the RRG. New constituents are
    onboarded into the streaming universe in the background (membership +
    Schwab tip-fill + deep history) so they get tracked with no data gaps; the
    theme appears once its members have enough history. Returns the created
    theme, the constituents being onboarded, and the full theme list.
    """
    with tool_call("create_sector_theme"):
        req = ThemeCreateRequest(
            name=name, members=members, label=label,
            weights=weights, benchmark=benchmark,
        )
        return await sectors_service.create_theme(req)


@mcp.tool()
def delete_sector_theme(theme_id: str) -> ThemeMutationResponse:
    """Remove a thematic basket from the Sector Rotation page (soft-delete).

    The theme's constituents STAY in the streaming universe — deleting a theme
    only removes the grouping, never prunes tracked symbols. Returns the
    remaining theme list.
    """
    with tool_call("delete_sector_theme"):
        return sectors_service.delete_theme(theme_id)
