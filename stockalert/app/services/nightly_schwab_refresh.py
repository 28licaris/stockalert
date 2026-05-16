"""
Nightly Schwab REST → bronze.schwab_minute refresh.

Background asyncio loop: sleep until ``SCHWAB_NIGHTLY_RUN_HOUR_UTC``,
then pull **yesterday's** 1-minute bars from Schwab pricehistory for
``SCHWAB_NIGHTLY_SYMBOLS`` (default: seed 100) and append them to
``bronze.schwab_minute``.

Gating: ``SCHWAB_NIGHTLY_ENABLED``, non-empty ``STOCK_LAKE_BUCKET``, and
working Schwab API credentials (CLIENT_ID/SECRET + refresh token).

Default run hour is 22 UTC (= 3 PM Arizona, ~30 min after NYSE close
even on DST days) so the prior trading day's bars are complete.

The actual per-symbol pull logic lives in
``scripts/schwab_bronze_backfill.run_backfill`` — this module is a
thin async wrapper that calls it once per day on a schedule.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from app.config import settings
from app.data.seed_universe import SEED_SYMBOLS

logger = logging.getLogger(__name__)


SCHWAB_NIGHTLY_DEFAULT_HOUR_UTC = 22  # ~3 PM Arizona / ~30 min after NYSE close
SCHWAB_NIGHTLY_SLEEP_BETWEEN_SYMBOLS = 0.05


def _seconds_until_next_run(hour_utc: int, *, now: datetime | None = None) -> float:
    """Same semantics as nightly_lake_refresh._seconds_until_next_run."""
    now = now or datetime.now(timezone.utc)
    h = max(0, min(23, int(hour_utc)))
    target = now.replace(hour=h, minute=0, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def _resolve_symbols(spec: str) -> list[str]:
    s = (spec or "").strip().lower()
    if s in ("seed", "seed-100", "seed_100", ""):
        return list(SEED_SYMBOLS)
    return [tok.strip().upper() for tok in spec.split(",") if tok.strip()]


def _schwab_nightly_gated() -> tuple[bool, str]:
    """Return (gated_off, reason). gated_off=True means we should not run."""
    if not getattr(settings, "schwab_nightly_enabled", False):
        return True, "SCHWAB_NIGHTLY_ENABLED=false"
    if not (settings.stock_lake_bucket or "").strip():
        return True, "STOCK_LAKE_BUCKET is empty"
    if not (settings.schwab_client_id or "").strip():
        return True, "SCHWAB_CLIENT_ID missing"
    if not (settings.schwab_client_secret or "").strip():
        return True, "SCHWAB_CLIENT_SECRET missing"
    if not settings.get_schwab_refresh_token():
        return True, "Schwab refresh token missing"
    return False, ""


async def refresh_schwab_bronze_yesterday(
    *,
    target: date | None = None,
) -> dict:
    """
    Schwab pricehistory → bronze.schwab_minute.

    Behavior:
      - ``target`` set    → process exactly that date (CLI / tests).
      - ``target`` None   → AUTO-CATCHUP. Find the most recent date
                            already in bronze.schwab_minute and fill
                            every weekday from then through yesterday.
                            If bronze is empty in the last 14 days,
                            processes yesterday only (cold-start
                            fallback to avoid runaway backfills).

    Each day delegates to ``scripts.schwab_bronze_backfill.run_backfill``
    which is idempotent; reruns are safe.
    """
    gated, why = _schwab_nightly_gated()
    if gated:
        logger.info("nightly_schwab_refresh: skipping — %s", why)
        return {"skipped": True, "reason": why}

    sym_spec = getattr(settings, "schwab_nightly_symbols", "seed")
    symbols = _resolve_symbols(sym_spec)

    # Determine which dates to process.
    if target is not None:
        dates_to_process: list[date] = [target]
    else:
        try:
            from app.services.bronze import (
                ensure_bronze_schwab_minute,
                latest_bronze_date,
                missing_weekdays,
            )
            bronze_table = ensure_bronze_schwab_minute()
            latest = latest_bronze_date(bronze_table)
            if latest is None:
                # Empty table or no data in lookback — cold-start seed.
                yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
                dates_to_process = [yesterday] if yesterday.weekday() < 5 else []
                logger.info(
                    "nightly_schwab_refresh: bronze empty in lookback window — "
                    "cold-start with yesterday only"
                )
            else:
                dates_to_process = missing_weekdays(bronze_table)
        except Exception as e:
            logger.warning(
                "nightly_schwab_refresh: gap detection failed (%s); "
                "falling back to yesterday-only",
                e,
            )
            from app.services.bronze import yesterday_et
            yesterday = yesterday_et()
            dates_to_process = [yesterday] if yesterday.weekday() < 5 else []

        if not dates_to_process:
            logger.info(
                "nightly_schwab_refresh: no gaps to fill — bronze is up to date"
            )
            return {"skipped": True, "reason": "no gaps; bronze up to date"}
        logger.info(
            "nightly_schwab_refresh: catch-up covers %d day(s): %s",
            len(dates_to_process),
            [d.isoformat() for d in dates_to_process],
        )

    # Lazy import — keeps top-level import side-effect-free.
    from scripts.schwab_bronze_backfill import run_backfill

    per_day: list[dict] = []
    for d in dates_to_process:
        logger.info(
            "nightly_schwab_refresh: starting %s for %d symbol(s)",
            d, len(symbols),
        )
        rc = await run_backfill(
            symbols=symbols,
            start=d,
            end=d,
            sleep_seconds=SCHWAB_NIGHTLY_SLEEP_BETWEEN_SYMBOLS,
            include_weekends=False,
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


async def run_schwab_refresh_loop() -> None:
    """Forever loop: sleep until configured run hour, then refresh."""
    gated, why = _schwab_nightly_gated()
    if gated:
        logger.info("nightly_schwab_refresh: loop not started — %s", why)
        return

    hour_utc = int(
        getattr(settings, "schwab_nightly_run_hour_utc", SCHWAB_NIGHTLY_DEFAULT_HOUR_UTC)
    )
    logger.info(
        "nightly_schwab_refresh: loop armed (run hour %02d:00 UTC)", hour_utc,
    )

    while True:
        try:
            wait_s = _seconds_until_next_run(hour_utc)
            logger.info(
                "nightly_schwab_refresh: sleeping %.0fs until next run",
                wait_s,
            )
            await asyncio.sleep(wait_s)
            await refresh_schwab_bronze_yesterday()
        except asyncio.CancelledError:
            logger.info("nightly_schwab_refresh: loop cancelled")
            raise
        except Exception as e:
            logger.exception("nightly_schwab_refresh: unexpected error: %s", e)
            # Sleep before retrying to avoid hot-loop on a persistent failure.
            await asyncio.sleep(300)
