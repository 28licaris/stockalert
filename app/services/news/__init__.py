"""News service — official-record feed (SEC EDGAR + govt), AI-summarized.

See docs/news_alerts_spec.md. v1: EDGAR filings → relevance (active universe)
→ idempotent store in CH `news_items`. Enrichment (LLM summary) is a separate,
cost-capped stage layered on top.
"""
from app.services.news.schemas import NewsIngestResult, NewsItem
from app.services.news.service import NewsIngestService

__all__ = ["NewsIngestService", "NewsItem", "NewsIngestResult"]
