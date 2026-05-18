# Futures data ingestion — investigation + plan

Plan-only doc. Investigates how to add futures (ES, NQ, CL, GC, …)
to the pipeline. Companion to [data_platform_plan.md](data_platform_plan.md)
and [streaming_universe_model.md](streaming_universe_model.md), both of
which explicitly defer futures ("Equities only for now").

## TL;DR

| Capability | Schwab | Polygon |
|---|---|---|
| **Live 1-min bars** | ✅ via `CHART_FUTURES` streamer (same shape as CHART_EQUITY) | ✅ via WebSocket Futures aggregates (separate paid plan) |
| **Live quotes** | ✅ `LEVELONE_FUTURES` | ✅ |
| **Historical bars (REST)** | ❌ `/pricehistory` is equity-only per Schwab docs | ✅ REST aggregates available (separate paid plan) |
| **Flat-file bulk history** | n/a (no flat-files) | ❌ flat-files are `us_stocks_sip` / options / indices / crypto / forex only — **no us_futures** |
| **Continuous contracts** | ❌ raw individual contracts only | ❌ raw individual contracts only |
| **Symbol format** | `/{root}{month}{year}` e.g. `/ESM26` | typically `F:` prefix or per-product convention |

**The fundamental architectural difference from equities:** we cannot
do a Schwab REST tip-fill for a brand-new futures symbol. Historical
futures requires a Polygon Futures plan (or another vendor). Without
it, the "add a symbol" UX is fundamentally different — chart starts
from subscribe-time, not 48-days-back.

**Recommendation:** phase futures as a Phase-2 expansion AFTER the
TA-5.5 silver-derived rollout is done. Three sub-phases, each a clean
gate:

1. **TF-1: Live-only futures.** Stream CHART_FUTURES → bronze →
   silver → CH. ~3 days. Limited backtesting (going-forward only).
2. **TF-2: Polygon Futures historical.** Subscribe to Polygon's
   Futures plan; add ingestion → bronze → silver. Enables real
   backtesting. ~3-5 days + a paid subscription decision.
3. **TF-3: Continuous contracts.** Roll-adjusted synthetic series
   (back-adjusted or proportional) for ML training. ~5-7 days.

This doc covers Phase TF-1 in concrete detail and outlines TF-2/TF-3.

---

## 1. Asset model: contracts vs continuous

Futures have a structural twist that equities don't:

### Individual contracts
Each contract is a separate symbol with an expiration date:
- `/ESH26` — E-mini S&P 500 March 2026
- `/ESM26` — E-mini S&P 500 June 2026
- `/ESU26` — E-mini S&P 500 September 2026

These trade independently. The June contract usually has the most
volume until ~1 week before expiration ("roll period"), then liquidity
shifts to September.

### Continuous contracts
A synthetic series that splices individual contracts together at
roll dates, producing one ticker (`/ES`) with continuous history.
Two common methods:

- **Unadjusted continuous**: simple concatenation; price jumps at
  the roll boundary. Bad for ML (creates fake gaps).
- **Back-adjusted continuous**: at each roll, adjust ALL prior bars
  by `(new_contract_price - old_contract_price)`. Continuous
  smooth series. Standard for backtesting.

### Decision required from operator

**Pick one:** does the system expose individual contracts, continuous
contracts, or both?

Recommended: **both, but at different layers.**
- Bronze + silver store **individual contracts** (raw, immutable, no
  rollover logic baked in).
- A separate **`silver.futures_continuous`** table is the rolled-up
  view, derived from silver via a rollover schedule. Consumers
  (chart, backtester, ML) read the continuous view by default; the
  individual contracts stay queryable for replay/specialist use.

This mirrors the `_raw`-vs-`_adj` debate for equities — except for
futures the answer is "both views, in separate tables" because the
rollover logic is non-trivial and a single column set can't carry
both views.

---

## 2. Phase TF-1: Live-only futures (~3 days)

**Scope:** Stream Schwab `CHART_FUTURES` 1-min bars for a configured
list of futures symbols. Write to a new bronze table.
Silver_ohlcv_build extends to merge them. CH chart shows them live.
No historical backfill — chart starts when the symbol is subscribed.

**Why start here:** small surface area, zero new vendor cost, exercises
all the integration points. If futures end up being a niche feature,
we don't pay for a second data subscription. If they end up critical,
TF-2 layers on cleanly.

### Sub-phases

| Step | What | Effort |
|---|---|---|
| TF-1.1 | New bronze table `bronze.schwab_futures_minute` (same shape as `bronze.schwab_minute`; just a different namespace) | 1 hr |
| TF-1.2 | Symbol routing in `watchlist_service._reconcile`: `/{...}` → CHART_FUTURES subscribe; else CHART_EQUITY (existing path) | 2 hr |
| TF-1.3 | `bar_batcher` accepts futures bars; writes to CH `ohlcv_1m` with an `asset_class` tag | 2 hr |
| TF-1.4 | `live_lake_writer` extended to flush futures bars to `bronze.schwab_futures_minute` (parallels the existing schwab_minute writer) | 2 hr |
| TF-1.5 | `silver_ohlcv_build` extended: new provider routing entry for `schwab_futures_minute`; corp-actions handling stubbed (futures don't split; emit F=1 always) | 3 hr |
| TF-1.6 | `silver.futures_ohlcv_1m` table (per individual contract). Identifier: `(symbol, timestamp)` same as equities | 2 hr |
| TF-1.7 | Symbol validation: reject malformed futures symbols at the API boundary; surface in `/api/silver/bars/{symbol}` etc. | 1 hr |
| TF-1.8 | Tests: futures-symbol parsing, routing to the right stream channel, bar_batcher source-tagging, silver build wiring | 3 hr |

**Gate:** Add `/ESM26` to a test watchlist; observe live ticks
flowing → CH ohlcv_1m → bronze.schwab_futures_minute (every 5 min
via live_lake_writer) → silver.futures_ohlcv_1m (next nightly build).
Chart endpoint `/api/silver/bars/%2FESM26` returns the bars
correctly.

**Wall-clock estimate:** 2-3 days focused work.

### Open design questions for TF-1

1. **`asset_class` column on CH ohlcv_1m, or separate CH tables?**
   - Single table + `asset_class` col: simpler, queries can filter
     `WHERE asset_class = 'equity'`. Cost: every existing query
     must opt-in or default to filtered.
   - Separate `ohlcv_1m_futures` table: clean separation, but every
     reader needs to know which to query.
   - Recommend: single table + column. Existing readers default to
     equities via a filter. Performance impact: negligible at our
     volume.

2. **Should bronze be ONE `bronze.schwab_futures_minute` with all
   contracts, or one table per root (`/ES`, `/NQ`)?**
   - One table: simpler, queries filter on `symbol`. Bronze is
     append-only so a few hundred K rows/day across all contracts
     is fine.
   - Recommend: one table, partition by `month(timestamp)` like
     equities.

3. **Symbol-format quirks.** Schwab uses `/ESM26` (slash prefix);
   URLs in `/api/silver/bars/{symbol}` need URL-encoding (`%2FESM26`).
   Cosmetic but every API surface needs to handle it.

---

## 3. Phase TF-2: Polygon Futures historical (~3-5 days)

**Why this matters:** without TF-2, futures backtests can only see
data accumulated since subscribe-time (going-forward). Equities
benefit from 5 years of Polygon flat-file history; futures would
get... nothing pre-subscribe.

For real ML training + walk-forward validation, you need years of
1-min futures history.

### Vendor options

| Option | What you get | Cost (approx) | Setup |
|---|---|---|---|
| Polygon Futures plan | REST aggregates + WebSocket, individual contracts back ~5y | $79-249/mo per their tier | ~1 day integration |
| CME DataMine direct | Tick-level, full history, every product | Expensive, complex licensing | ~1-2 weeks |
| Databento Futures | 1-min + tick, S&P/CME products, ~10y | $99-299/mo | ~1 day |
| FirstRate Data (one-time) | 1-min CSV downloads, ~20y history | One-time ~$500-1500 | ~1 day import |

**Recommended:** start with Polygon (familiar SDK, same data
philosophy we already use for equities). FirstRate as a one-shot
historical-only seed if cost is a concern.

### Sub-phases

| Step | What | Effort |
|---|---|---|
| TF-2.1 | `app/providers/polygon_futures_provider.py` — REST aggregates + symbol translation | 1 day |
| TF-2.2 | `bronze.polygon_futures_minute` table | 2 hr |
| TF-2.3 | `scripts/polygon_futures_bulk_backfill.py` — one-shot historical | 4 hr |
| TF-2.4 | Nightly `polygon_futures_refresh` (parallels Polygon equity nightly) | 4 hr |
| TF-2.5 | Silver build extension: provider precedence for futures (Polygon > Schwab when both present, same as equities) | 2 hr |
| TF-2.6 | Tests + operator runbook | 4 hr |

**Gate:** Spot-check 10 historical days against Polygon's web UI or
a third-party reference (Yahoo's continuous /ES series, etc.).

---

## 4. Phase TF-3: Continuous contracts (~5-7 days)

**Why:** rollover is the futures equivalent of split adjustment.
Without it, every backtest spans only one contract's lifetime
(~3 months for equity index futures). With rollovers, ML training
sees 5-year continuous series.

### The math

For each root (e.g. `/ES`), maintain a **rollover schedule**:
- Date: roll date (e.g. "2nd Friday of expiration month, 8 days
  before contract expiry")
- From: prior contract (e.g. `/ESM26`)
- To: next contract (e.g. `/ESU26`)

At each roll date, the **adjustment offset** for back-adjusted
continuous is:
```
offset = price(new_contract, roll_date) - price(old_contract, roll_date)
```
Apply offset to ALL prior bars of the continuous series.

For proportional adjustment (ratio-based):
```
ratio = price(new_contract, roll_date) / price(old_contract, roll_date)
old_bars *= ratio
```

### Sub-phases

| Step | What | Effort |
|---|---|---|
| TF-3.1 | `silver.futures_rollover_schedule` table: per-root, per-roll-date metadata | 4 hr |
| TF-3.2 | `silver.futures_continuous_1m` build job: reads `silver.futures_ohlcv_1m` + rollover schedule, emits per-root continuous series | 1 day |
| TF-3.3 | API surface: `GET /api/silver/futures/{root}/continuous` | 4 hr |
| TF-3.4 | Tests: synthetic two-contract data → expected continuous output (verify both back-adjusted + proportional methods) | 1 day |
| TF-3.5 | Operator-tunable rollover policy (volume-based vs date-based) | 1 day |

**Gate:** 5-year `/ES` continuous series with no visible discontinuities
at roll dates; backtests on the continuous series produce stable
equity curves across multiple rolls.

---

## 5. What this displaces / interacts with

### Streaming universe model
`docs/streaming_universe_model.md` is currently equity-only. After
TF-1, the "active universe" needs to grow to include futures
symbols (separate from the equity universe).

Recommendation: split `get_active_universe()` into:
- `get_active_equity_universe()` — what we have today
- `get_active_futures_universe()` — new
- `get_active_universe()` → union of both, for callers that need it

### Add-symbol flow
The current `add_streamed_symbol` flow (silver→CH + Schwab REST
tip-fill + live stream) breaks for futures:
- silver→CH: works if silver has the symbol (after TF-2)
- Schwab REST tip-fill: BROKEN — no Schwab REST for futures
- Live stream: works via CHART_FUTURES

Result for a brand-new futures symbol pre-TF-2: chart shows
live-only. After TF-2 + first nightly: chart shows full history.

### Existing tests
Anything that hardcodes "equity" assumptions (e.g. the universe
resolvers, the silver-build's provider routing, the CH source-tag
mappings) will need to be made asset-class-aware. Estimate ~30
tests to touch.

---

## 6. Order of operations + decision points

```
Now → finish TA-5.5 (delete legacy Path ②, wipe-and-rebuild CH)
  ↓
DECISION POINT: do we need futures?
  ↓
TF-1 (live-only, ~3 days) — minimal scope to validate the integration
  ↓
DECISION POINT: backtest futures? If yes:
  ↓
TF-2 (Polygon Futures subscription, ~3-5 days)
  ↓
DECISION POINT: continuous contracts? If yes:
  ↓
TF-3 (continuous + rollover, ~5-7 days)
```

Each phase is reversible (delete the asset class) and additive (the
existing equity infra is untouched).

---

## 7. Risks + unknowns

1. **Schwab account permissions.** Futures trading entitlements vary
   per Schwab account. The streamer might not return data without
   the right permissions. Verify by subscribing to `/ESM26` in
   `LEVELONE_FUTURES` first; if quotes arrive, CHART_FUTURES should
   too.

2. **Symbol-format edge cases.** Schwab's contract symbols use
   month/year codes (F-Z for months, two-digit year). Renames at
   expiration are a concern — does `/ESM26` after June 2026 expiry
   start returning empty, or does it 404? Need to test before
   building rollover automation.

3. **Polygon's futures product cost + coverage.** Need to verify on
   their pricing page before committing to TF-2.

4. **Continuous-series ambiguity.** No industry standard for the
   rollover method — back-adjusted vs proportional vs unadjusted are
   all valid. Operator will need to pick one as the canonical, and
   make it explicit in the silver schema (which method generated
   this series).

5. **Tick data vs aggregated.** Futures markets have notable price
   action in seconds 0-30 of a minute (e.g. economic releases at 8:30).
   1-min aggregates lose this signal. If trading strategies need
   it, TF-2/TF-3 should also bring in sub-minute aggregates
   (parallel to the existing 1-min tier, not a replacement).

---

## What to do RIGHT NOW

**Nothing in code.** This is a planning doc.

Decisions needed before TF-1 starts:
1. Do we need futures at all? (Strategic — depends on whether your
   trading thesis includes index futures / commodities.)
2. Single CH table + `asset_class` column, or separate tables?
3. After TA-5.5 lands, is futures the next priority, or
   options/crypto/something else?

Once those are answered, TF-1 is a clean ~3-day implementation.
