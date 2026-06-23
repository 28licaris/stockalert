# Futures flat-file mirror + raw lake (Polygon)

**Status:** Phase 1 implemented (mirror). Phase 2 (parse → Iceberg) pending.
**Owner:** EWT initiative. **Created:** 2026-06-21.

## Why

The Polygon **Futures Advanced** subscription is time-boxed (expires ~2026-07).
We must capture *all* available futures flat-file data before it lapses. The
first attempt — a continuous-front-month backfill
(`polygon_futures_flatfiles_backfill.py`) — was both slow and wrong:

| Problem | Root cause |
|---|---|
| Only ~5 yrs (2021+) captured | REST `list_futures_contracts` only returns contracts back to ~2021, even though **flat files exist from 2017-04-03**. The roll logic depended on REST, so it capped history. |
| 8 roots wrote **zero** rows | Wrong exchange-prefix map (`YM/MYM` are CBOT not CME; `PL/PA` are NYMEX not COMEX) — hardcoded from memory, never verified. |
| All monthly roots broken/partial | Front-month window logic was only tested on quarterly contracts; monthly roots' strip/average pseudo-contracts (`CL:SA 12M`, `…XXX`) collapse the windows. |
| `errors=0` despite all the above | The script validated bar *counts*, not per-root coverage — a silent failure. |
| ~9.4 h runtime | Dominated by REST contract discovery (energy roots have 600–1,645 contracts each); the data download itself was fast. |

**Decision:** stop deriving continuous roots during ingest. Mirror the raw flat
files verbatim first (immune to every bug above), then derive curated tables
*later* from the durable mirror — so the subscription can expire safely.

## Verified facts (Polygon `flatfiles` bucket, us-east-1)

Futures = 4 exchanges (`us_futures_{cme,cbot,comex,nymex}`), each with 4
datasets. Files are *listed* back to `2017-04-03`, but **GetObject is entitled
only for a rolling trailing ~5-year window** — older files return **403**
(listable, not readable). Probed boundary on 2026-06-21 was exactly
`2021-06-21` (today − 5 years); it rolls forward daily, so the obtainable
history shrinks over time. **Capture now.** The mirror script probes this floor
at runtime and only attempts entitled files.

(This also explains the earlier continuous-root backfill's 2021 floor — it was
the subscription boundary, not only the REST contract-history limit.)

Full-listing sizes (2017–present) vs the ~5-yr entitled slice we can actually pull:

| Dataset | Listed files / size | Entitled (~5 yr) | Captured? |
|---|---|---|---|
| `minute_aggs_v1` | 9,508 / 7.42 GB | ~5,200 / ~4 GB | ✅ Phase 1 |
| `session_aggs_v1` | 9,472 / 0.13 GB | ~5,200 / ~0.07 GB | ✅ Phase 1 |
| `trades_v1` | 9,508 / 123.6 GB | ~5,200 / ~67 GB | ✅ Phase 1 (raw only) |
| `quotes_v1` | 11,174 / 5.77 TB | — | ❌ deferred (storage cost) |

## Architecture (mirrors the equities pattern)

Equities store flat files as the parsed Iceberg table `equities.polygon_raw`
(→ `equities.polygon_adjusted`). Futures follow the same shape, with a raw
byte-mirror landing zone in front of it:

```
Polygon flat files
  └─ Phase 1: byte mirror  → s3://<lake>/polygon_flatfiles_mirror/{exchange}/{dataset}/YYYY/MM/*.csv.gz
       └─ Phase 2: parse    → futures.polygon_raw   (Iceberg, every contract, no roll)   ≈ equities.polygon_raw
            └─ later        → futures continuous roots (volume-based roll)                ≈ equities.polygon_adjusted
```

- **Phase 1** (`scripts/polygon_futures_mirror.py`): pure GET→PUT, byte-for-byte,
  no parse/REST/roll. Idempotent (skip existing matching-size objects).
  Reconciles dest vs source key set + byte totals per (exchange, dataset);
  **exits non-zero on any missing key, size mismatch, or transfer error**, and
  writes `polygon_flatfiles_mirror/_manifest.json`. Datasets copy in order
  (aggregates first, trades last). Runs on CodeBuild
  (`scripts/codebuild/buildspec_futures_mirror.yml`) in-region — no home egress.
- **Phase 2** (pending): parse `minute_aggs` + `session_aggs` from the **mirror**
  (not Polygon) into `futures.polygon_raw`. Trades stay raw `.csv.gz`. No
  exchange-prefix map or REST needed — exchange is a column, every row is stored.
- **Continuous roots** (later): volume-based roll derived from `polygon_raw`
  (front month = dominant-volume contract, with roll hysteresis). Fixes the
  monthly-root bug at the root and needs zero REST.

## Operational notes

- Re-running the mirror is safe and cheap (resumes; only missing/changed files copy).
- The broken `futures.polygon_futures` Iceberg table (23.7 M rows, ~29 partial
  roots, 2021+) is **dropped** — it will be rebuilt correctly in the
  continuous-roots step.
- `quotes_v1` (5.77 TB, ~$133/mo S3) is intentionally **not** mirrored. Revisit
  only if microstructure/spread-fill research is needed; pull a targeted subset.

## Roadmap to equities parity

The equities lake separates **raw** (every symbol, unadjusted) from **derived**
(`polygon_adjusted`, built by a periodic job). Futures must follow the same
split. The dropped `futures.polygon_futures` table was wrong because it
conflated both — baking roll logic into ingest and storing continuous `/ES`
directly. Mapping each equities layer to the futures gap:

| Equities | Futures equivalent | Status |
|---|---|---|
| `equities.polygon_raw` (`tables.py` `ensure_polygon_raw`) | `futures.polygon_raw` — every **contract** (ESH4, CLM4…), no roll | ✅ **DONE — 179.2M rows, all roots** |
| `equities.polygon_adjusted` (Spark `polygon_adjustment_job.py`) | `futures.polygon_continuous` — volume-based roll off `polygon_raw` | ✅ **DONE — 55.4M rows, 37 roots** |
| `equities.market_corp_actions` | — (futures have no corp actions) | n/a |
| `equities.schwab_universe` | `futures.schwab_futures` (live) | ✅ exists |
| `AdjustedOhlcvReader` → `polygon_adjusted` | `bars_gateway` + `lake_to_ch_fill` → `polygon_continuous` (+ schwab tip) | ✅ **DONE — read path repointed + verified** |
| `nightly_polygon_refresh` → `polygon_raw` | nightly mirror → raw → continuous | ⏳ **remaining** (Schwab nightly covers the recent tip today) |

### Built & verified (2026-06-22)
- **Phase 1 mirror:** 88.85 GB (minute+session+trades, 2021-06-21→present).
- **Phase 2 `polygon_raw`:** 179,155,856 outright-contract rows, 0 failures;
  all 20 liquid roots present incl. the 8 the old backfill dropped (CL/YM/PL/PA/BZ…).
- **Phase 3 `polygon_continuous`:** 55,441,450 rows, 37 roots; roll cadence
  correct by class (quarterly=20, energy monthly=60, metals=25, grains≈24);
  `/ES` & `/GC` seams smooth, `/CL` outliers verified as real Sunday-open
  moves (not seam gaps); `adj_factor` recoverable (front=1.0).
- **Read path:** `_scan_futures_lake` → `polygon_continuous` (+ schwab tip),
  verified serving back-adjusted bars.

### Remaining (finish-line)
1. **Nightly Polygon extend** (analog of `nightly_polygon_refresh`): mirror
   yesterday → append `polygon_raw` → extend `polygon_continuous` front edge.
   Not blocking historical backtesting; Schwab nightly already keeps the recent
   tip fresh via the union. Needs the CodeBuild role granted Glue perms on the
   `futures` DB (or run under `stock-lake` creds).
2. **Dead-code cleanup:** the abandoned roll-at-ingest path now references the
   dropped `polygon_futures` table — `ensure_polygon_futures`, `polygon_sink.py`,
   `polygon_futures_backfill.py`, `polygon_futures_flatfiles_backfill.py`,
   `contract_chain.py`, and their buildspecs. Dormant (nothing live calls them),
   safe to remove.
3. **session_aggs → daily raw** (small): parse the mirrored daily/session files.

### Phase 2 — `futures.polygon_raw` (raw, every contract)
- New schema: contract `ticker` + `exchange` + OHLCV + `vwap` + `transactions`
  (+ `dollar_volume`), partitioned `month(timestamp)` (consider `bucket` by
  root). Add `ensure_polygon_raw()` + sink in `app/services/futures/`.
- Parse job reads the **mirror** (`polygon_flatfiles_mirror/{ex}/minute_aggs_v1`)
  → Iceberg append. Runs on CodeBuild (no Polygon dependency → subscription-safe).
- Parse `session_aggs` into a daily raw table (or feed `schwab_futures_daily`'s analog).
- `trades_v1` stays raw `.csv.gz` (equities doesn't parse trades either).

### Phase 3 — `futures.polygon_continuous` (derived roots)
- Volume-based roll: front month = dominant-volume contract per day, with roll
  **hysteresis** (require N days of dominance) to avoid flip-flop. Reads
  `polygon_raw`, writes continuous `/ES`, `/CL`, … Replaces the dropped table.
  Fixes the monthly-root window-collapse bug at the root (low-volume strip
  pseudo-contracts never win) and needs **zero REST**.
- **Open design decisions (need sign-off before building):**
  1. Roll signal: volume (recommended) vs open-interest (not in flat files) vs calendar.
  2. Back-adjustment: store unadjusted continuous, back-adjusted (Panama/ratio,
     an `adj_factor` analog), or both? EWT leans unadjusted; confirm.
  3. Continuous universe: which roots get a `/X` series.
  4. Naming: `futures.polygon_continuous` vs reuse `futures.polygon_futures`.

### Phase 4 — wiring + nightly
- Point `bars_gateway._futures_one_min_from_lake` + `lake_to_ch_fill` at
  `polygon_continuous` (union with `schwab_futures` for the live tip), replacing
  the dropped `polygon_futures` reference.
- Nightly futures Polygon refresh (analog of `nightly_polygon_refresh`):
  incremental mirror of yesterday → append to `polygon_raw` → extend
  `polygon_continuous` tip. Keeps the rolling window fresh.
