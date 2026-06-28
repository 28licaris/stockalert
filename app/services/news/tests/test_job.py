"""Unit test for the news ingest job — audit + service mocked."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import app.services.news.job as job
from app.services.news.schemas import NewsEnrichResult, NewsIngestResult


class _FakeSvc:
    def __init__(self):
        self.enrich_limit = None

    def ingest_filings(self):
        return NewsIngestResult(fetched=5, matched=2, stored=2)

    def enrich_pending(self, limit):
        self.enrich_limit = limit
        return NewsEnrichResult(read=2, enriched=2, failed=0)


def test_run_once_ingests_then_enriches(monkeypatch):
    @asynccontextmanager
    async def _no_audit(name):
        yield

    fake = _FakeSvc()
    monkeypatch.setattr("app.services.jobs.service.audit_run", _no_audit)
    monkeypatch.setattr(
        "app.services.news.service.NewsIngestService.from_settings",
        lambda: fake,
    )

    res = asyncio.run(job.run_news_ingest_once())

    assert res["stored"] == 2
    assert res["matched"] == 2
    assert res["enriched"] == 2
    assert res["enrich_failed"] == 0
    # enrich limit comes from settings.news_enrich_limit (the per-run cost cap).
    assert fake.enrich_limit is not None
