"""Keep the rotation universe complete.

Platform rule: **tracked instruments live in the streaming universe.** Every
sector ETF and theme constituent shown on the sectors page must be in the
stream universe so it (a) streams live — no forward gaps, (b) gets the nightly
refresh, and (c) gets the Schwab tip-fill, which grows
`equities.schwab_universe`. So adding a theme to `definitions._THEMES`
auto-onboards its constituents: this reconciler runs at app startup and is
idempotent (a steady state with nothing new is a cheap no-op).
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def tracked_symbols() -> list[str]:
    """All symbols the sectors page tracks: sector ETFs + benchmark + every
    theme constituent (de-duplicated, order-stable)."""
    from app.services.sectors import definitions

    out = dict.fromkeys([*definitions.sector_symbols(), *definitions.theme_symbols()])
    return list(out)


# Symbols currently being onboarded — dedupe concurrent onboards (e.g. several
# theme creates touching overlapping tickers) so we never run the work twice.
_inflight: set[str] = set()


async def _onboard(symbols, *, tip_fill: bool, deep_history: bool) -> dict:
    """Onboard a SPECIFIC list of symbols: membership (stream-universe add +
    live subscribe) → Schwab tip-fill (recent ~48d, grows schwab_universe) →
    deep history (read_arrow union, enough bars for RRG). Skips symbols already
    in flight. Best-effort; never raises. This is the shared worker."""
    from app.services.stream import stream_service

    syms = [s for s in dict.fromkeys(symbols) if s and s not in _inflight]
    if not syms:
        return {"added": [], "tip_filled": [], "deep_filled": []}
    _inflight.update(syms)
    try:
        added: list[str] = []
        for sym in syms:
            try:
                await asyncio.to_thread(
                    stream_service.add, sym,
                    added_by="sector-rotation", notes="tracked sector/theme",
                )
                added.append(sym)
            except Exception as exc:  # noqa: BLE001 — best-effort
                logger.error("sector onboard: add %s failed: %s", sym, exc)

        tip_filled: list[str] = []
        if tip_fill and added:
            from app.services.ingest.schwab_tip_fill import SchwabTipFill

            tip = SchwabTipFill.from_settings()
            for sym in added:
                try:
                    res = await tip.tip_fill(sym)
                    if getattr(res, "error", None):
                        logger.warning("sector onboard: tip_fill %s: %s", sym, res.error)
                    else:
                        tip_filled.append(sym)
                except Exception as exc:  # noqa: BLE001
                    logger.error("sector onboard: tip_fill %s failed: %s", sym, exc)

        deep_filled: list[str] = []
        if deep_history and added:
            from datetime import datetime, timedelta, timezone

            from app.config import settings
            from app.services.equities.lake_to_ch_fill import fill_ch_from_lake_sync

            end = datetime.now(timezone.utc)
            start = end - timedelta(days=settings.rrg_lookback_days)
            for sym in added:
                try:
                    rows = await asyncio.to_thread(fill_ch_from_lake_sync, sym, start, end)
                    if rows:
                        deep_filled.append(sym)
                    logger.info("sector onboard: deep_history %s rows=%d", sym, rows)
                except Exception as exc:  # noqa: BLE001
                    logger.error("sector onboard: deep_history %s failed: %s", sym, exc)

        logger.info(
            "sector onboard: added=%d tip_filled=%d deep_filled=%d (%s)",
            len(added), len(tip_filled), len(deep_filled), ", ".join(added) or "—",
        )
        return {"added": added, "tip_filled": tip_filled, "deep_filled": deep_filled}
    finally:
        _inflight.difference_update(syms)


async def ensure_tracked_in_universe(
    *, tip_fill: bool = True, deep_history: bool = True
) -> dict:
    """Reconcile ALL tracked sector/theme symbols into the universe: onboard
    any that are missing. Idempotent — steady state (nothing new) is a cheap
    no-op. Use at startup."""
    from app.services.universe import get_active_universe

    tracked = tracked_symbols()
    active = set(await asyncio.to_thread(get_active_universe))
    missing = [s for s in tracked if s not in active]
    if not missing:
        logger.info("sector universe-sync: all %d tracked symbols already active", len(tracked))
        return {"tracked": len(tracked), "added": [], "tip_filled": [], "deep_filled": []}
    res = await _onboard(missing, tip_fill=tip_fill, deep_history=deep_history)
    res["tracked"] = len(tracked)
    return res


def _schedule(coro_factory, name: str) -> None:
    async def _runner() -> None:
        try:
            await coro_factory()
        except Exception as exc:  # noqa: BLE001 — never break the caller
            logger.error("sector %s failed: %s", name, exc, exc_info=True)

    try:
        asyncio.get_running_loop().create_task(_runner(), name=name)
    except RuntimeError:
        logger.warning("sector %s: no running loop; skipped", name)


def schedule_universe_sync() -> None:
    """Fire-and-forget the full reconcile (startup). Best-effort."""
    _schedule(ensure_tracked_in_universe, "universe_sync")


def schedule_onboard(symbols, *, tip_fill: bool = True, deep_history: bool = True) -> None:
    """Fire-and-forget onboarding of a SPECIFIC symbol list — e.g. a newly
    created theme's constituents. Targeted (no full reconcile) + deduped via the
    in-flight guard, so N theme creates don't storm the onboarding path."""
    syms = list(symbols)
    if not syms:
        return
    _schedule(lambda: _onboard(syms, tip_fill=tip_fill, deep_history=deep_history), "onboard")
