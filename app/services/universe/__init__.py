"""
Universe service — single source of truth for "what symbols does the
system care about right now?"

Defines the **active universe**: the set of symbols that are eligible
for nightly bronze refresh + silver build + live stream subscription
+ any other per-symbol operation that needs to know the current scope.

Per [data_flow_review_2026-05-17.md §G1](../../../docs/data_flow_review_2026-05-17.md):

  active_universe = SEED_SYMBOLS ∪ <every symbol in any active watchlist>

Adding a symbol to any watchlist (via watchlist_service.add_members or
add_streamed_symbol) automatically grows the universe — no separate
"promote-to-seed" step needed for the nightlies to start covering it.

SEED_SYMBOLS remains the curated floor so the nightlies always have
something to do even when no user watchlists exist (cold-start /
fresh-install).
"""
from __future__ import annotations

from app.services.universe.active_universe import (
    UNIVERSE_SPEC_ACTIVE,
    UNIVERSE_SPEC_SEED,
    get_active_universe,
    resolve_universe_spec,
)

__all__ = [
    "UNIVERSE_SPEC_ACTIVE",
    "UNIVERSE_SPEC_SEED",
    "get_active_universe",
    "resolve_universe_spec",
]
