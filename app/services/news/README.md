# news — official-record news feed

Free, licensing-clean news feed for the alerts product: SEC EDGAR filings (and,
later, govt economic/monetary releases), AI-summarized with a link to the source
document. We never store/republish source bodies — only our summary + the link.

Spec: [`docs/product/news_alerts.md`](../../../docs/product/news_alerts.md).

## Layout
- `schemas.py` — `NewsItem` (Pydantic, API/internal shape) + `NewsIngestResult`.
- `service.py` — `NewsIngestService`: EDGAR latest filings → relevance filter
  (active stream universe) → idempotent store in CH `news_items`. Deps injected
  (`edgar`, `ch_client`, `universe_resolver`); `from_settings()` wires reals.
- `tests/` — unit tests with fully injected fakes (no network / no CH).

## Pipeline (v1)
```
EdgarClient.latest_filings → CIK→ticker → keep active-universe symbols
  → append to news_items (unenriched) → [LLM enrichment stage] → API/feed
```

## Idempotency
`news_items` is a ReplacingMergeTree keyed on (published_at, source, id) with
`id` = EDGAR accession number. Re-ingesting the same filing collapses to one row
(higher version wins). Append-only; no delete/filter in the hot path
(see the bronze idempotency model).

## Not yet (see spec build order)
LLM enrichment (step 4), `/api/v1/news` (step 5), frontend feed (step 6),
scheduled job + MCP tool (step 7), macro sources + digest (v1.1).
