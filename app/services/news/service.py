"""News ingest — EDGAR filings → relevance filter → idempotent CH store.

Idempotency: `news_items` is a ReplacingMergeTree keyed on
(published_at, source, id) with `id` = EDGAR accession number, so re-ingesting
the same filing collapses to one row (higher version wins). Append-only writes;
no delete/filter in the hot path.

Deps are injected for testability (`edgar`, `ch_client`, `universe_resolver`);
`from_settings()` wires the real ones lazily.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from app.services.news.schemas import NewsIngestResult

logger = logging.getLogger(__name__)

# Column order for the CH insert — must match app/db/init.py::news_items.
_NEWS_COLUMNS = [
    "id", "published_at", "ingested_at", "source", "event_type", "symbol",
    "cik", "title", "url", "summary", "why_it_matters", "materiality",
    "sentiment", "enriched", "version",
]

_DEFAULT_FORM_TYPES = ("8-K", "4")


class NewsIngestService:
    def __init__(
        self,
        *,
        edgar=None,
        ch_client=None,
        universe_resolver: Optional[Callable[[], Sequence[str]]] = None,
    ) -> None:
        self._edgar = edgar
        self._ch = ch_client
        self._universe_resolver = universe_resolver

    @classmethod
    def from_settings(cls) -> "NewsIngestService":
        return cls()  # real deps resolved lazily on first use

    def _edgar_client(self):
        if self._edgar is None:
            from app.providers.edgar import EdgarClient
            self._edgar = EdgarClient.from_settings()
        return self._edgar

    def _ch_client(self):
        if self._ch is None:
            from app.db.client import get_client
            self._ch = get_client()
        return self._ch

    def _universe(self) -> set[str]:
        if self._universe_resolver is not None:
            symbols = self._universe_resolver()
        else:
            from app.services.universe.active_universe import resolve_universe_spec
            symbols = resolve_universe_spec("active")
        return {s.upper() for s in symbols}

    def ingest_filings(
        self,
        form_types: Sequence[str] = _DEFAULT_FORM_TYPES,
        count: int = 100,
    ) -> NewsIngestResult:
        """Pull latest EDGAR filings, keep those for active-universe symbols, and
        idempotently store them (unenriched). Returns counts for every outcome."""
        edgar = self._edgar_client()
        filings = edgar.latest_filings(form_types, count)
        universe = self._universe()

        now = datetime.now(timezone.utc)
        version = int(now.timestamp() * 1000)

        rows: list[list] = []
        skipped_no_ticker = 0
        skipped_not_universe = 0
        for f in filings:
            ticker = edgar.ticker_for_cik(f.cik) if f.cik else None
            if not ticker:
                skipped_no_ticker += 1
                continue
            ticker = ticker.upper()
            if ticker not in universe:
                skipped_not_universe += 1
                continue
            rows.append([
                f.accession,
                f.published_at or now,
                now,
                "edgar",
                f.form_type,
                ticker,
                f.cik,
                f.title,
                f.url,
                "",          # summary — filled by enrichment
                "",          # why_it_matters
                "unrated",   # materiality
                "",          # sentiment
                0,           # enriched
                version,
            ])

        stored = 0
        if rows:
            self._ch_client().insert("news_items", rows, column_names=_NEWS_COLUMNS)
            stored = len(rows)

        result = NewsIngestResult(
            fetched=len(filings),
            matched=len(rows),
            stored=stored,
            skipped_no_ticker=skipped_no_ticker,
            skipped_not_universe=skipped_not_universe,
        )
        logger.info(
            "news ingest: fetched=%d matched=%d stored=%d "
            "skipped_no_ticker=%d skipped_not_universe=%d "
            "(universe=%d, forms=%s)",
            result.fetched, result.matched, result.stored,
            result.skipped_no_ticker, result.skipped_not_universe,
            len(universe), ",".join(form_types),
        )
        return result
