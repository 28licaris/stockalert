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


async def ensure_tracked_in_universe(*, tip_fill: bool = True) -> dict:
    """Add any tracked sector/theme symbol missing from the stream universe,
    then Schwab-tip-fill the newly-added ones so they're current and
    `schwab_universe` grows. Best-effort + idempotent — safe to call at every
    startup; never raises (a failure is logged, the rest proceed)."""
    from app.services.stream import stream_service
    from app.services.universe import get_active_universe

    tracked = tracked_symbols()
    active = set(await asyncio.to_thread(get_active_universe))
    missing = [s for s in tracked if s not in active]
    if not missing:
        logger.info("sector universe-sync: all %d tracked symbols already active", len(tracked))
        return {"tracked": len(tracked), "added": [], "tip_filled": []}

    added: list[str] = []
    for sym in missing:
        try:
            await asyncio.to_thread(
                stream_service.add, sym,
                added_by="sector-rotation", notes="tracked sector/theme",
            )
            added.append(sym)
        except Exception as exc:  # noqa: BLE001 — best-effort onboarding
            logger.error("sector universe-sync: add %s failed: %s", sym, exc)

    tip_filled: list[str] = []
    if tip_fill and added:
        from app.services.ingest.schwab_tip_fill import SchwabTipFill

        tip = SchwabTipFill.from_settings()
        for sym in added:
            try:
                res = await tip.tip_fill(sym)
                if getattr(res, "error", None):
                    logger.warning("sector universe-sync: tip_fill %s: %s", sym, res.error)
                else:
                    tip_filled.append(sym)
            except Exception as exc:  # noqa: BLE001
                logger.error("sector universe-sync: tip_fill %s failed: %s", sym, exc)

    logger.info(
        "sector universe-sync: tracked=%d added=%d tip_filled=%d (added: %s)",
        len(tracked), len(added), len(tip_filled), ", ".join(added) or "—",
    )
    return {"tracked": len(tracked), "added": added, "tip_filled": tip_filled}


def schedule_universe_sync() -> None:
    """Fire-and-forget the reconcile on the running event loop. Wraps it so a
    failure can never take down app startup. Call once from the app lifespan
    after the stream service is up."""

    async def _runner() -> None:
        try:
            await ensure_tracked_in_universe()
        except Exception as exc:  # noqa: BLE001 — never break startup
            logger.error("sector universe-sync: reconcile failed: %s", exc, exc_info=True)

    try:
        asyncio.get_running_loop().create_task(_runner(), name="sector_universe_sync")
    except RuntimeError:
        logger.warning("sector universe-sync: no running loop; reconcile skipped")
