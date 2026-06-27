"""
Active-universe resolver — reads from the canonical `stream_universe` CH table.

Per the LOCKED architecture in
[docs/standards/data/symbol_lifecycle.md](../../../docs/standards/data/symbol_lifecycle.md),
`stream_universe` is the **single source of truth** for "what's our
hot universe?" Watchlists are pure organization (they auto-extend the
universe on add). The legacy `SEED_SYMBOLS ∪ watchlists` derivation
is retired.

Three universe specs are supported across the codebase:

  - ``"active"``     → stream_universe symbols (canonical; the default)
  - ``"all"`` / ``"*"`` → empty list (whole-market signal for Polygon
                       flat-files; downstream interprets as "no filter")
  - ``"AAPL,NVDA…"`` → explicit comma-separated list (operator override)

Empty stream_universe = empty universe. No SEED_SYMBOLS fallback. If
the operator hasn't added anything to the stream universe, the
nightlies are no-ops for their universe-bounded steps. This is the
correct behavior post-FE-CONTRACTS-4-final: there is no implicit
"things we secretly stream even though no one asked for them."

ClickHouse read failures propagate. An unavailable source of truth must not
become an empty universe or trigger a fallback.
"""
from __future__ import annotations

# Canonical spec strings. Use these constants in callers rather than
# raw literals so refactors are typed.
UNIVERSE_SPEC_ACTIVE = "active"


def get_active_universe() -> list[str]:
    """Return the active universe as a sorted, deduplicated list.

    Canonical behavior: reads symbols from the CH `stream_universe`
    table (the canonical "what's streaming" source per
    docs/standards/data/symbol_lifecycle.md). No SEED_SYMBOLS
    fallback — empty stream_universe returns an empty list.

    """
    from app.services.stream import stream_service

    return sorted(stream_service.list_active_symbols())


def resolve_universe_spec(spec: str) -> list[str]:
    """Translate a config spec string → list[str].

    Spec strings:
      - "active" / "universe" / "dynamic" / "" / None → stream_universe (canonical)
      - "all" / "*"                                   → empty list (whole-market signal)
      - "AAPL,NVDA,MSFT"                              → explicit list

    Used by nightly_equities_polygon_refresh, nightly_schwab_refresh,
    silver_ohlcv_build, and any future operator-facing CLI that takes
    a symbols flag. Default (empty/None) resolves to the canonical
    active universe rather than seed.
    """
    s = (spec or "").strip().lower()
    if s in ("", "active", "universe", "dynamic"):
        return get_active_universe()
    if s in ("seed", "seed-100", "seed_100"):
        raise ValueError(
            "the static seed universe is retired; use 'active' to read "
            "the authoritative ClickHouse stream_universe table"
        )
    if s in ("all", "*"):
        # Whole-market signal. Polygon flat-files honor this as "no
        # symbol filter — import everything".
        return []
    # Explicit CSV.
    return [tok.strip().upper() for tok in spec.split(",") if tok.strip()]
