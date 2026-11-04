# Architecture — the data platform

**End-to-end map of how market data moves through StockAlert: providers →
ingestion → storage (hot + cold) → freshness → read/serve.**

This is the integrative view across the whole system and the **source of truth
for platform architecture**. If you are an agent orienting in this repo, read
this first, then the per-module `README.md` for whatever you are touching.

| You want… | Go to |
|---|---|
| Lake schema, DDL, partitioning, S3 layout | [`lake_schema.md`](lake_schema.md) |
| Provider abstraction, adding a provider, token refresh | [`providers.md`](providers.md) |
| Operator procedures (restart, backfill, DR) | [`runbook.md`](../operations/runbook.md) |
| Service folder map + commands | [`../../CLAUDE.md`](../../CLAUDE.md) |
| A specific module's contract | that folder's `README.md` |
| Coding / testing / engagement rules | [`../standards/`](../standards/README.md) |
| Startup + job registration order | [`operations/startup_flow.md`](../operations/startup_flow.md) |

---

## 1. Mental model — a lakehouse with a hot cache

Two tiers, one read surface:

- **Hot tier — ClickHouse.** Low-latency reads for charts, MCP tools, live
  scanners. Holds recent + on-demand-filled bars. Disposable: can be wiped and
  refilled from the lake.
- **Cold tier — Iceberg on S3 (Glue catalog).** The durable ground truth — full
  history, every provider, reproducible for ML/backtests. ClickHouse can be
  down and training is unaffected.

Everything else is plumbing to **(a)** get provider data into both tiers and
**(b)** keep them in sync. A single gateway (`bars_gateway`) reads CH-first and
falls back to the lake, so callers never think about tiers.

```
PROVIDERS ─┬─ live WS ───────► ClickHouse (HOT) ──┐
           └─ batch/nightly ──► Iceberg lake (COLD) ┤─► Bars Gateway ─► consumers
                          freshness loops keep them in sync
```

---

## 2. Data providers

Three bar providers, **env-switched** (`app/config.py`), behind one ABC
(`app/providers/base.py:DataProvider`).

| Provider | Live stream | REST history | Flat files | Notes |
|---|---|---|---|---|
| **Polygon** | WS "AM" 1-min aggs | `RESTClient` | S3 flat files | Futures deep history. Equities minute flat-files **lapsed** — daily bars now come via REST grouped-daily |
| **Schwab** | Streamer WS (equity + futures chart) | `/marketdata/v1/pricehistory` | — | Live tape, recent (~48d 1-min) refresh, options chains, account/journal |
| **Alpaca** | `StockDataStream` | `list_aggs` | — | Default equities provider |

ThetaData is used out-of-band (not a `DataProvider`) for options EOD IV history.

Selection (`app/config.py`):
- `DATA_PROVIDER` (default `alpaca`) — primary.
- `STREAM_PROVIDER` → `effective_stream_provider` — the **live** WS provider.
- `HISTORY_PROVIDER` → `effective_history_provider` — REST backfill.
- Factories: `get_stream_provider()`, `get_history_provider()`,
  `get_market_quotes_provider()` (Schwab-preferred for quotes).

Details: [`providers.md`](providers.md).

---

## 3. Storage layers

### 3a. ClickHouse — hot tier (`app/db/init.py`)

Bar tables are `ReplacingMergeTree(version)`, `PARTITION BY toYYYYMM(timestamp)`,
`ORDER BY (symbol, timestamp)` → pruned reads + merge-on-read dedup (higher
`version` wins).

**Serving tables** — created by `init_schema()`, read by charts/MCP/scanners:

| Table | Holds | Written by |
|---|---|---|
| `stocks.ohlcv_1m` | equities 1-min bars | live batcher, lake-fill, ch_reconcile |
| `stocks.futures_ohlcv_1m` | futures 1-min bars (continuous roots `/ES`) | live batcher, futures lake-fill, ch_reconcile |

`ohlcv_1m` is the **only** equities serving table. Every chart timeframe
(5m/15m/30m/1h/1d) is resampled from it on read
(`queries.list_bars_resampled`, daily bucketed on the ET trading day), so all
timeframes stay current to the latest streamed minute and stay split-adjusted.
The former `ohlcv_5m` / `ohlcv_daily` *cache* tables were retired in 2026-06 —
feeding them separately caused staleness and duplicate-candle bugs and bought
nothing (a 5-year daily resample is sub-second). If deep-history daily ever
needs to be instant under heavy concurrency, add an `AggregatingMergeTree`
materialized view — do not reintroduce a separately-fed table.

**Research tables** — materialized by `scripts/build_ohlcv_*.py` for
backtesting and strategy R&D, *not* on the serving path:
`ohlcv_daily` (split-adjusted, delisted-inclusive, dominant-segment trimmed),
`ohlcv_30m`, `ohlcv_hourly`.

**Other state tables:**

| Table | Holds | Written by |
|---|---|---|
| `stocks.stream_universe` | what we hot-cache (membership authority) | universe service |
| `stocks.futures_universe` | futures roots tracked | futures universe sync |
| `stocks.options_contracts_latest` / `options_gex_latest` | latest chain + GEX snapshot | options hot sink |
| `stocks.options_atm_iv_daily` | ATM IV history (ThetaData EOD) | `build_atm_iv_history.py` |
| `stocks.vol_premium_history` | vol-premium rankings | `build_vol_premium_history.py` |
| `stocks.strategy_definitions` / `paper_state` | durable strategy + paper-trading state | sim / paper engine |
| `stocks.signals` / `trades` / `trade_notes` / `account_snapshots` | signal + journal surface | alerts, journal sync |
| `stocks.ingestion_runs` | job audit trail (90d TTL) | `RunRecorder` |
| `stocks.market_events` / `economic_data` / `news_items` | calendar, macro, news | calendar + news services |
| `stocks.instrument_names` / `sector_themes` | reference data | instruments, sectors |
| `stocks.watchlists` / `watchlist_members` | user watchlists | watchlists service |
| `stocks.assistant_conversations` / `assistant_turns` / `agent_runs` | assistant transcripts | assistant service |

Live bar writes are **batched** (`app/db/batcher.py`): flush at **500 rows or
5s**, separate batchers for equities vs futures. Source tag `{provider}-stream`.

### 3b. Iceberg lake — cold tier

Three Glue databases. Bronze appends, silver dedups — never
`overwrite(filter)` / `delete(filter)` in the hot path.

**`equities.*`**

| Table | Source | Adjustment |
|---|---|---|
| `polygon_raw` | Polygon flat files (historical; minute feed lapsed 2026-06-12) | none (raw) |
| `polygon_daily_raw` | Polygon REST grouped-daily (nightly, paced) | none (raw) |
| `schwab_universe` | Schwab REST nightly + `live_lake_writer` (5m) | pre-adjusted (`adj_factor=1.0`) |
| `schwab_daily_raw` | Schwab REST daily | pre-adjusted |
| `market_corp_actions` | Polygon REST nightly | dividends (+ splits, legacy) |
| `market_splits` | mirrored from corp-actions | splits only (~27k rows); the read-time adjustment source |

> **Split-adjusted OHLCV is computed at read time, not stored.**
> `adjusted = f(polygon_raw, market_splits)` via
> `app/services/equities/adjust.py`. The old materialized
> `equities.polygon_adjusted` table and its weekly Spark job were retired —
> `AdjustedOhlcvReader` and the `read_arrow` `"polygon_adjusted"` *source name*
> apply the adjustment on read. Design:
> [`data/adjusted_storage.md`](../data/adjusted_storage.md).

**`futures.*`**

| Table | Source | Notes |
|---|---|---|
| `schwab_futures` | Schwab live + REST nightly | continuous roots, recent ~48d 1-min |
| `schwab_futures_daily` | Schwab REST | deep daily tier |
| `polygon_raw` | Polygon flat-file **mirror** (parse) | every outright contract (`ESH4`…), no roll |
| `polygon_continuous` | `polygon_raw` + volume roll | back-adjusted continuous roots, `adj_factor` |

**`options.*`**

| Table | Source | Notes |
|---|---|---|
| `schwab_chain_raw` | Schwab REST `/chains` | raw provider payload per snapshot; audit/reparse source |
| `schwab_chain_contracts` | Schwab REST `/chains` | canonical contract rows per snapshot; replay/backtest source |
| `schwab_expirations` | Schwab chain data | observed expirations per underlying |
| `gamma_exposure_snapshots` | derived from canonical contracts | GEX totals/levels by underlying, strike, expiry |

Plus a raw landing zone: `s3://<lake>/polygon_flatfiles_mirror/` — byte-for-byte
Polygon flat files (minute + session + trades), the durable vendor archive the
futures pipeline parses from. See
[`../futures_flatfile_mirror.md`](../data/futures_flatfile_mirror.md).

Schema, DDL and partitioning: [`lake_schema.md`](lake_schema.md).

---

## 4. The futures pipeline (mirror → raw → continuous)

Mirrors the equities raw → adjusted pattern, with the roll playing the role of
the corporate-action adjustment. **Zero REST** — contract order comes from the
ticker month-codes.

```
Polygon flat files (entitled rolling ~5yr)
  └─ mirror (byte copy)      → polygon_flatfiles_mirror/      scripts/polygon_futures_mirror.py
       └─ parse (per contract) → futures.polygon_raw           scripts/polygon_futures_parse_raw.py
            └─ volume roll +    → futures.polygon_continuous    scripts/polygon_futures_build_continuous.py
               ratio back-adjust                                app/services/futures/volume_roll.py
```

- **Mirror** — byte-for-byte, idempotent, probes the subscription entitlement
  floor (Polygon grants only a rolling ~5yr GET window), throttled to dodge 429s.
- **Parse** — outright contracts only (`contract_root()` rejects spreads/strips);
  dedups session-boundary overlaps.
- **Continuous** — front month = highest-volume contract per ET day with roll
  hysteresis; ratio back-adjustment removes roll gaps; `adj_factor` (1.0 on the
  front segment) recovers true contract prices.

Read path: `bars_gateway` → `futures/lake_to_ch_fill._scan_futures_lake` unions
`schwab_futures` (live tip) + `polygon_continuous` (deep back-adjusted).

---

## 5. Freshness — keeping hot and cold in sync

| Mechanism | Direction | Cadence | Heals |
|---|---|---|---|
| Live batcher | provider → CH | 500 rows / 5s | n/a (the live feed) |
| `live_lake_writer` | CH → lake | 5 min | persists live bars to `schwab_universe` / `schwab_futures` |
| `ch_reconcile` | lake → CH | post-close (23 UTC) | live-stream gaps from restarts/outages |
| `lake_to_ch_fill` | lake → CH | on-demand (gateway miss) | first read of cold history |
| Nightly lake refreshes | provider → lake | daily | yesterday's authoritative bars |
| `gap_fill_worker` | provider → CH | background queue | gateway-detected coverage holes, off the HTTP path |
| `backfill` sweeper | provider → CH | daily 06 UTC | intraday holes in streamed symbols |

Split adjustment is **not** a freshness mechanism any more — it happens on read.

---

## 6. Scheduled jobs (registered in `app/main_api.py` startup)

All gated by an env flag; each appears in the `JobRegistry` (Status page) and
records to `stocks.ingestion_runs` through one shared `audit_run` path, so a
manual button-press and the scheduled loop log identically.

| Job | Flag | Cadence | Output |
|---|---|---|---|
| Live lake writer | `LIVE_LAKE_WRITER_ENABLED` | 5 min | `equities.schwab_universe` |
| Nightly Polygon (equities) | `POLYGON_NIGHTLY_ENABLED` | daily 07 UTC | `equities.polygon_daily_raw` |
| Nightly Schwab (equities) | `SCHWAB_NIGHTLY_ENABLED` | daily 22 UTC | `equities.schwab_universe` |
| Nightly Schwab (futures) | `FUTURES_NIGHTLY_ENABLED` | daily 22 UTC | `futures.schwab_futures` |
| Nightly Polygon (futures) | `FUTURES_POLYGON_NIGHTLY_ENABLED` | daily 21 UTC | `futures.polygon_raw` → `polygon_continuous` |
| CH reconcile | `CH_RECONCILE_ENABLED` (on) | daily 23 UTC | CH (heals gaps from lake) |
| Corp actions | nightly | daily | `equities.market_corp_actions` + `market_splits` |
| Elliott recompute | `ELLIOTT_RECOMPUTE_ENABLED` | daily 22 UTC | `*.elliott_wave_labels` |
| Elliott live scanner | `ELLIOTT_LIVE_SCANNER_ENABLED` | on each bar | wave alerts (WS) |
| Journal sync | `JOURNAL_ENABLED` (on) | 5 min | Schwab balances/trades |
| Gap sweeper | (on) | daily 06 UTC | enqueues CH gap fills |

The **futures Polygon nightly** is **incremental**: per root it appends the new
front-month bars at `adj_factor=1.0` when nothing rolled (seconds), and
full-rebuilds only the roots that actually rolled.

Jobs run as in-process asyncio loops, not cron. Pollers set `frequent=True` so
the heartbeat does not flood the audit table.

---

## 7. Read path (`app/services/readers/bars_gateway.py`)

`get_chart_bars(symbol, interval, source=AUTO)`:

- **`CLICKHOUSE`** — CH only (fast, possibly partial).
- **`LAKE`** — Iceberg ground truth (equities: read-time-adjusted
  `polygon_raw`; futures: `polygon_continuous` + Schwab tip), no side effects.
- **`AUTO`** (default) — read CH; if coverage is short (`_ch_lacks_window`),
  hand the fill to `gap_fill_worker` and return what CH has *now* (sub-100ms).
  Next read is warm. The HTTP path never blocks on a lake fill.

Symbol routing: `/`-prefix ⇒ futures (`futures_ohlcv_1m` / `polygon_continuous`);
otherwise equities. Daily futures prefer `schwab_futures_daily`.

Readers are the **only** sanctioned read surface; HTTP routes (`app/api/`) and
MCP tools (`app/mcp/`) are thin adapters over them.

---

## 8. Cross-cutting invariants

- **ET trading-day, not UTC date.** After-hours/overnight bars carry a UTC
  timestamp on the next calendar day; bucket by ET (`astimezone(NY).date()`).
  Futures session is CME Globex Sun–Fri. See
  [`../standards/data/timezone_et_vs_utc.md`](../standards/data/timezone_et_vs_utc.md).
- **Idempotency everywhere.** CH `ReplacingMergeTree(version)`; Iceberg append +
  dedup-on-read by identifier. Reruns are safe.
- **Bronze appends, silver dedups.** Never `overwrite(filter)` /
  `delete(filter)` in the hot path — PyIceberg reads existing files first.
  Maintenance happens via Athena.
- **Tracked instruments live in the universe.** Adding a symbol/sector/theme we
  track means adding its constituents to `stream_universe`, not just
  backfilling CH. See
  [`../standards/data/symbol_lifecycle.md`](../standards/data/symbol_lifecycle.md).
- **No silent failures.** Jobs log every outcome including zero, verify
  mutations cross-side, and exit non-zero on mismatch. See
  [`../standards/coding.md`](../standards/coding.md).

---

## 9. Performance posture

Current strengths: CH hot cache with pruned reads, CH-first non-blocking
gateway, batched live writes, in-region CodeBuild for backfills, append-only
bronze, incremental futures continuous build.

Open optimization levers (ranked):
1. Move heavy lake compute off the API process → CodeBuild in-region (the
   continuous build is single-machine pandas today).
2. Unify large historical fills on Athena `UNLOAD` (as equities already does).
3. Pre-warm CH for the active watchlist off-hours.
4. S3 Intelligent-Tiering / Glacier for the write-once mirror + trades archive.
5. ClickHouse HA (replicated cluster) + partition TTL.

**Sanity floor:** a bulk operation should run near `data_volume / 100 MB/sec`.
At 100× that, the architecture is wrong — stop and audit rather than grinding
through. This is not big data.
