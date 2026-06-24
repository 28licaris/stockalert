# Data Platform Architecture

**The canonical end-to-end map of how market data moves through StockAlert:
providers → ingestion → storage (hot + cold) → freshness → read/serve.**

Written for both humans and agents. If you're an agent orienting in this repo,
read this first, then the per-module `README.md` for whatever you're touching.
For deep lake/schema details see [`../architecture_v2/`](../architecture_v2/README.md);
this doc is the integrative view across the whole system.

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

See the rendered diagram in the architecture chat artifact
(`stockalert_lake_to_clickhouse_dataflow`).

---

## 2. Data providers

Three providers, **env-switched** (`app/config.py`), behind one ABC
(`app/providers/base.py:DataProvider`).

| Provider | Live stream | REST history | Flat files | Notes |
|---|---|---|---|---|
| **Polygon** | WS "AM" 1-min aggs | `massive.RESTClient` | **S3 flat files** (`files.massive.com`) | Equities + futures deep history; flat files are the bulk-history workhorse |
| **Schwab** | Streamer WS (equity + futures chart) | `/marketdata/v1/pricehistory` | — | Live tape + recent (~48d 1-min) refresh; account/journal |
| **Alpaca** | `StockDataStream` | `list_aggs` | — | Default equities provider |

Selection (`app/config.py`):
- `DATA_PROVIDER` (default `alpaca`) — primary.
- `STREAM_PROVIDER` → `effective_stream_provider` — the **live** WS provider.
- `HISTORY_PROVIDER` → `effective_history_provider` — REST backfill.
- Factories: `get_stream_provider()`, `get_history_provider()`, `get_market_quotes_provider()` (Schwab-preferred for quotes).

---

## 3. Storage layers

### 3a. ClickHouse — hot tier (`app/db/init.py`)

All `ReplacingMergeTree(version)`, `PARTITION BY toYYYYMM(timestamp)`,
`ORDER BY (symbol, timestamp)` → pruned reads + merge-on-read dedup (higher
`version` wins).

| Table | Holds | Written by |
|---|---|---|
| `stocks.ohlcv_1m` | equities 1-min | live batcher, lake-fill, ch_reconcile |
| `stocks.ohlcv_daily` | equities daily | resample / fill |
| `stocks.futures_ohlcv_1m` | futures 1-min (continuous roots `/ES`) | live batcher, futures lake-fill, ch_reconcile |

Live writes are **batched** (`app/db/batcher.py`): flush at **500 rows or 5s**,
separate batchers for equities vs futures. Source tag `{provider}-stream`.

### 3b. Iceberg lake — cold tier

Two Glue databases. All tables keyed for idempotent upsert; bronze appends,
silver dedups (see [`bronze idempotency`](../standards/data/architecture_v2.md)).

**`equities.*`**

| Table | Source | Adjustment |
|---|---|---|
| `polygon_raw` | Polygon flat files (nightly) | none (raw) |
| `polygon_adjusted` | `polygon_raw` + `market_corp_actions` (Spark, weekly) | split-adjusted + `adj_factor` |
| `schwab_universe` | Schwab REST nightly + live_lake_writer (5m) | pre-adjusted (1.0) |
| `market_corp_actions` | Polygon REST nightly | splits/dividends |

**`futures.*`**

| Table | Source | Notes |
|---|---|---|
| `schwab_futures` | Schwab live + REST nightly | continuous roots, recent ~48d 1-min |
| `schwab_futures_daily` | Schwab REST | deep daily tier |
| `polygon_raw` | Polygon flat-file **mirror** (parse) | **every outright contract** (ESH4…), no roll |
| `polygon_continuous` | `polygon_raw` + volume roll | **back-adjusted continuous roots**, `adj_factor` |

Plus a raw landing zone: `s3://<lake>/polygon_flatfiles_mirror/` — byte-for-byte
Polygon flat files (minute + session + trades), the durable vendor archive the
futures pipeline parses from. See [`futures_flatfile_mirror.md`](../futures_flatfile_mirror.md).

---

## 4. The futures pipeline (mirror → raw → continuous)

Mirrors the equities `raw → adjusted` pattern, with the roll playing the role
of the corporate-action adjustment. **Zero REST** — contract order comes from
the ticker month-codes.

```
Polygon flat files (entitled rolling ~5yr)
  └─ mirror (byte copy)      → polygon_flatfiles_mirror/         scripts/polygon_futures_mirror.py
       └─ parse (per contract) → futures.polygon_raw             scripts/polygon_futures_parse_raw.py
            └─ volume roll +    → futures.polygon_continuous      scripts/polygon_futures_build_continuous.py
               ratio back-adjust                                  app/services/futures/volume_roll.py
```

- **Mirror** — byte-for-byte, idempotent, probes the subscription entitlement
  floor (Polygon grants only a rolling ~5yr GET window), throttled to dodge 429s.
- **Parse** — outright contracts only (`contract_root()` rejects spreads/strips);
  dedups session-boundary overlaps.
- **Continuous** — front month = highest-volume contract per ET day with roll
  hysteresis; ratio back-adjustment removes roll gaps; `adj_factor` (1.0 on the
  front segment) recovers true contract prices.

Read path: `bars_gateway` → `futures/lake_to_ch_fill._scan_futures_lake`
unions `schwab_futures` (live tip) + `polygon_continuous` (deep back-adjusted).

---

## 5. Freshness — keeping hot and cold in sync

| Mechanism | Direction | Cadence | Heals |
|---|---|---|---|
| Live batcher | provider → CH | 500 rows / 5s | n/a (the live feed) |
| `live_lake_writer` | CH → lake | 5 min | persists live bars to `schwab_universe`/`schwab_futures` |
| `ch_reconcile` | lake → CH | post-close (23 UTC) | live-stream gaps from restarts/outages |
| `lake_to_ch_fill` | lake → CH | on-demand (gateway miss) | first read of cold history |
| Nightly lake refreshes | provider → lake | daily | yesterday's authoritative bars |
| Spark adjustment | raw → adjusted | weekly | split-adjusted equities |
| `backfill` sweeper | provider → CH | daily 06 UTC | intraday holes in streamed symbols |

---

## 6. Scheduled jobs (registered in `app/main_api.py` startup)

All gated by an env flag; each appears in the `JobRegistry` (Status page).

| Job | Flag | Cadence | Output |
|---|---|---|---|
| Live lake writer | `LIVE_LAKE_WRITER_ENABLED` | 5 min | `equities.schwab_universe` |
| Nightly Polygon (equities) | `POLYGON_NIGHTLY_ENABLED` | daily 07 UTC | `equities.polygon_raw` |
| Nightly Schwab (equities) | `SCHWAB_NIGHTLY_ENABLED` | daily 22 UTC | `equities.schwab_universe` |
| Nightly Schwab (futures) | `FUTURES_NIGHTLY_ENABLED` | daily 22 UTC | `futures.schwab_futures` |
| Nightly Polygon (futures) | `FUTURES_POLYGON_NIGHTLY_ENABLED` | daily 21 UTC | `futures.polygon_raw` → `polygon_continuous` |
| CH reconcile | `CH_RECONCILE_ENABLED` (on) | daily 23 UTC | CH (heals gaps from lake) |
| Corp actions | nightly | daily | `equities.market_corp_actions` |
| Elliott recompute | `ELLIOTT_RECOMPUTE_ENABLED` | daily 22 UTC | `*.elliott_wave_labels` |
| Elliott live scanner | `ELLIOTT_LIVE_SCANNER_ENABLED` | on each bar | wave alerts (WS) |
| Journal sync | `JOURNAL_ENABLED` (on) | 5 min | Schwab balances/trades |
| Gap sweeper | (on) | daily 06 UTC | enqueues CH gap fills |

The **futures Polygon nightly** is **incremental**: per root it appends the new
front-month bars at `adj_factor=1.0` when nothing rolled (seconds), and
full-rebuilds only the roots that actually rolled. Spark adjustment runs
out-of-process (EMR Serverless / CodeBuild), not in the API.

---

## 7. Read path (`app/services/readers/bars_gateway.py`)

`get_chart_bars(symbol, interval, source=AUTO)`:

- **`CLICKHOUSE`** — CH only (fast, possibly partial).
- **`LAKE`** — Iceberg ground truth (equities: `polygon_adjusted`; futures:
  `polygon_continuous` + schwab tip), no side effects.
- **`AUTO`** (default) — read CH; if coverage is short (`_ch_lacks_window`),
  schedule a background `lake_to_ch_fill` and return what CH has *now*
  (sub-100ms). Next read is warm.

Symbol routing: `/`-prefix ⇒ futures (`futures_ohlcv_1m` / `polygon_continuous`);
otherwise equities. Daily futures prefer `schwab_futures_daily`.

Readers are the **only** sanctioned read surface; HTTP routes (`app/api/`) and
MCP tools (`app/mcp/`) are thin adapters over them.

---

## 8. Cross-cutting invariants

- **ET trading-day, not UTC date.** After-hours/overnight bars carry a UTC
  timestamp on the next calendar day; bucket by ET (`astimezone(NY).date()`).
  Futures session is CME Globex Sun–Fri. See
  [`timezone_et_vs_utc`](../standards/data/timezone_et_vs_utc.md).
- **Idempotency everywhere.** CH `ReplacingMergeTree(version)`; Iceberg
  append + dedup-on-read by identifier. Reruns are safe.
- **Bronze appends, silver dedups.** Never `overwrite(filter)`/`delete(filter)`
  in the hot path; the continuous (silver) layer dedups source overlaps.
- **No silent failures.** Jobs log every outcome incl. zero, verify mutations
  cross-side, and exit non-zero on mismatch. See [`coding.md`](../standards/coding.md).

---

## 9. Cloud / speed optimization notes

Current strengths: CH hot cache with pruned reads, CH-first lazy-fill gateway,
batched live writes, in-region CodeBuild for backfills, append-only bronze.

Open optimization levers (ranked):
1. **Futures continuous nightly is now incremental** (append daily, rebuild on
   roll) — done; was the main inefficiency.
2. Move heavy lake compute off the API process → CodeBuild/EMR in-region or
   Spark (the continuous build is single-machine pandas today).
3. Unify large historical fills on Athena `UNLOAD` (as equities already does).
4. Pre-warm CH for the active watchlist off-hours.
5. S3 Intelligent-Tiering/Glacier for the write-once mirror + trades archive.
6. ClickHouse HA (replicated cluster / CH Cloud) + partition TTL.

---

## 10. Where to look

| You want… | Go to |
|---|---|
| Lake schema/partition deep-dive | [`architecture_v2/`](../architecture_v2/README.md) |
| Futures mirror→raw→continuous | [`futures_flatfile_mirror.md`](../futures_flatfile_mirror.md) |
| A specific module's contract | that folder's `README.md` |
| Coding/standards rules | [`standards/`](../standards/README.md) |
| Startup/registration order | [`STARTUP_FLOW.md`](../STARTUP_FLOW.md) |
| Operator procedures | [`architecture_v2/07_runbook.md`](../architecture_v2/07_runbook.md) |
