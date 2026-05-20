"""
Active-universe resolver — reads from the canonical `stream_universe` CH table.

Per the LOCKED architecture in
[docs/standards/data/symbol_lifecycle.md](../../../docs/standards/data/symbol_lifecycle.md),
`stream_universe` is the **single source of truth** for "what's our
hot universe?" Watchlists are pure organization (they auto-extend the
universe on add). The legacy `SEED_SYMBOLS ∪ watchlists` derivation
is retired.

Three universe specs supported across the codebase:

  - ``"active"``     → stream_universe symbols (canonical; the default)
  - ``"seed"``       → legacy SEED_SYMBOLS list (kept for back-compat
                       in operator scripts; the static 100-ticker
                       reference set). New code paths should NOT use
                       this — `stream_universe` is the canonical input.
  - ``"all"`` / ``"*"`` → empty list (whole-market signal for Polygon
                       flat-files; downstream interprets as "no filter")
  - ``"AAPL,NVDA…"`` → explicit comma-separated list (operator override)

Empty stream_universe = empty universe. No SEED_SYMBOLS fallback. If
the operator hasn't added anything to the stream universe, the
nightlies are no-ops for their universe-bounded steps. This is the
correct behavior post-FE-CONTRACTS-4-final: there is no implicit
"things we secretly stream even though no one asked for them."

Cold-start safety: the function never raises on CH outages. If the
stream_universe read fails, returns an empty list and logs a warning.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# Canonical spec strings. Use these constants in callers rather than
# raw literals so refactors are typed.
UNIVERSE_SPEC_SEED = "seed"
UNIVERSE_SPEC_ACTIVE = "active"


def get_active_universe(
    *,
    include_seed_fallback: bool = False,  # deprecated; ignored
    watchlist_kinds: Optional[list[str]] = None,  # deprecated; ignored
) -> list[str]:
    """Return the active universe as a sorted, deduplicated list.

    Canonical behavior: reads symbols from the CH `stream_universe`
    table (the canonical "what's streaming" source per
    docs/standards/data/symbol_lifecycle.md). No SEED_SYMBOLS
    fallback — empty stream_universe returns an empty list.

    Args:
        include_seed_fallback: DEPRECATED. Previously controlled
            whether an empty stream_universe fell back to SEED_SYMBOLS.
            That fallback was removed when stream_universe became
            canonical. Kept as a keyword for legacy callers; ignored.
        watchlist_kinds: DEPRECATED. Pre-FE-CONTRACTS-4-final the
            universe was derived from watchlists. Kept as a kwarg for
            legacy callers; ignored.
    """
    if include_seed_fallback is True:
        logger.debug(
            "get_active_universe: include_seed_fallback=True ignored "
            "(SEED_SYMBOLS fallback was removed; stream_universe is canonical)"
        )
    if watchlist_kinds is not None:
        logger.debug(
            "get_active_universe: watchlist_kinds=%r ignored "
            "(universe now reads from stream_universe; watchlists no "
            "longer drive the universe)",
            watchlist_kinds,
        )

    try:
        from app.services.stream import stream_service

        return sorted(stream_service.list_active_symbols())
    except Exception as e:  # noqa: BLE001 — boundary
        logger.warning(
            "get_active_universe: stream_universe read failed (%s); "
            "returning empty list (no SEED_SYMBOLS fallback)",
            e,
        )
        return []


def resolve_universe_spec(spec: str) -> list[str]:
    """Translate a config spec string → list[str].

    Spec strings:
      - "active" / "universe" / "dynamic" / "" / None → stream_universe (canonical)
      - "seed" / "seed-100"                           → SEED_SYMBOLS (legacy)
      - "all" / "*"                                   → empty list (whole-market signal)
      - "AAPL,NVDA,MSFT"                              → explicit list

    Used by nightly_polygon_refresh, nightly_schwab_refresh,
    silver_ohlcv_build, and any future operator-facing CLI that takes
    a symbols flag. Default (empty/None) resolves to the canonical
    active universe rather than seed.
    """
    s = (spec or "").strip().lower()
    if s in ("", "active", "universe", "dynamic"):
        return get_active_universe()
    if s in ("seed", "seed-100", "seed_100"):
        # Legacy SEED_SYMBOLS, kept for operator scripts that
        # explicitly request the static curated list.
        from app.data.seed_universe import SEED_SYMBOLS

        return list(SEED_SYMBOLS)
    if s in ("all", "*"):
        # Whole-market signal. Polygon flat-files honor this as "no
        # symbol filter — import everything".
        return []
    # Explicit CSV.
    return [tok.strip().upper() for tok in spec.split(",") if tok.strip()]
