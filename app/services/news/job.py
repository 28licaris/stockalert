"""
News ingest job — batch cadence: poll EDGAR, store relevant filings, then
enrich a capped batch. `run_news_ingest_once` is the manual-trigger + per-cycle
unit (audited); `run_news_ingest_loop` is the background interval loop wired in
main_api. The service methods are sync (httpx/CH/SDK), so we offload them to a
thread to keep the event loop free. See docs/news_alerts_spec.md.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def run_news_ingest_once() -> dict:
    """One ingest+enrich cycle. Audited via ingestion_runs. Returns counts."""
    from app.config import settings
    from app.services.jobs.service import audit_run
    from app.services.news.service import NewsIngestService

    async with audit_run("news_ingest"):
        svc = NewsIngestService.from_settings()
        ingest = await asyncio.to_thread(svc.ingest_filings)

        # Macro (FOMC) — degrade safely: a Fed outage must not block filings.
        fomc_stored = 0
        try:
            fomc = await asyncio.to_thread(svc.ingest_fomc)
            fomc_stored = fomc.stored
        except Exception:  # noqa: BLE001 — boundary
            logger.exception("news_ingest: FOMC ingest failed; continuing")

        # Economic data (BLS) — degrade safely too.
        econ_releases = 0
        try:
            from app.services.news.econ import EconService

            econ = await asyncio.to_thread(EconService.from_settings().ingest)
            econ_releases = econ.releases
        except Exception:  # noqa: BLE001 — boundary
            logger.exception("news_ingest: BLS econ ingest failed; continuing")

        enrich = await asyncio.to_thread(svc.enrich_pending, settings.news_enrich_limit)
        logger.info(
            "news_ingest: filings_stored=%d (fetched=%d matched=%d) fomc_stored=%d "
            "econ_releases=%d | enriched=%d/%d failed=%d",
            ingest.stored, ingest.fetched, ingest.matched, fomc_stored,
            econ_releases, enrich.enriched, enrich.read, enrich.failed,
        )
        return {
            "fetched": ingest.fetched, "matched": ingest.matched,
            "stored": ingest.stored, "fomc_stored": fomc_stored,
            "econ_releases": econ_releases,
            "enriched": enrich.enriched, "enrich_failed": enrich.failed,
        }


async def run_news_ingest_loop() -> None:
    """Background loop — run a cycle every `news_poll_minutes`. A failing cycle
    is logged and the loop continues (never crashes the process)."""
    from app.config import settings

    interval = max(1, int(settings.news_poll_minutes)) * 60
    logger.info("news_ingest: loop started (every %d min)", settings.news_poll_minutes)
    while True:
        try:
            await run_news_ingest_once()
        except asyncio.CancelledError:
            logger.info("news_ingest: loop cancelled")
            raise
        except Exception:  # noqa: BLE001 — one bad cycle must not kill the loop
            logger.exception("news_ingest: cycle failed; continuing")
        await asyncio.sleep(interval)
