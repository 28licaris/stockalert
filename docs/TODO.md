# TODO — parked / deferred work

Durable, version-controlled list of intentionally-deferred work (the
counterpart to the ephemeral in-app task chips). Bugs/flaky tests live in
[`ISSUES.md`](ISSUES.md); lake-read follow-ups in
[`lake_read_followups_remaining.md`](lake_read_followups_remaining.md).

## Billing
- **Activate Stripe billing.** The service is built + mounted
  (`/api/v1/customer/billing{,/checkout,/portal,/webhook}`) but returns 503
  `billing_not_configured` until configured. To activate: set `STRIPE_*` +
  `BILLING_*` env vars (`.env.example`), `IDENTITY_DATABASE_URL` +
  `AUTH_ENABLED=true`, start the identity Postgres + run its migrations, then
  verify the endpoints for an authed session. Code: `app/services/billing/`,
  `app/api/auth_dependencies.py::get_billing_service`.

## News → Alerts + Economic hub (design: [`news_alerts_spec.md`](news_alerts_spec.md))
Shipped + **activated 2026-06-28** (`NEWS_INGEST_ENABLED=true`): EDGAR filings
(relevance-filtered, AI-summarized) + FOMC (Fed RSS) + BLS (CPI/jobs/unemployment)
+ BEA (GDP/PCE) → CH `news_items` / `economic_data`, served to the News feed +
daily digest, the Economic page, and the AI via the `get_news` / `get_economic_data`
MCP tools. Free sources only; licensing-clean (link, never republish).
Deferred next steps:
- **Per-user scoping + tiering.** Today the feed/digest are global. Add per-user
  watchlist scoping (the relevance filter already resolves the active universe;
  extend to a customer's watchlist) + per-user alert state (read/unread, dedup)
  in Postgres, and plan-tier entitlements (watchlist-only vs all-markets, alerts/
  day, AI-summary access, real-time vs digest). Ties into Stripe billing above.
  The natural next step toward monetization.
- **Delivery channels.** v1 digest is in-app only. Add push/email/webhook
  delivery of the daily digest + high-materiality alerts (reuse
  `ALERT_WEBHOOK_URL` for a Slack/Discord-style post; email needs a provider).
- **More macro (optional).** Same `EconService` pattern: BLS PPI/retail sales,
  BEA personal income. Add a series to the catalog + (BEA) a NIPA table/line.
- **Phase 2 (optional).** RSS media headlines (link + AI summary, terms-checked
  sources only — no scraping); embedding-based dedup; sentiment surfaced in the
  News UI; earnings (needs a free/robust source — see calendar 2c).
- **Ops:** BLS is keyless (25 req/day) — add a free `BLS_API_KEY` for 500/day if
  polling more often. `BEA_API_KEY` required for GDP/PCE (set in `.env`).

## Market calendar — events (design: [`market_calendar_spec.md` §12a](market_calendar_spec.md))
Shipped (Phase 2a): computed OPEX/quad-witching + seeded FOMC on the calendar.
Free + production-robust only (no runtime HTML scraping).
- **2b — dividend/split ex-dates.** Sync corp-action ex-dates from the lake
  into the CH `market_events` table (already created): dividends from
  `equities.market_corp_actions` scoped to the active stream universe (avoid
  flooding the grid), splits from `equities.market_splits` market-wide.
  Idempotent sync (mirror `ch_reconcile`) + a CLI backfill. The read path
  (`app/services/market_events.py::ch_events`) + frontend already pick these
  up — additive, no API/UI change.
- **2c — earnings.** Deferred: no free + robust source (Schwab exposes none;
  Polygon earnings = paid Benzinga). The `market_events` model already
  supports `event_type='earnings'`. Revisit when a free source exists or
  billing funds a paid one (Finnhub / FMP).
- **Annual seed refresh.** `data/market_events_seed.json` holds FOMC decision
  dates (sourced to federalreserve.gov). Verify/extend annually; CPI/NFP/GDP/
  PCE can be added with the same shape once verified against BLS/BEA. Optional:
  a reviewed dev-time scrape-to-seed script (never a runtime dependency).

## Data / lake (ops)
- **Polygon flat-files subscription renewal.** `equities.polygon_raw` is
  frozen at 2026-06-12 until the (expired) Polygon/Massive flat-files
  entitlement is renewed; once renewed, re-run the equities Polygon nightly to
  fill the gap. Cold tier only — does not affect ClickHouse freshness (Schwab
  feeds the hot tier).
