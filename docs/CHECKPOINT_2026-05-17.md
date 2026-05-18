# Checkpoint — 2026-05-17

Session pause point after a major TA-5.0 + TA-5.7 push. Documents
exactly what landed, what's verified, and where to pick up next.

---

## What's complete

### TA-5.0 — Corp-actions ingestion (LANDED)

Bronze + silver pipeline for splits + dividends + spinoffs is
production-grade. Polygon REST → bronze.polygon_corp_actions →
silver.corp_actions. End-to-end live-verified against the operator's
Polygon subscription.

**Empirical proof:** Ran the canary window 2024-06-10..2024-06-14
(week of NVDA's 10-for-1 split). 5,108 rows in silver.corp_actions.
NVDA's split correctly shows `factor=10.0` on 2024-06-10. Idempotent
re-run produces the same count.

**Sub-phases:**
- 5.1: silver package scaffold
- 5.2: corp-actions Pydantic + Iceberg schemas
- 5.3: silver Iceberg table bootstrap
- 5.4: PolygonCorpActionsClient (REST wrapper)
- 5.4.5: Design realignment — bronze→silver pattern (per operator clarification)
- 5.5a: bronze.polygon_corp_actions schema + table
- 5.5b: Polygon → bronze ingest (`PolygonCorpActionsBronzeIngest`)
- 5.5c: bronze → silver merger (`SilverCorpActionsBuild`)
- 5.5d: Universal provider-adjustment probe framework
- 5.5e: Universal bronze audit framework with 5 checks
- 5.6: 66 unit tests + circular-import fix
- 5.7: CorpActionsReader + HTTP route + MCP tool
- 5.8: Operator backfill CLI (`scripts/run_corp_actions_backfill.py`)
- 5.9: Live verification + 2 real bugs caught + fixed

**Two real bugs the live test caught (no pure unit test could have):**
1. `CorpActionKind` needed expansion: `CD`/`LT`/`ST` were collapsing
   under `cash_dividend` → duplicate-key upsert errors when funds
   issue regular + cap-gains distributions on same ex_date. Fixed
   by giving `lt_capital_gain` and `st_capital_gain` distinct kinds.
2. Same-ex_date special + regular cash dividends (13 / 5089 in one
   week). Fixed by `_dedupe_actions()` in the bronze ingest (sums
   `cash_amount` for matching identifiers).

### TA-5.7 — Live lake writer (LANDED)

Closes the 8-24h Schwab live → bronze freshness gap that the audit
flagged. Live ticks now land in bronze within ~5-10 minutes during
market hours.

**Sub-phases:**
- 5.7.1: `app/services/ingest/live_lake_writer.py` core class
- 5.7.2: Lifespan startup/shutdown wiring + config flag
- 5.7.3: `ingestion_runs` CH audit table
- 5.7.4: `scripts/compact_bronze.py` (Athena OPTIMIZE wrapper)
- 5.7.5: New bronze audit check `live_freshness`
- 5.7.6: 27 unit tests + RTH detection tests

**Schwab live stream rows now tagged `schwab-stream`** to distinguish
from REST-backfilled `schwab` rows (the writer filters on this).

### Universal frameworks built (cross-cutting)

- **`app/services/silver/probes/`** — pluggable provider-adjustment
  probe framework. 5 known-split probes (AAPL 2020, NVDA 2024, AMZN
  2022, GOOGL 2022, TSLA 2022). Adding a new provider = drop a file
  in `probes/`, register via `@register_probe(name)`. Verified
  empirically: Polygon = RAW, Schwab = SPLIT_ADJUSTED.

- **`app/services/bronze/audit/`** — pluggable bronze audit
  framework. 6 checks: schema_match, row_counts, source_tags,
  null_symbols, adjustment_status, live_freshness. Same registry
  pattern as probes.

- **`docs/data_ingestion_paths.md`** (NEW) — comprehensive ingestion
  architecture diagram + per-path walkthrough.

---

## Live system state (empirically verified)

| What | Value | Verified |
|---|---|---|
| `bronze.polygon_minute` row count | 2,116,486,243 | 2026-05-17 audit |
| `bronze.polygon_minute` date range | 2021-01-04 → 2026-05-15 | audit |
| `bronze.polygon_minute` adjustment status | RAW | NVDA 2024 probe (ratio 9.932 ≈ 10.0) |
| `bronze.schwab_minute` row count | 1,774,051 | audit |
| `bronze.schwab_minute` date range | 2026-03-30 → 2026-05-15 | audit |
| `bronze.schwab_minute` adjustment status | SPLIT_ADJUSTED | API probe (AAPL 2020 + NVDA 2024) |
| `bronze.polygon_corp_actions` | created, 5,076 rows from canary | live ingest |
| `silver.corp_actions` | created, 5,108 rows from canary | live build |
| Null symbols in bronze (total) | 0 | audit |
| Schema drift | None (both tables 12 fields, match declarations) | audit |
| Test count (new+existing combined) | 104 passing across the new TA-5.0 + TA-5.7 work | local pytest |

---

## What needs operator action before TA-5.1

Before silver_build (TA-5.1) starts, you should validate that
TA-5.7 (live_lake_writer) is actually running in production. This
is a 5-minute operator check:

1. **Restart the FastAPI process.** Startup logs should show:
   ```
   ✅ Live lake writer started (cycle=5min lookback=15min)
   ```

2. **Run during market hours (Mon-Fri 9:30am-4pm ET):**
   ```bash
   # After ~10 minutes of market open:
   poetry run python scripts/audit_bronze.py --check live_freshness
   ```
   Expected: 🟢 OK for `schwab_minute` (stale_minutes < 30, has
   `schwab-stream`-tagged rows).

3. **Spot-check bronze:**
   ```bash
   poetry run python scripts/audit_bronze.py --check source_tags
   ```
   Expected: `schwab_minute` now shows BOTH `schwab` (REST backfills)
   AND `schwab-stream` (live writer). The "expected-but-absent"
   warning for `schwab-stream` should disappear.

4. **Optional — run the full historical corp-actions backfill:**
   ```bash
   poetry run python scripts/run_corp_actions_backfill.py --full
   ```
   This pulls ~50K splits + ~3M dividends since 2003 (~30-60 min
   wall-clock; bounded by Polygon pagination cadence). After
   completion, silver.corp_actions will be the canonical history
   that silver_build (TA-5.1) will use for adjustment computations.

---

## TA-5.1 status (silver OHLCV build) — .1–.6 LANDED 2026-05-17

✅ All code sub-phases done. Only operator-validate (TA-5.1.7)
remains. See [BUILD_JOURNAL.md](BUILD_JOURNAL.md) decision log for
per-commit details.

Implemented in `app/services/silver/ohlcv/` (sub-package, not the
flat `silver_build.py` originally sketched):

- ✅ `schemas.py` — `silver.ohlcv_1m` (18 fields, both _raw + _adj,
  month partition, `(symbol, ts)` identifier) + `silver.bar_quality`
  (11 fields, `(symbol, date)` identifier) + Pydantic SilverBar.
- ✅ `normalize.py` — raw↔split-adjusted math. Polygon raw → _adj
  via divide-by-F; Schwab adj → _raw via multiply-by-F. NVDA
  2024-06-10 10-for-1 split verified.
- ✅ `merge.py` — provider precedence (polygon > schwab default) +
  one-pass bar_quality (expected/actual/gaps/disagreements).
- ✅ `build.py` — orchestrator with build_slice/window/nightly/full.
  Provider-pluggable via `_PROVIDER_ROUTING`. Error-isolated per
  slice. Corp-actions cache primed once per run.
- ✅ Reader + HTTP + MCP (TA-5.1.5):
  `SilverOhlcvReader.get_bars` + `get_bar_quality`,
  `GET /api/silver/bars/{symbol}` + `/api/silver/bar-quality/{symbol}`,
  MCP `get_silver_bars` + `get_silver_bar_quality`.
- ✅ Nightly loop + CLI (TA-5.1.6): in-process asyncio at default
  23:00 UTC (1h after Schwab nightly), gated on
  `SILVER_OHLCV_BUILD_ENABLED`. Operator CLI
  `scripts/run_silver_ohlcv_build.py --full / --nightly / --since
  / --until / --symbols / --out-json`.

102 silver tests green. Toggle `SILVER_OHLCV_BUILD_ENABLED=true`
to arm the nightly; run `scripts/run_silver_ohlcv_build.py --full`
once to seed silver from bronze.

### Original design recap (for reference)

`silver.ohlcv_1m` + `silver.bar_quality` per the design in
[silver_layer_plan §3](silver_layer_plan.md).

**Architecture (preview):**

```
bronze.polygon_minute (RAW)        bronze.schwab_minute (SPLIT_ADJUSTED)
       │                                       │
       │  apply corp_actions factors           │  un-adjust via cumulative
       │  → compute _adj                       │  split factor → compute _raw
       ▼                                       ▼
   normalized: { _raw, _adj }              normalized: { _raw, _adj }
                          │              │
                          ▼              ▼
                ┌────────────────────────────────┐
                │ merge with provider precedence  │
                │ polygon > schwab default        │
                └────────────┬───────────────────┘
                             │
                             ▼
                ┌────────────────────────────────┐
                │  silver.ohlcv_1m               │
                │  (8 price columns: _raw + _adj)│
                │  + silver.bar_quality          │
                └────────────────────────────────┘
```

**Sub-phases (planned):**
- 5.1.1: `silver.ohlcv_1m` + `silver.bar_quality` Iceberg schemas
- 5.1.2: Per-provider normalization logic (raw→adj for polygon;
  adj→raw for schwab via cumulative split factors from silver.corp_actions)
- 5.1.3: Provider precedence merge
- 5.1.4: `bar_quality` computation
- 5.1.5: Watermarked + idempotent build job
- 5.1.6: Unit tests + adversarial test cases (synthetic split → expected adjustment)
- 5.1.7: `SilverReader` + HTTP + MCP
- 5.1.8: Operator CLI for initial backfill
- 5.1.9: Live verification

**The hardest piece:** the per-provider normalization in 5.1.2.
The un-adjustment math for Schwab is:

```
For each bar with timestamp T and source = schwab-*:
  splits_after_T = corp_actions where symbol=S, ex_date > T, action_type=split
  cumulative_factor = product(factor for s in splits_after_T)
  _raw = _adj × cumulative_factor   # un-adjust Schwab's split-adjusted price
```

This needs careful testing — synthetic split + verify the math
round-trips, plus integration against a real symbol (e.g. NVDA
before/after 2024-06-10) to confirm `_raw` reconstructs the
unadjusted price the trader actually saw.

---

## Recommended next session structure

1. **5 min**: Operator validates TA-5.7 in production (steps above).
2. **30 min**: Plan TA-5.1.1 in detail (Iceberg schemas for
   `silver.ohlcv_1m` + `silver.bar_quality`).
3. **2-3 hr**: TA-5.1.1 + 5.1.2 (schemas + normalization logic).
4. **Subsequent sessions**: TA-5.1.3 through 5.1.9 surgically.

Once TA-5.1 lands, then TA-5.2 (`SilverReader` + flip the backtest
harness from `BronzeReader` to `SilverReader`) and TA-5.3
(`silver_to_ch_backfill` for the cockpit warming-up UX) follow
naturally.

---

## Today's commit log (for reference)

All on `main`, pushable as one block of work:

```
b1e6bd4  TA-5.7 LANDED: live_lake_writer + ingestion_runs + freshness audit + compaction
90b14d7  TA-5.0 LANDED step 9: live verification + two real bugs fixed
0cf2bac  TA-5.0 step 8: operator corp-actions backfill CLI
85e343b  TA-5.0 step 7: CorpActionsReader + HTTP route + MCP tool
4bcb4bb  TA-5.0 step 6: unit tests + fix circular import in silver/__init__.py
5400d96  TA-5.0 step 5.6 + TA-5.7 inserted: bronze audit framework + freshness-gap finding
a166c16  TA-5.0 step 5.5: universal probe framework + empirical adjustment-status finding
05d74e1  TA-5.0 step 5c: silver_corp_actions_build (bronze → silver merger)
4910741  TA-5.0 step 5b: Polygon → bronze.polygon_corp_actions ingest
d1e774e  TA-5.0 step 5a: bronze.polygon_corp_actions schema + table bootstrap
152d55d  TA-5.0 step 4.5: realign corp-actions to bronze→silver pattern
3554959  TA-5.0 step 4/9: PolygonCorpActionsClient — Polygon REST splits + dividends
37750b0  TA-5.0 step 3/9: idempotent Iceberg table bootstrap
5de565e  TA-5.0 step 2/9: define silver.corp_actions schemas
af988ce  TA-5.0 step 1/9: scaffold app/services/silver/ package
```

Plus the earlier risk-management plan + audit plan from the morning
review session.
