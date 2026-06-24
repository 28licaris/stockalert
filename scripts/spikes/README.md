# Lake read-layer engine spike

**Question:** which engine should back `read_arrow()` for the **cold
read path** — DuckDB-over-Iceberg, Polars, or the current
PyIceberg+Python baseline?

Spec: [`docs/lake_read_layer_design.md`](../../docs/lake_read_layer_design.md)
(§3.2, §6 open question 1, §5 Risk "DuckDB Iceberg maturity").

This is a **spike**: throwaway-until-chosen code under `scripts/spikes/`.
Nothing in `app/` imports it. When a winner is picked it graduates into
`app/services/readers/read_arrow.py` as a real PR.

## What the cold path is (and isn't)

| Path | Engine | This spike? |
|---|---|---|
| Live intraday alerts / monitoring | ClickHouse (hot) | ❌ out of scope |
| Sync / one-time-load ClickHouse from the lake | cold read | ✅ |
| Batch pull for backtesting + agent training | cold read | ✅ |

The cold path reads **multiple lake tables** (per security type + data
provider) and, by default, **unions** them: e.g. `AAPL` = Polygon deep
history `∪` Schwab universe, polygon winning contested
`(symbol, timestamp)` rows. You can also target a single source. Gap
back-filling (Schwab/Polygon REST after the union) is a separate,
downstream step and is not part of the engine choice.

## The contract every engine implements

    union all sources → keep highest-precedence row per (symbol, timestamp)
    → sort by (symbol, timestamp) → return a pyarrow.Table (no per-row objects)

`SourceSpec` (in `lake_read_engines.py`) is the spec's §3.1 registry
entry — adding a provider is one `SourceSpec`, no merge-logic edit.

## Files

- `lake_read_engines.py` — `SourceSpec`, the registry, the three
  engines behind one contract, and a `__main__` that runs one engine
  once and prints a JSON result. Engines:
  - `baseline` — PyIceberg→Arrow, union/dedup/sort in **Python** (the
    bottleneck the spec removes).
  - `polars` — `pl.scan_iceberg`, union/dedup/sort in Rust.
  - `duckdb` — PyIceberg plans the files, DuckDB read_parquet does the
    SQL union/dedup/sort in C++.
  - `duckdb_iceberg` — DuckDB's native `iceberg_scan` over the Glue
    metadata json. Probes the spec's Risk #1 directly; may fail, and
    that failure IS a finding (not swallowed).
- `lake_read_engine_bench.py` — runs every engine over a sweep of
  request shapes, prints a comparison table, and **cross-checks the
  output hashes per shape** (correctness before speed).

## Run it

Install the engines (optional dep group):

    poetry install --with spike

Offline correctness gate (no cloud — proves the engines agree):

    poetry run pytest tests/spikes/test_lake_read_engines_equality.py -v

Real benchmark (needs lake creds; **run in-region**):

    AWS_PROFILE=stock-lake poetry run python -m scripts.spikes.lake_read_engine_bench

    # narrower:
    AWS_PROFILE=stock-lake poetry run python -m scripts.spikes.lake_read_engine_bench \
        --symbols AAPL --symbols AAPL,MSFT,NVDA --years 5 --engines baseline,polars,duckdb

## Reading the results

- **Run in-region.** From a laptop you measure your home internet, not
  the engine — S3 round-trip latency dominates a cold read. The harness
  prints `host=` / `lake_region=` so a WAN run isn't mistaken for a real
  one.
- **`compute ms` vs `planning ms`.** `planning` = Glue/Iceberg metadata
  + file resolution; `compute` = scan + union + dedup + sort. Polars
  folds planning into `collect()`, so its split is approximate — compare
  on `total ms`.
- **Watch the ⚠ deletes flag.** `polygon_adjusted` is merge-on-read.
  DuckDB's read_parquet-over-files path does NOT apply Iceberg delete
  files, so if the flag fires its output can be wrong — prefer
  `duckdb_iceberg` (native, applies deletes) or Polars/PyIceberg there.
  The offline test plus the per-shape hash check guard this.

## Decision criteria (what closes the spike)

1. **Correctness** — all engines hash-identical on every shape (offline
   test already green; cloud bench must confirm on real data incl. any
   merge-on-read deletes).
2. **Speed/scale** — pick the engine whose `total ms` and `peak MB`
   stay flat as symbol count grows (the baseline is expected to blow up
   on peak RSS — that's the point being proven).
3. **Iceberg/Glue maturity** — does `duckdb_iceberg` read the Glue
   tables cleanly? If yes, DuckDB is viable end-to-end. If it fights
   the catalog, Polars-over-PyIceberg wins on integration (it reuses
   the PyIceberg planning this repo already runs in production).

Record the table + the call in the spec (§6 q1) before writing the
production `read_arrow()`.
