# Spec — News → Alerts (official-record feed)

Status: **APPROVED — implementing v1** (2026-06-27)

## 1. Product

A subscription feature: a **per-symbol news feed + alerts** built from **free,
official, public sources** — SEC EDGAR filings + US government economic/monetary
releases. Each item is **summarized by Claude with a link to the source
document**. We never republish source bodies → licensing-clean (we sell the
triage + plain-English read, not the document).

Use cases, one data set: (a) a **feed** page, (b) per-symbol **alerts/digest**,
(c) a **News tab** on the symbol page, (d) an **MCP tool** so the assistant can
answer "any news on NVDA?".

## 2. Decisions (worked out 2026-06-27)
- **Scope:** per-symbol — a user's watchlist + positions. (Macro events are
  market-wide, shown to everyone.)
- **Sources (free, API/feed — NOT scraping):**
  - **SEC EDGAR** — 8-K (material events), Form 4 (insider), 10-K/10-Q
    (earnings), S-1 (IPO). Via the official latest-filings Atom feed +
    `data.sec.gov` submissions JSON + `company_tickers.json` (CIK↔ticker).
  - **Macro (later in v1 / fast-follow):** Fed FOMC (RSS), BLS/BEA (JSON APIs).
  - **Media via RSS (Phase 2):** headline + link + summary only.
- **Acquisition:** official APIs/feeds + fetch the specific referenced document.
  `User-Agent` header + ≤10 req/s. No scraping, no key, no cost.
- **LLM:** cost-capped — only **watchlist-relevant** filings are enriched;
  cheap triage → escalate material items for summary + "why it matters."
- **Cadence:** batch poll (feed near-fresh) + periodic **digest** of new
  material items.
- **Display:** badge (form type) + materiality + timestamp + AI summary + "why
  it matters" + link to the official doc.

## 3. Alerting logic (defaults — adjustable)
- **Feed:** every watchlist-relevant filing/event appears in the feed.
- **Alert (digest):** an item alerts when LLM **materiality ≥ `high`**
  (default; user-configurable threshold later). Macro high-materiality
  (e.g. FOMC decision) alerts everyone.
- **Dedup:** idempotent on EDGAR **accession number** (one filing = one item);
  per-user alert dedup so a story pings once. Cross-source story dedup = Ph2.
- **Frequency:** v1 = in-app feed (live) + a daily digest of the day's
  material items. Push/email = Phase 2.

## 4. Architecture
```
poll EDGAR feed → normalize → relevance filter (watchlist) → enrich(LLM, capped)
→ store → feed/API + digest
```
- **Provider:** `app/providers/edgar.py` — `EdgarClient`: `latest_filings(form_types)`
  (parse the Atom feed), `cik_for_ticker()` / `ticker_for_cik()` (cached map),
  `fetch_filing_text(url)` (the primary doc, for the LLM). `from_settings()`.
- **Service:** `app/services/news/` (service-module template) — ingest
  orchestration, relevance filter (active stream universe / watchlists),
  enrichment via the `assistant`/Anthropic path (cost-capped + cached), idempotent
  upsert. Pure where possible.
- **Enrichment:** Claude → `{event_type, materiality(low|med|high), sentiment,
  summary, why_it_matters}`. Cheap-model triage first; only material items get
  the fuller summary call. Content-hash cache; per-run + per-day token cap.
- **Reuse:** `assistant` (LLM + cost tracking), `live` monitor/alert framework
  (digest delivery), watchlists/stream universe (relevance), identity/billing
  (tiering).

## 5. Storage — ClickHouse `news_items`
```
id             String        -- EDGAR accession (or source uid); dedup key
published_at   DateTime64(3,'UTC')
ingested_at    DateTime64(3,'UTC')
source         LowCardinality(String)   -- 'edgar' | 'fed' | 'bls' | ...
event_type     LowCardinality(String)   -- '8-K' | 'form4' | '10-Q' | 'fomc' | ...
symbol         LowCardinality(String)   -- '' for macro
cik            String DEFAULT ''
title          String
url            String                    -- link to the official document
summary        String DEFAULT ''         -- AI; '' until enriched
why_it_matters String DEFAULT ''
materiality    LowCardinality(String) DEFAULT 'unrated'
sentiment      LowCardinality(String) DEFAULT ''
enriched       UInt8 DEFAULT 0
version        UInt64
ENGINE ReplacingMergeTree(version)
PARTITION BY toYYYYMM(published_at)
ORDER BY (published_at, source, id)
```
Per-user alert/read state → Postgres (Phase 2 with delivery). Embedding dedup → Ph2.

## 6. API + frontend
- `app/api/routes_news.py` — `GET /api/v1/news?symbols=&types=&since=&limit=`
  → list of news items (Pydantic `NewsItem`). Symbol filter keeps market-wide
  (macro) items.
- Frontend `routes/news.tsx` — the feed (mockup already designed): scope toggle,
  type chips, item cards with badge/materiality/summary/why-it-matters/source
  link. Nav entry + `page.news` flag. MCP tool `get_news`.

## 7. Cost & reliability (paid SLA)
- Only watchlist-relevant filings hit the LLM; triage drops the rest; cache by
  content hash; hard per-run + per-day token budget (assistant cost tracking).
- Idempotent ingest (accession id) + per-user alert dedup → never double-alert.
- Every stage degrades safely (a source/LLM failure logs + skips, never drops
  the run). No fragile scraping (EDGAR is a stable API).

## 8. Monetization hooks
Tier gates: watchlist-only (free) vs all-markets; alerts/day; materiality
threshold; AI summary/sentiment access; digest vs real-time; push/email. Ties
into the deferred billing entitlements.

## 9. Phasing
- **v1 (this build):** EDGAR ingest (8-K + Form 4) → relevance → capped LLM
  enrichment → `news_items` → `/api/v1/news` + News feed page + MCP tool.
- **v1.1:** macro (FOMC/BLS/BEA) into the same feed; daily digest delivery.
- **Phase 2:** RSS media (link-only), real-time push/email, embedding dedup,
  per-user alert state + tier gating, sentiment.

## 10. Build order (v1)
1. `EdgarClient` (provider) + unit tests (parse Atom, CIK map) — no network in tests.
2. CH `news_items` table (init_schema).
3. `news` service: ingest + relevance + idempotent store + tests.
4. LLM enrichment (capped) wired through the assistant path + tests (mocked LLM).
5. `routes_news` + Pydantic + tests.
6. Frontend News page + nav/flag + codegen + build.
7. Scheduled ingest job (like the nightly refreshes) + MCP tool.
