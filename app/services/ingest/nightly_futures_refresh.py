"""
Nightly Schwab REST → ``futures.schwab_futures`` refresh.

Background asyncio loop: sleep until ``FUTURES_NIGHTLY_RUN_HOUR_UTC``,
then pull the missing CME session days' 1-minute bars for
``FUTURES_NIGHTLY_SYMBOLS`` (default: the active ``futures_universe`` ∪
seed roots) and append them to ``futures.schwab_futures``.

Mirror of ``nightly_schwab_refresh`` (equities) — gating + auto-catchup
are identical; only the lake table, the symbol resolver, and the session
calendar (Sun-Fri, via ``app.services.futures.gaps``) differ. The
per-day pull logic lives in ``scripts.futures_history_backfill.run_backfill``;
this module is a thin async wrapper that schedules it.

Gating: ``FUTURES_NIGHTLY_ENABLED``, non-empty ``STOCK_LAKE_BUCKET``, and
working Schwab API credentials (CLIENT_ID/SECRET + refresh token).

Default run hour is 22 UTC (same as the equities nightly) — the prior
CME session is complete well before then.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from app.config import settings

logger = logging.getLogger(__name__)


FUTURES_NIGHTLY_DEFAULT_HOUR_UTC = 22
FUTURES_NIGHTLY_SLEEP_BETWEEN_SYMBOLS = 0.05


def _seconds_until_next_run(hour_utc: int, *, now: datetime | None = None) -> float:
    """Same semantics as nightly_schwab_refresh._seconds_until_next_run."""
    now = now or datetime.now(timezone.utc)
    h = max(0, min(23, int(hour_utc)))
    target = now.replace(hour=h, minute=0, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def _futures_nightly_gated() -> tuple[bool, str]:
    """Return (gated_off, reason). gated_off=True means we should not run."""
    if not getattr(settings, "futures_nightly_enabled", False):
        return True, "FUTURES_NIGHTLY_ENABLED=false"
    if not (settings.stock_lake_bucket or "").strip():
        return True, "STOCK_LAKE_BUCKET is empty"
    if not (settings.schwab_client_id or "").strip():
        return True, "SCHWAB_CLIENT_ID missing"
    if not (settings.schwab_client_secret or "").strip():
        return True, "SCHWAB_CLIENT_SECRET missing"
    if not settings.get_schwab_refresh_token():
        return True, "Schwab refresh token missing"
    return False, ""


async def refresh_futures_yesterday(*, target: date | None = None) -> dict:
    """
    Schwab pricehistory → ``futures.schwab_futures``.

    Behavior:
      - ``target`` set    → process exactly that date (CLI / tests).
      - ``target`` None   → AUTO-CATCHUP. Find the most recent session
                            already in ``futures.schwab_futures`` and fill
                            every CME session day (Sun-Fri) from then
                            through yesterday. Cold-start fallback if the
                            table is empty in the lookback window.

    Each day delegates to ``scripts.futures_history_backfill.run_backfill``
    which is idempotent at the gap-detection layer; reruns are safe.
    """
    gated, why = _futures_nightly_gated()
    if gated:
        logger.info("nightly_futures_refresh: skipping — %s", why)
        return {"skipped": True, "reason": why}

    from app.services.futures.universe import resolve_futures_spec
    sym_spec = getattr(settings, "futures_nightly_symbols", "active")
    symbols = resolve_futures_spec(sym_spec)

    # Determine which dates to process.
    if target is not None:
        dates_to_process: list[date] = [target]
    else:
        try:
            from app.services.futures.gaps import (
                missing_futures_sessions,
            )
            from app.services.futures.tables import ensure_schwab_futures
            table = ensure_schwab_futures()
            dates_to_process = missing_futures_sessions(table)
        except Exception as e:
            logger.warning(
                "nightly_futures_refresh: gap detection failed (%s); "
                "falling back to yesterday-only",
                e,
            )
            from app.services.futures.gaps import is_futures_session_day, yesterday_et
            y = yesterday_et()
            dates_to_process = [y] if is_futures_session_day(y) else []

        if not dates_to_process:
            logger.info(
                "nightly_futures_refresh: no gaps to fill — "
                "futures.schwab_futures is up to date"
            )
            return {"skipped": True, "reason": "no gaps; futures.schwab_futures up to date"}
        logger.info(
            "nightly_futures_refresh: catch-up covers %d day(s): %s",
            len(dates_to_process),
            [d.isoformat() for d in dates_to_process],
        )

    from scripts.futures_history_backfill import run_backfill

    per_day: list[dict] = []
    for d in dates_to_process:
        logger.info(
            "nightly_futures_refresh: starting %s for %d root(s)",
            d, len(symbols),
        )
        rc = await run_backfill(
            symbols=symbols,
            start=d,
            end=d,
            sleep_seconds=FUTURES_NIGHTLY_SLEEP_BETWEEN_SYMBOLS,
            dry_run=False,
        )
        per_day.append({"date": d.isoformat(), "symbols": len(symbols), "exit_code": rc})

    if target is not None:
        return per_day[0] if per_day else {"skipped": True, "reason": "no date processed"}
    return {
        "auto_catchup": True,
        "days_processed": len(per_day),
        "dates": [r["date"] for r in per_day],
        "results": per_day,
    }


async def run_futures_refresh_loop() -> None:
    """Forever loop: sleep until configured run hour, then refresh."""
    gated, why = _futures_nightly_gated()
    if gated:
        logger.info("nightly_futures_refresh: loop not started — %s", why)
        return

    hour_utc = int(
        getattr(settings, "futures_nightly_run_hour_utc", FUTURES_NIGHTLY_DEFAULT_HOUR_UTC)
    )
    logger.info(
        "nightly_futures_refresh: loop armed (run hour %02d:00 UTC)", hour_utc,
    )

    while True:
        try:
            wait_s = _seconds_until_next_run(hour_utc)
            logger.info(
                "nightly_futures_refresh: sleeping %.0fs until next run",
                wait_s,
            )
            await asyncio.sleep(wait_s)
            await refresh_futures_yesterday()
        except asyncio.CancelledError:
            logger.info("nightly_futures_refresh: loop cancelled")
            raise
        except Exception as e:
            logger.exception("nightly_futures_refresh: unexpected error: %s", e)
            await asyncio.sleep(300)
