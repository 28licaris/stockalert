"""Symbol-spec resolution for options snapshots."""
from __future__ import annotations

from typing import Callable, Sequence

SymbolResolver = Callable[[], Sequence[str]]
WatchlistResolver = Callable[[str], Sequence[str]]


def parse_symbols(value: str) -> list[str]:
    symbols = sorted(
        {token.strip().upper() for token in (value or "").split(",") if token.strip()}
    )
    if not symbols:
        raise ValueError("--symbols must include at least one symbol")
    return symbols


def resolve_active_symbols() -> Sequence[str]:
    from app.services.universe import resolve_universe_spec

    return resolve_universe_spec("active")


def resolve_watchlist_symbols(name: str) -> Sequence[str]:
    from app.services.live.watchlist_service import watchlist_service

    return watchlist_service.list_members(name)


def resolve_options_symbol_spec(
    value: str,
    *,
    active_resolver: SymbolResolver = resolve_active_symbols,
    watchlist_resolver: WatchlistResolver = resolve_watchlist_symbols,
) -> list[str]:
    spec = (value or "").strip()
    normalized = spec.lower()
    if normalized in {"all", "*"}:
        raise ValueError("'all' is not supported for Schwab option-chain snapshots")
    if normalized in {"active", "universe", "dynamic"}:
        symbols = sorted(
            {symbol.strip().upper() for symbol in active_resolver() if symbol.strip()}
        )
        if not symbols:
            raise ValueError("active universe returned no symbols")
        return symbols
    if normalized.startswith("watchlist:"):
        name = spec.split(":", 1)[1].strip()
        if not name:
            raise ValueError("watchlist symbol spec must include a watchlist name")
        symbols = sorted(
            {
                symbol.strip().upper()
                for symbol in watchlist_resolver(name)
                if symbol.strip()
            }
        )
        if not symbols:
            raise ValueError(f"watchlist {name!r} returned no symbols")
        return symbols
    return parse_symbols(spec)
