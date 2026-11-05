# TODO — parked / deferred work

Durable, version-controlled list of intentionally-deferred work (the
counterpart to the ephemeral in-app task chips). Bugs and flaky tests live in
[`ISSUES.md`](ISSUES.md).

## Billing
- **Activate Stripe billing.** The service is built + mounted
  (`/api/v1/customer/billing{,/checkout,/portal,/webhook}`) but returns 503
  `billing_not_configured` until configured. To activate: set `STRIPE_*` +
  `BILLING_*` env vars (`.env.example`), `IDENTITY_DATABASE_URL` +
  `AUTH_ENABLED=true`, start the identity Postgres + run its migrations, then
  verify the endpoints for an authed session. Code: `app/services/billing/`,
  `app/api/auth_dependencies.py::get_billing_service`.

## News → Alerts + Economic hub (design: [`product/news_alerts.md`](product/news_alerts.md))
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

## Market calendar — events (design: [`data/market_calendar.md` §12a](data/market_calendar.md))
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

## GEX / dealer-flow research (chapter opened 2026-07-02 — GEX-0 in research/findings.md)
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
Data state, validated baseline and known gaps:
[`options_vol_premium_state.md`](trading/options_vol_premium_state.md). Merged to main
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
  pre-registered in `research/hypotheses.md`, before any return claim.
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

## Identity / auth — optional enhancements
Core flows are **production-ready** (signup, login, logout, session isolation,
tenant scoping, audit trail). Local dev must use `scripts/dev/run_auth_api.sh`
— see [`architecture/runbook.md`](operations/runbook.md#auth-enabled-local-development)
for why a bare uvicorn breaks auth silently. Module:
[`app/services/identity/README.md`](../app/services/identity/README.md).

- **MFA enforcement** (~2-3h, high value). Wire TOTP and SMS OTP into the login
  flow — the service stubs already exist in
  `app/services/identity/mfa_service.py`. Extend the login endpoint in
  `routes_customer_auth.py` to check a user's `mfa_required` flag and issue a
  challenge, wire `verify_totp()` / `verify_sms_otp()`, enable MFA device
  tracking in the Cognito user pool, and add the frontend code-entry dialog.
  Required for compliance-driven customers.
- **Provider credential management UI** (~3-4h, high value). Let users
  link/unlink their own Schwab and Polygon credentials per account instead of
  one hardcoded set in `.env`. `provider_session.py` has the foundation; needs
  `/providers/connect` + `/providers/revoke` endpoints, a "Connected Accounts"
  settings page, and a storage decision (AWS Secrets Manager vs encrypted DB
  column — pick one).
- **Link Stripe customer to Cognito user** (~1h). On signup, create the Stripe
  customer and store `stripe_customer_id` on the `users` row, so billing events
  tie to accounts. Prerequisite for the Billing activation above.
- **Audit dashboard** (low priority). UI over the existing security-event trail
  (login attempts, session creation, account changes) per account.

## Futures lake — optional enhancements
Production-ready for backtesting. Pipeline and nightly behaviour:
[`architecture/README.md`](architecture/README.md#4-the-futures-pipeline-mirror--raw--continuous).

- **Parse session-aggregates** (~1-2h, low priority). The mirror already
  captures Polygon's `session_aggs_v1/` (daily bars) alongside
  `minute_aggs_v1/`, but only the minute → raw → continuous path was built. Add
  `scripts/polygon_futures_parse_session_daily.py` parallel to
  `polygon_futures_parse_raw.py`. No new Polygon calls needed. Niche — Schwab
  nightly already covers recent days.
- **Move the nightly rebuild to CodeBuild** (~30 min + IAM approval). Roll-day
  rebuilds (~1-2 min per root) currently run in the API event loop via
  `asyncio.to_thread`. Blocked on an IAM grant:
  `stockalert-codebuild-silver-role` needs `glue:CreateDatabase` +
  `glue:PutTable` on the `futures` namespace (it has equities only).
  `scripts/codebuild/buildspec_futures_parse_raw.yml` is the template.
- **Archive the flat-file mirror** (~15 min, cost only). The 88 GB
  `polygon_flatfiles_mirror/` plus trades files are write-once and read-never
  after parsing. An S3 lifecycle policy (Intelligent-Tiering at 30d, Glacier at
  90d) saves ~$2-3/month with no correctness impact.

## Frontend: 60 hand-declared types duplicate generated OpenAPI schemas
`frontend/src/api/queries.ts` declares 85 shapes, **60 of which already exist**
in `src/api/types.gen.ts` as `components["schemas"][...]` — effectively a
parallel type system. Nothing is broken today; the hazard is **drift**: the
backend changes a shape, TypeScript still compiles against the hand-written
copy, and it fails at runtime instead of at build time.

**Deliberately not done before the AWS deployment** (decided 2026-11-05): it
touches every page's data layer for zero functional gain, which is the wrong
risk to take on the eve of a deploy. Land it early in the next cycle instead.

- **Root cause is already fixed.** `npm run codegen` needs a running API, so it
  got skipped and `types.gen.ts` went months stale (missing `/api/v1/ppl/*` and
  `/api/v1/health/freshness` entirely) — which is what let the duplicates
  diverge unnoticed. `tests/contract/test_openapi_types_in_sync.py` now fails
  the suite when a schema is missing from the generated file.
- Migration order: start with the newest shapes (PPL, freshness), which have
  had the least time to be depended on, then the high-traffic ones.
- Replace `fetchJson<T>(path)` with the typed `openapi-fetch` client wherever a
  generated path exists, so the URL is compile-checked too. 11 call sites.
- Keep `fetchJson` only for endpoints genuinely absent from the schema, with a
  comment saying why.

