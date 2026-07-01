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

from app.services.news.schemas import NewsEnrichResult, NewsIngestResult

logger = logging.getLogger(__name__)

# Column order for the CH insert — must match app/db/init.py::news_items.
_NEWS_COLUMNS = [
    "id", "published_at", "ingested_at", "source", "event_type", "symbol",
    "cik", "title", "url", "summary", "why_it_matters", "materiality",
    "sentiment", "enriched", "version",
]

_DEFAULT_FORM_TYPES = ("8-K", "4")

# Substrings that mark a persistent, batch-wide LLM outage (out of credits,
# quota, rate limit, billing) — as opposed to a one-off per-item failure. When
# one of these is seen we stop enriching for the cycle and leave filings stored
# unenriched, rather than burning a failing API call on every pending item.
_PROVIDER_UNAVAILABLE_MARKERS = (
    "credit balance",
    "insufficient",
    "quota",
    "rate limit",
    "rate_limit",
    "429",
    "billing",
    "too low",
    "overloaded",
)


def _is_provider_unavailable(ex: Exception) -> bool:
    msg = str(ex).lower()
    return any(m in msg for m in _PROVIDER_UNAVAILABLE_MARKERS)


class NewsIngestService:
    def __init__(
        self,
        *,
        edgar=None,
        ch_client=None,
        universe_resolver: Optional[Callable[[], Sequence[str]]] = None,
        enricher=None,
        fed=None,
    ) -> None:
        self._edgar = edgar
        self._ch = ch_client
        self._universe_resolver = universe_resolver
        self._enricher = enricher
        self._fed = fed

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

    def _enricher_obj(self):
        if self._enricher is None:
            from app.services.news.enrich import NewsEnricher
            self._enricher = NewsEnricher.from_settings()
        return self._enricher

    def _fed_client(self):
        if self._fed is None:
            from app.services.news.macro import FedClient
            self._fed = FedClient.from_settings()
        return self._fed

    def ingest_fomc(self) -> NewsIngestResult:
        """Pull FOMC statements/minutes from the Fed RSS and store them as
        market-wide (symbol='') news items, unenriched. Idempotent on item id.
        Always relevant — macro items skip the universe filter."""
        fed = self._fed_client()
        items = fed.latest_fomc()
        now = datetime.now(timezone.utc)
        version = int(now.timestamp() * 1000)
        rows = [
            [
                it.id, it.published_at or now, now, "fed", it.event_type,
                "", "", it.title, it.url,
                "", "", "unrated", "", 0, version,
            ]
            for it in items
        ]
        stored = 0
        if rows:
            self._ch_client().insert("news_items", rows, column_names=_NEWS_COLUMNS)
            stored = len(rows)
        logger.info("news ingest (fomc): fetched=%d stored=%d", len(items), stored)
        return NewsIngestResult(fetched=len(items), matched=len(rows), stored=stored)

    # Columns re-selected to rebuild a full row when rewriting with enrichment.
    _READ_COLUMNS = [
        "id", "published_at", "ingested_at", "source", "event_type",
        "symbol", "cik", "title", "url",
    ]

    def _read_unenriched(self, limit: int) -> list[dict]:
        sql = (
            "SELECT " + ", ".join(self._READ_COLUMNS) + " FROM news_items FINAL "
            "WHERE enriched = 0 ORDER BY published_at DESC LIMIT " + str(int(limit))
        )
        result = self._ch_client().query(sql)
        return [dict(zip(self._READ_COLUMNS, row)) for row in result.result_rows]

    def enrich_pending(self, limit: int = 50) -> NewsEnrichResult:
        """Summarize up to `limit` unenriched items: fetch the source doc, run
        the LLM, and rewrite the row with summary/why/materiality/sentiment +
        enriched=1 (ReplacingMergeTree updates in place). Per-item fetch/LLM
        failures are logged and skipped — they never drop the run."""
        rows = self._read_unenriched(limit)
        if not rows:
            logger.info("news enrich: nothing pending")
            return NewsEnrichResult(read=0, enriched=0, failed=0)

        edgar = self._edgar_client()
        enricher = self._enricher_obj()
        now = datetime.now(timezone.utc)
        version = int(now.timestamp() * 1000)

        out: list[list] = []
        failed = 0
        for r in rows:
            try:
                body = edgar.fetch_filing_text(r["url"]) if r.get("url") else ""
                e = enricher.enrich(
                    title=r.get("title", ""),
                    form_type=r.get("event_type", ""),
                    body_text=body,
                )
            except Exception as ex:  # noqa: BLE001 — degrade safely, don't drop the run
                # AI enrichment is best-effort: the filing is already stored and
                # visible unenriched. If the LLM provider is out of credits /
                # rate-limited (a persistent, batch-wide condition), stop the
                # batch instead of burning one failed call per item. The rows
                # stay enriched=0 and auto-resume enriching once it recovers.
                if _is_provider_unavailable(ex):
                    logger.warning(
                        "news enrich: LLM provider unavailable (%s) — leaving "
                        "%d item(s) unenriched this cycle; filings remain stored "
                        "and searchable, enrichment resumes when it recovers.",
                        ex, len(rows) - len(out),
                    )
                    break
                failed += 1
                logger.warning("news enrich: skipped id=%s (%s)", r.get("id"), ex)
                continue
            out.append([
                r["id"], r["published_at"], r["ingested_at"], r["source"],
                r["event_type"], r["symbol"], r["cik"], r["title"], r["url"],
                e.summary, e.why_it_matters, e.materiality, e.sentiment,
                1, version,
            ])

        if out:
            self._ch_client().insert("news_items", out, column_names=_NEWS_COLUMNS)

        result = NewsEnrichResult(read=len(rows), enriched=len(out), failed=failed)
        logger.info(
            "news enrich: read=%d enriched=%d failed=%d",
            result.read, result.enriched, result.failed,
        )
        return result

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
