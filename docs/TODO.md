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

## GEX / dealer-flow research (chapter opened 2026-07-02 — GEX-0 in strategy_rnd_findings.md)
Collection flywheel is LIVE: `OPTIONS_SNAPSHOT_ENABLED=true`, 40-name
pre-registered universe, 15-min cadence, 30 strikes → `options.*` lake
(first cycle verified: 34/40 underlyings, 23,777 GEX rows; per-symbol
transient fetch errors self-heal next cycle). Snapshots collect ONLY while
uvicorn runs — weekday uptime = sample completeness. Historical backfill is
impossible honestly (no OI outside Polygon's 403 tier); the forward dataset
IS the moat.

Next steps, in order:
1. **Methodology validation (before ANY study reads the numbers):** audit
   sign conventions (dealer-long-call assumption), greeks source (Schwab vs
   computed), flip-point/wall math in `app/services/options/` against raw
   chains; add golden tests. The EXP-34 lesson applied preemptively —
   validate the instrument first.
2. **Collection health check (~daily for the first week):** ingestion_runs
   `options_snapshot_refresh` + per-cycle underlying counts; confirm all 40
   names appear across cycles; watch OPEX-week (monthly expiry) captures.
3. **GEX-1 wall-pinning study (~2 weeks of data):** cross-sectional — does
   |spot − nearest wall| predict intraday range compression near walls?
   Cross-section over 40 names substitutes for calendar depth.
4. **GEX-2 regime study (~2-3 months of data):** net-GEX sign vs next-day
   realized vol + trend-vs-chop; OPEX-week effects. Pre-register criteria
   before running.
5. **GEX-3:** GEX-regime conditioning of the swing system + alerts; fold
   into the paper falsification loop.
Related standing item: Schwab stream expansion (momentum-candidate superset
+ 1m level-watching) serves swing alerts, the day-trading corpus, and
intraday GEX-vs-price studies.

## Strategy R&D — MCPT extensions (parked)
- **Fitness-across-permutations (Build Alpha "pre-simulation" use).** When
  parameter searching resumes (walkforward_search / improve_strategy),
  average the selection fitness across the real history AND K permuted
  variants (`permutation.permute_frames`) so parameters fit to the one
  historical sequence are penalized during selection, not just caught
  after. Only worth wiring if a family survives the EXP-39 battery and
  earns further parameter work. Source: buildalpha.com/monte-carlo-permutation.

## Reporting — quantstats tearsheets (parked until the paper record has meat)
Add `quantstats` (prefer the maintained `quantstats-lumi` fork, pinned) as a
REPORTING-ONLY dependency: monthly HTML tearsheets for the FOMC paper
strategies (rolling Sharpe, drawdown table, benchmark-relative) attached to
the findings doc, and a cross-check on the sim engine's metric math. Engine
metrics stay canonical; quantstats renders, never validates — it has no
nulls/FDR and must not creep into the promotion gate. Trigger: first
monthly paper-record review (~Aug 2026, after the 2026-07-29 FOMC trades).

## Vol Premium — remaining build
Full status and cold-start notes:
[`handoff_ppl_vol_premium.md`](handoff_ppl_vol_premium.md). Merged to main
2026-11-03 (PRs #28, #30-33).

- ~~**Historical premium percentile.**~~ **DONE 2026-11-03.**
  `vol_premium_history`: 81,015 rows, 40 symbols, 2016-01 → 2026-08, built by
  `scripts/build_vol_premium_history.py`. GJR-GARCH refit monthly with the
  variance rolled daily; no look-ahead, tested by rewriting the future after a
  cut point and asserting no past forecast moves. Wired through service + CLI.
- **API + page.** `routes_vol_premium.py` + `vol-premium.tsx`, per spec §5.
  **The only thing left before this is a product** — the engine, the baseline
  and the percentile all exist, but nothing in the app can reach them; the sole
  entry point is `scripts/vol_premium_screen.py`. Needs a scheduled job writing
  to ClickHouse first (a GARCH fit per symbol is ~0.2s, so 40 symbols is ~8s:
  fine for a job, unacceptable on a page load).
- **Backtest the premium.** Ten years of history makes it possible now. MCPT,
  pre-registered in `research_hypotheses.md`, before any return claim.
- **Reparse the lake's canonical option quotes.** Deprioritized: ThetaData
  covers history better and the rebuild is slow. Raw payloads are intact so it
  stays recoverable. NOTE: do it as a full-table rebuild, never an upsert —
  PyIceberg falls back to copy-on-write and rewrites every matching file
  (>10 min for a single symbol).

## Nightly 1-minute backfill for the 115 stream-capped symbols
Schwab streams only **300** equity symbols for this account — a hard
subscription cap, confirmed 2026-11-03, not fixable in code (see
`schwab-300-symbol-account-cap` in ISSUES). The other 115 of the 415-symbol
universe receive **nothing** live.

**REST is not subject to that cap, and returns full 1-minute history** —
verified on symbols that are silent on the stream:

```
SBUX 487 bars · WMT 819 · SHOP 572 · XLF 535 · SCHW 412   (Nov 2-3, 1m, via REST)
```

So the shape is: stream the 300 live, backfill the other 115 nightly at the
**same 1-minute resolution**. All 415 end up with complete intraday history;
the 115 simply lag by a day, which costs real-time alerting on them and costs
research nothing.

- **Not happening today.** Checked 2026-11-02, 10-30, 10-29: **zero** 1m bars
  for any of the 115. This is new work, not a config flag.
- **The machinery exists.** `scripts/schwab_history_backfill.py` already pulls
  REST 1-minute bars into `equities.schwab_universe`; it is simply not run
  against the symbols that need it. Scope is roughly: pick the universe
  symbols NOT currently delivering, run that puller for them nightly, then
  `rebuild_ch_from_lake` / `fill_ch_from_lake` for the CH side.
- **Load-bearing, so monitor it.** Schwab's 1-minute history reaches back only
  ~48 days. Miss this job for more than ~7 weeks and that window is gone
  permanently — exactly how the 2026-08→11 options gap became unrecoverable.
- **Do it in the AWS session.** Where this runs and how it is alerted is a
  deployment question; building it before that means building it twice.
- **Related and more important than the backfill itself:** the health panel
  must compare *delivered* against *subscribed*. The cap hid for three months
  because `/stream/status` reported 424 subscribed while 131 delivered nothing,
  and nothing anywhere compared the two numbers.
