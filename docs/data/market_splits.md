# Spec — Dedicated `equities.market_splits` store

Status: **IMPLEMENTED** (2026-06-27) — table created, backfilled (27,453
splits), read path (reader + read_arrow via `splits_reader`) cut over, ingest
dual-writes splits. Per-symbol split read: **79 s → ~sub-second** (no dividend
scan); adjustment output unchanged (verified). Backfill mechanism: PyIceberg
idempotent upsert (`scripts/backfill_market_splits.py`). Splits kept in
`market_corp_actions` for the overlap cycle.
Date: 2026-06-27

## 1. Problem

`equities.market_corp_actions` co-mingles two datasets with opposite
cardinality and access patterns:

- **Splits** — ~50k rows, slowly-changing, read **by symbol** on the
  adjustment hot path (and read in full for whole-market adjustment).
- **Dividends** — ~3M rows, only relevant to total-return analysis (NOT used
  by split-adjustment).

The table is partitioned only by `month(ex_date)` with no symbol/type
addressability, so fetching one symbol's ~4 splits scans ~3M rows / ~280
monthly partitions → **~79 s, on every adjusted lake read**. An in-memory
cache would only hide this; the fix is to store splits so they're cheap to
read.

## 2. Goal

- Per-symbol split lookup: **instant** (no dividend scan).
- "Read all splits" (what whole-market adjustment needs): **sub-second**.
- Production-grade: correct, snapshot-pinnable for reproducible ML/cold
  builds, kept current by the existing ingest, no dual-write divergence.
- Split-adjustment output **unchanged** (same factors, sourced faster).

## 3. Design

### 3.1 New table `equities.market_splits`
Authoritative store for splits (factor != 1.0 normalized as today).

| col | type | notes |
|---|---|---|
| `symbol` | string (req) | identifier |
| `ex_date` | date (req) | split effective date |
| `factor` | double (req) | split ratio (e.g. 4.0, 0.2 reverse) |
| `source_provider` | string | provenance (polygon) |
| `ingestion_ts` | timestamptz | when written |
| `ingestion_run_id` | string | run provenance |

- **Identifier fields** `(symbol, ex_date)` — dedup key (idempotent upsert).
- **Sort order** `(symbol ASC, ex_date ASC)` — contiguous per symbol.
- **Layout**: tiny (~50k rows). Unpartitioned (or `year(ex_date)` if it ever
  grows). Explicit small `write.parquet.row-group-size-bytes` + statistics so
  per-symbol row-group pruning works AND the whole table is one/few small
  files (full read is sub-second).

### 3.2 Dividends stay in `market_corp_actions`
`market_corp_actions` continues to hold dividends (its existing dividend
consumers are unaffected). **Splits route to `market_splits` only** — no
dual-write, single source of truth for splits. (Optional later: rename the
dividend table to `market_dividends`; out of scope here.)

### 3.3 Ingest change
`PolygonCorpActionsIngest` already separates `iter_splits` / `iter_dividends`.
Route splits → `market_splits` (idempotent upsert on `(symbol, ex_date)`),
dividends → `market_corp_actions` as today. Forward-only refreshes append.

### 3.4 Read path
`app/services/equities/adjust.py` + `AdjustedOhlcvReader` + `read_arrow`
load splits from `market_splits` (full small read, or per-symbol filter) —
replacing the `market_corp_actions WHERE action_type='split'` scan. The
single-symbol hot path stays on ClickHouse (already adjusted; no split read).

## 4. Migration (one-time)
1. Create `market_splits`.
2. Backfill from existing `market_corp_actions` splits — one ~79 s scan,
   once. Verify row count matches distinct splits.
3. Cut the read path over to `market_splits`.
4. Cut the ingest over so future refreshes maintain it.

## 5. Verification
- **Equivalence:** adjusted output identical before/after (factors are the
  same data, just sourced faster) — re-run the read-time-vs-known check for
  split-heavy symbols (AAPL/NVDA).
- **Perf:** per-symbol split read < ~100 ms; full `market_splits` read
  < ~1 s (vs 79 s today).
- Ingest idempotency: re-running a window doesn't duplicate `(symbol,ex_date)`.

## 6. Rollout
Land table + ingest + backfill + read-path cutover together (behind nothing —
it's a strictly-better source). Keep `market_corp_actions` splits in place
for one cycle as a fallback, then optionally stop writing splits there.

## 7. Open questions
1. Backfill mechanism: PyIceberg (read corp_actions splits once → append to
   market_splits) — no Spark needed (set is tiny). Confirm.
2. Keep writing splits to `market_corp_actions` during the one-cycle overlap,
   or cut immediately after backfill+verify?
