"""Pydantic contracts for the news service (API + internal shapes)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel


class NewsItem(BaseModel):
    """One feed item — a filing/event with an optional AI summary + source link.

    `summary`/`why_it_matters`/`materiality`/`sentiment` are filled by the LLM
    enrichment stage; they stay at their defaults (``enriched=False``) until then.
    We never carry the source body — only our summary + the `url` link.
    """

    id: str
    published_at: datetime
    source: str
    event_type: str
    symbol: str = ""           # '' for market-wide / macro
    cik: str = ""
    title: str
    url: str
    summary: str = ""
    why_it_matters: str = ""
    materiality: str = "unrated"
    sentiment: str = ""
    enriched: bool = False


@dataclass(frozen=True)
class NewsIngestResult:
    """Outcome of one ingest run — every count surfaced (no silent failures)."""

    fetched: int = 0               # filings returned by the source
    matched: int = 0               # relevant to the active universe
    stored: int = 0                # rows written to news_items
    skipped_no_ticker: int = 0     # filing CIK had no ticker mapping
    skipped_not_universe: int = 0  # ticker not in the active universe


@dataclass(frozen=True)
class NewsEnrichResult:
    """Outcome of one enrichment run — every count surfaced."""

    read: int = 0       # unenriched rows pulled
    enriched: int = 0   # rows summarized + rewritten with enriched=1
    failed: int = 0     # rows that errored (fetch/LLM) and were skipped this run
