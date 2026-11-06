"""Symbols the nightly daily refresh must keep current beyond ``configs/liquid_universe.txt``.

The liquid-universe file is a fixed ~1,000-symbol research list. Two other sets of
symbols also need fresh daily bars or the Sector Rotation page goes stale again right
after they are backfilled:

* the **active stream universe** (shared sector themes' members live there, by the
  "tracked instruments live in the universe" rule);
* every ticker in a user's **personal themes** (fetched on demand, see
  ``daily_on_demand``). They are NOT added to the stream universe (the live stream is
  capped at 300 symbols); this is the only thing that keeps them current.

A source that cannot be read is reported as a warning, never silently skipped, and
never blocks the base refresh.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)


def extra_daily_symbols(
    *,
    stream_universe: Callable[[], Iterable[str]] | None = None,
    personal_tickers: Callable[[], Iterable[str]] | None = None,
) -> tuple[list[str], list[str]]:
    """Return ``(symbols, warnings)``: the union of both sources, sorted, plus one
    warning per source that could not be read."""
    out: set[str] = set()
    warnings: list[str] = []

    def _read_stream() -> Iterable[str]:
        from app.db.client import get_client

        rows = get_client().query(
            "SELECT symbol FROM stream_universe FINAL WHERE is_active = 1"
        ).result_rows
        return [r[0] for r in rows]

    def _read_personal() -> Iterable[str]:
        from app.services.sectors.personal_themes import PersonalThemesRepository

        return PersonalThemesRepository.from_settings().all_tickers()

    for name, reader in (
        ("stream_universe", stream_universe or _read_stream),
        ("personal themes", personal_tickers or _read_personal),
    ):
        try:
            out.update(s.strip().upper() for s in reader() if s and s.strip())
        except Exception as exc:  # noqa: BLE001 — reported, never swallowed
            msg = f"could not read {name}: {type(exc).__name__}: {str(exc)[:120]}"
            logger.error("extra_daily_symbols: %s", msg)
            warnings.append(msg)
    return sorted(out), warnings
