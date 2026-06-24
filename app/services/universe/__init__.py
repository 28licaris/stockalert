"""
Universe service — single source of truth for "what symbols does the
system care about right now?"

Defines the **active universe**: the set of symbols that are eligible
for nightly bronze refresh + silver build + live stream subscription
+ any other per-symbol operation that needs to know the current scope.

Per [data_flow_review_2026-05-17.md §G1](../../../docs/data_flow_review_2026-05-17.md):

  active_universe = active rows in the ClickHouse stream_universe table

Adding a symbol to any watchlist (via watchlist_service.add_members or
add_streamed_symbol) automatically grows the universe — no separate
"promote-to-seed" step needed for the nightlies to start covering it.

There is no static fallback. ClickHouse is the authoritative runtime source,
and read failures propagate.
"""
from __future__ import annotations

from app.services.universe.active_universe import (
    UNIVERSE_SPEC_ACTIVE,
    get_active_universe,
    resolve_universe_spec,
)

__all__ = [
    "UNIVERSE_SPEC_ACTIVE",
    "get_active_universe",
    "resolve_universe_spec",
]
