# Lake read layer + onboarding + freshness — remaining work

Consolidated list of what's **shipped** vs **outstanding** across the
read-layer / symbol-onboarding / freshness initiative (branch
`feat/lake-read-followups`, merged to `main`). Pointers to the
authoritative specs for each.

## Shipped

- **Cold read engine** — Polars-over-PyIceberg `read_arrow()` +
  `SourceSpec` registry (lazy/pushdown/merge/streaming); `get_bars_union`
  delegates to the shared dedup. Spec:
  [`lake_read_layer_design.md`](lake_read_layer_design.md).
- **Hotload-on-add** — `SYMBOL_HOTLOAD_ENABLED`/`DAYS` (default on/30d,
  <5s), stream-only mode. Spec:
  [`symbol_onboarding_read_design.md`](symbol_onboarding_read_design.md) §3.1.
- **Read-path gap-fill** — provider REST fallback when the lake can't
  cover a cold/new symbol (`SYMBOL_GAPFILL_ENABLED`). §3.3 of the same.
- **Production-grade freshness** — nightly lake writers default ON
  (gated, auto-catchup); `get_lake_freshness` covers the adjusted +
  futures tiers; `clickhouse_connect_timeout` for fast-fail boot.

## Outstanding

### 1. Operational — the one external gap (highest priority)
- **Wire the weekly `polygon_adjustment_job` (Spark) into a scheduler**
  (CodeBuild buildspec + cron/EventBridge, like
  `scripts/codebuild/buildspec_lake_read_bench.yml`) **with failure
  alerting**. It builds `equities.polygon_adjusted` and has NO in-process
  auto-catchup — `get_lake_freshness` now *surfaces* when it's stalled,
  but something still has to *run* it. Until wired, this is manual.
- **External alerting** on freshness/job failure (Slack/PagerDuty).
  Today: structured logs + the `get_lake_freshness` tool only.

### 2. Symbol onboarding follow-ups
- **Interior mid-series hole detection** — gap-fill v1 only handles the
  whole-window cold-symbol case; a present-but-patchy symbol's interior
  holes aren't repaired. Reuse `find_intraday_gaps` (CH-side) +
  per-hole provider fill.
- **Futures provider gap-fill** — v1 is equities-only (Schwab tip-fill);
  futures skipped.
- **Per-hotload / per-gapfill provider knob** — both use the configured
  provider (Schwab) today; make the provider selectable.
- **Per-add hotload override** — expose `hotload`/`hotload_days` on the
  watchlist/seed add endpoints (settings-only today).
- **First-paint "history loading" signal** — bars response carries a
  loading flag (or WS push) so the UI refreshes when a fill lands,
  instead of showing a silent empty chart.

### 3. Read-layer levers (signoff-gated — dual storage / new layers)
- **Lever 1 — materialized union table** (dual storage): pre-compute the
  deduped polygon∪schwab union so reads are a plain projection scan.
  Needs explicit signoff (second copy of truth).
- **Lever 4 — snapshot-keyed result cache**: cache `read_arrow` output
  by `(symbols, window, snapshot_id)` for repeated backtest/training
  pulls.
- **Alpaca as a 3rd `SourceSpec`** — onboard one more provider to prove
  the registry's modularity end-to-end.

### 4. Symbol-universe coverage (known limitation)
- Schwab nightly only refreshes `"active"` (stream_universe) symbols; a
  symbol dropped from watchlists stops getting Schwab refresh (Polygon
  whole-market still covers it). Document/decide the symbol-lifecycle
  policy if Schwab is the primary source for a symbol.
