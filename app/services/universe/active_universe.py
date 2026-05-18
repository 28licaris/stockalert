"""
Active-universe resolver — SEED_SYMBOLS ∪ active-watchlist symbols.

Three universe specs supported across the codebase:

  - ``"seed"``       → SEED_SYMBOLS only (the curated 100, static)
  - ``"active"``     → SEED_SYMBOLS ∪ active-watchlist symbols (dynamic)
  - ``"AAPL,NVDA…"`` → explicit comma-separated list (operator override)

The nightly bronze refreshers (Polygon + Schwab) and silver build all
consume this — adding any symbol to any watchlist (via
`watchlist_service.add_members` or `add_streamed_symbol`)
automatically grows the universe. **No separate "promote" step
needed.** Per data_flow_review §G1.

Cold-start safety: if ClickHouse is down or `watchlists` is empty,
`get_active_universe()` falls back to SEED_SYMBOLS so the nightlies
still have something to do.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.data.seed_universe import SEED_SYMBOLS

logger = logging.getLogger(__name__)


# Canonical spec strings. Use these constants in callers rather than
# raw literals so refactors are typed.
UNIVERSE_SPEC_SEED = "seed"
UNIVERSE_SPEC_ACTIVE = "active"


def get_active_universe(
    *,
    include_seed: bool = True,
    watchlist_kinds: Optional[list[str]] = None,
) -> list[str]:
    """Return the active universe as a sorted, deduplicated list.

    Default behavior: SEED_SYMBOLS ∪ <every active-watchlist symbol
    across every active watchlist>.

    Args:
        include_seed: When True (default), unions with SEED_SYMBOLS.
            Set False if you want strictly the watchlist contribution
            (rare — the seed is the curated floor).
        watchlist_kinds: Restrict to watchlists of these kinds (e.g.
            ``['user']`` or ``['baseline']``). None = all kinds.
    """
    symbols: set[str] = set()
    if include_seed:
        symbols.update(SEED_SYMBOLS)

    # Best-effort watchlist read. If CH is unavailable (cold start, mid-
    # restart) we degrade to seed-only rather than throwing — the
    # nightlies must remain robust to ClickHouse outages.
    try:
        from app.db import watchlist_repo  # local import: avoid CH at module load
        wl_symbols = watchlist_repo.list_all_active_symbols(
            kinds=watchlist_kinds,
        )
        symbols.update(wl_symbols)
    except Exception as e:
        logger.warning(
            "get_active_universe: watchlist read failed (%s); "
            "falling back to seed-only", e,
        )

    return sorted(symbols)


def resolve_universe_spec(spec: str) -> list[str]:
    """Translate a config spec string → list[str].

    Spec strings:
      - "seed" / "seed-100" / "" / None → SEED_SYMBOLS
      - "active"                        → get_active_universe()
      - "AAPL,NVDA,MSFT"                → explicit list (uppercased)

    Used by nightly_polygon_refresh, nightly_schwab_refresh,
    silver_ohlcv_build, and any future operator-facing CLI that takes
    a symbols flag.
    """
    s = (spec or "").strip().lower()
    if s in ("", "seed", "seed-100", "seed_100"):
        return list(SEED_SYMBOLS)
    if s in ("active", "universe", "dynamic"):
        return get_active_universe()
    # Explicit CSV.
    return [tok.strip().upper() for tok in spec.split(",") if tok.strip()]
