# Lake Read Layer — Modular & Fast

**Status:** in progress — engine chosen, core landed. Branch
`feat/lake-read-layer`.

**Done:** Polars-over-PyIceberg engine chosen (§6 q1) via the
`scripts/spikes/` spike + in-region CodeBuild benchmark (14× faster
than the Python baseline, lowest memory). `SourceSpec` registry
(`app/services/readers/source_registry.py`) + `read_arrow()`
(`app/services/readers/read_arrow.py`) shipped with levers 2 (sorted-
merge) & 3 (lazy + pushdown) + streaming collect. `get_bars_union`
delegated to the shared dedup (`union_arrow`). 54 reader + read_arrow
tests green; verified vs the real lake. See §4 for the step-by-step.

**Open (signoff-gated, NOT built):** lever 1 materialized union table
(dual storage), lever 4 snapshot-keyed result cache, post-union
gap-fill, Alpaca as a 3rd registry source. See end of §4.

How the platform reads adjusted OHLCV history out of the v2 Iceberg
lake, so the read path is (a) **fast in the cloud** for both
interactive and bulk/ML workloads and (b) **modular across data
sources** (Schwab, Polygon, and any future provider) without editing
merge logic.

Complements:
- [architecture_v2/01_architecture.md](architecture_v2/01_architecture.md)
  — the v2 lake; why the medallion vocabulary was retired; the
  `equities.polygon_adjusted` + `equities.schwab_universe` split.
- [indicator_exposure_design.md](indicator_exposure_design.md) — a
  consumer of this read layer.
- [`app/services/readers/README.md`](../app/services/readers/README.md)
  — the reader module contract.

This doc does **not** change ingestion (the `DataProvider` interface
is already pluggable) or the ClickHouse hot tier. It targets the
**lake-direct read path**.

## 0. Cold-read consumers & requirements (signed off 2026-06-23)

The hot/interactive path (live intraday monitoring, alert evaluation)
stays on **ClickHouse** and is out of scope. This read layer serves the
**cold** consumers:

1. **Sync / one-time-load ClickHouse** from the lake (seed or repair the
   hot tier).
2. **Batch pull** for backtesting and agent/ML training.

Requirements that pin the design:

- **Selectable source or default union.** A caller can target a single
  table, or — by default — **union across the per-(security-type,
  provider) tables**. e.g. `AAPL` → Polygon deep history `∪` Schwab
  universe, with **polygon winning** contested `(symbol, timestamp)`.
- **Optional post-union gap-fill.** After fetch+union, the caller may
  request that remaining gaps be filled from a provider REST read
  (Schwab / Polygon / …). This is a **separate downstream step**, not an
  engine concern — it consumes this layer's output, it doesn't change
  the union/dedup contract.
- **Fast even though it's cold.** Cold ≠ slow: the path must stay
  efficient from 1 symbol to whole-market without a Python object
  explosion.

The engine that backs the columnar path (§3.2) is being chosen by a
spike — see [`scripts/spikes/README.md`](../scripts/spikes/README.md)
(DuckDB-over-Iceberg vs Polars vs the PyIceberg+Python baseline).

## 1. The problem

There are two read paths today, with different performance profiles:

| Workload | Path | Engine | Status |
|---|---|---|---|
| Interactive (chart, alerts, live screener) | bars gateway → ClickHouse | CH (vectorized) | fast; correct path |
| Cold / reproducible (ML training, agents, deep history) | `AdjustedOhlcvReader` | **PyIceberg + Python** | adequate for 1 symbol; bottlenecks at scale |

The cold path — [`adjusted_ohlcv_reader.py`](../app/services/readers/adjusted_ohlcv_reader.py)
— is correct and partition-pruned (`bucket(32, symbol) + month`), but
has three properties that hurt in the cloud at scale:

1. **The union/dedup/sort runs in Python.** `get_bars_union` builds a
   `dict` keyed by `(symbol, timestamp)` then `sorted()`
   ([line 281-288](../app/services/readers/adjusted_ohlcv_reader.py)).
   Single-threaded; fine for one symbol, does not scale to many
   symbols / whole-market.
2. **Per-row Pydantic materialization.** `_arrow_to_bars` instantiates
   one `SilverBar` per bar. A 5y 1m single symbol ≈ 500k objects; an
   ML batch across hundreds of symbols is pathological.
3. **Single-node + cold S3 latency.** No cross-symbol parallelism;
   per-query Iceberg metadata planning + per-file S3 GETs.

And it is **not source-modular**: the union is hardcoded to exactly
two tables with "polygon wins" dedup baked in. Adding a third provider
(e.g. Alpaca) means editing the merge method, not adding config.

## 2. Goals / non-goals

**Goals**
- Cold-path reads that scale from 1 symbol to whole-market without a
  Python object explosion.
- A single place to register a new data source; zero edits to merge
  logic to onboard one.
- Keep the existing public contract (`get_bars`, `get_bars_union`,
  `SilverBarsResponse`) working unchanged for current callers.
- Stay cluster-free for point/bounded reads (no Spark/Trino on the
  request path).

**Non-goals**
- No change to the ClickHouse hot/interactive path (already fast).
- No change to ingestion or the `DataProvider` interface.
- No rename of `Silver*`/`Bronze*` classes here (tracked separately;
  the medallion-naming cleanup is its own change).
- Not building a feature store; that is post-v1 ML architecture.

## 3. Proposed design

### 3.1 Source registry (modularity)

Replace the hardcoded two-table union with a registry of `SourceSpec`s.
Each source declares everything the reader needs:

```python
@dataclass(frozen=True)
class SourceSpec:
    name: str                      # "polygon_adjusted", "schwab_universe", "alpaca_universe"
    table_id: str                  # equities.<name>
    adjustment: Literal[
        "computed",                # needs the corp-actions adjust join (Polygon raw → adjusted)
        "pass_through",            # provider already adjusted (Schwab, adj_factor=1.0)
    ]
    precedence: int                # higher wins on (symbol, timestamp) dedup
    covers: Literal["history", "tip", "both"]  # planning hint for range pruning
```

The reader iterates the registry, scans each source over the pruned
window, and unions by `precedence`. Today's "polygon wins" becomes
`polygon_adjusted.precedence > schwab_universe.precedence`. A new
provider is **one registry entry** — no merge-logic edit. This matches
the v2 insight that *adjustment is a provider-specific concern, not a
pipeline layer*: each source owns its adjustment behavior.

### 3.2 Columnar bulk path (the speed fix)

Split the read API by response size, so bulk paths never touch
Pydantic:

- **`read_arrow(symbols, start, end) -> pyarrow.Table`** — the bulk
  path. The union/dedup/sort happens in a **columnar engine**, not
  Python, and returns Arrow (zero per-row objects). Candidate engine:
  **DuckDB** over the Iceberg/parquet in S3 — in-process, vectorized,
  no cluster; does scan + union + dedup + sort in C++ and hands back
  Arrow. (Polars is the fallback if DuckDB's Iceberg support is
  insufficient; both keep us columnar.)
- **`get_bars` / `get_bars_union` -> SilverBarsResponse** — unchanged
  small-response API for HTTP/MCP. Internally these become a thin
  `read_arrow(...)` + Arrow→Pydantic adapter for a single symbol, so
  there is one code path and one dedup rule.

### 3.3 Engine selection (who does the union)

| Workload | Engine | Why |
|---|---|---|
| Interactive point/range | ClickHouse | already hot, vectorized, sub-10ms |
| Cold single/few symbols | DuckDB-over-Iceberg (in-proc) | no cluster, columnar, returns Arrow |
| Whole-market batch / ML feature build | Athena/Trino or Spark/EMR | distributed; ~1-3s planning → batch only |

The reader picks the engine by request shape (symbol count × range),
but every engine applies the **same `SourceSpec` registry** so dedup
precedence and adjustment semantics are identical regardless of engine.

## 4. Migration (keep current callers working)

1. **[DONE]** `SourceSpec` + registry (`readers/source_registry.py`)
   and `read_arrow()` (`readers/read_arrow.py`, **Polars** engine — see
   §6 q1) landed as additive modules. Levers baked in: lazy
   `LazyFrame` + projection/predicate pushdown (lever 3), single-symbol
   sorted-merge hint (lever 2), streaming collect. Covered by
   `tests/test_read_arrow.py` (real local Iceberg table: union/dedup
   precedence, single-source select, projection, cold-start). Zero
   change to existing callers.
2. **[DONE]** `get_bars_union` now delegates its union/dedup to the
   shared Polars engine (`read_arrow.union_arrow`) — ONE dedup rule
   across reader + bulk path, replacing the per-row Python dict-merge
   (§1.1 bottleneck). The reader keeps its existing PyIceberg scan seam
   (so its lightweight unit tests are unchanged — all 54 reader +
   read_arrow tests green), feeding already-scanned Arrow into
   `union_arrow`. `get_bars` (single source, no dedup) is unchanged.
   `SilverBarsResponse` output is identical; verified vs the real lake
   (AAPL union 13,832 rows, snapshot pinned, sorted, provenance kept).
3. Point ML/whole-market consumers at `read_arrow`; leave HTTP/MCP on
   the Pydantic API.
4. Onboard a third source (Alpaca) as a registry entry to prove
   modularity end-to-end.

   Signoff-gated follow-ups (NOT bundled — each is a storage/behaviour
   change requiring its own go-ahead): materialized union table (lever
   1, dual storage); snapshot-keyed result cache (lever 4); post-union
   gap-fill via provider REST.

Each step is independently shippable and reversible.

## 5. Risks & mitigations

- **DuckDB Iceberg maturity.** Mitigate: capability spike in step 2;
  Polars/parquet-scan fallback; engine is swappable behind `read_arrow`.
- **Dedup correctness across engines.** Mitigate: golden-fixture test
  asserting Arrow path == Python path before any consumer cuts over.
- **Cloud creds/IO for an in-proc engine.** DuckDB reads S3 with the
  same role as PyIceberg; `AWS_PROFILE=stock-lake` requirement is
  unchanged.
- **Scope creep into a feature store.** Out of scope; this is a read
  layer, not precomputed features.

## 6. Open questions (for signoff)

1. **Engine for the bulk path: RESOLVED → Polars-over-PyIceberg.**
   Spiked all three in `scripts/spikes/` (see
   `scripts/spikes/README.md`) against the real lake:
   - **Correctness:** baseline (Python), Polars, and DuckDB-on-planned-
     files are byte-identical on every shape tested (offline fixture +
     live AAPL window) — confirmed via content-hash cross-check.
   - **DuckDB's native `iceberg_scan` over our Glue-backed tables
     fails** (`HTTP 404` resolving the manifest-list path off
     `metadata.json` — `allow_moved_paths=true` does not help). This is
     exactly the spec's Risk #1 (DuckDB↔Glue Iceberg maturity) landing
     in practice, not a hypothetical. DuckDB is usable only via
     PyIceberg-planned file lists (`read_parquet([...])`), which means
     it does **not** apply Iceberg merge-on-read delete files itself —
     a correctness trap for `polygon_adjusted` (merge-on-read) that the
     harness flags (`deletes_present`) but DuckDB can't resolve without
     re-deriving PyIceberg's own delete-application logic.
   - **Polars (`pl.scan_iceberg`) reuses PyIceberg's planning layer
     directly** — same Glue/S3 wiring already proven in production
     (`AWS_PROFILE=stock-lake`), and PyIceberg applies merge-on-read
     deletes correctly by construction. No new integration surface.
   - **Decision: Polars-over-PyIceberg** backs `read_arrow()`. DuckDB
     is not viable today against this repo's Glue catalog without
     reimplementing delete-file handling — revisit if/when DuckDB's
     Iceberg-Glue support matures.
   - **Bench numbers — IN-REGION** (CodeBuild `BUILD_GENERAL1_2XLARGE`
     in us-east-1, 5y window; the decisive run — reproduce via
     `scripts/codebuild/buildspec_lake_read_bench.yml`):

     | Symbols (rows) | Engine | Total | Peak RSS |
     |---|---|---|---|
     | 1 (980K) | baseline | 23.9s | 2.4 GB |
     | | **polars** | **4.5s** | **1.9 GB** |
     | | duckdb | 5.5s | 4.1 GB |
     | 5 (4.1M) | baseline | 79.8s | 7.6 GB |
     | | **polars** | **9.0s** | **2.8 GB** |
     | | duckdb | 8.4s | 9.9 GB |
     | 20 (13.4M) | baseline | 261.5s | 18.7 GB |
     | | **polars** | **18.1s** | **6.4 GB** |
     | | duckdb | 20.9s | 25.6 GB |
     | all | duckdb_iceberg | — | ERROR (Glue 404, every shape) |

     All engines hash-identical at every shape (correctness holds on
     real data). **Polars wins outright:** at 20 symbols it is **14×
     faster than the Python baseline** (18.1s vs 261.5s) AND uses the
     least memory of any engine (6.4 GB vs duckdb's 25.6 GB). Polars
     planning stays flat ~1.1s at every scale (PyIceberg pruning);
     baseline planning grows to ~30s. DuckDB matches Polars on speed
     but is the **worst on memory** (25.6 GB at 20 symbols, 4× Polars —
     would OOM a normal box before Polars does), on top of the native-
     Glue 404 and the merge-on-read-delete gap on its file-list path.
     The baseline's 262s / 18.7 GB at 20 symbols is the object-explosion
     problem (§1.2) measured.

   - **Bench numbers — laptop→WAN** (kept for contrast; *misleading on
     speed*): over the WAN, S3 latency dwarfed compute, so baseline and
     polars looked "tied" (~190s @ 1 sym, ~700-780s @ 5 sym) and only
     the ~55% peak-RSS gap was visible; duckdb's file-list path *timed
     out* on S3 GETs. The in-region run above is what the speed
     decision rests on — never size an engine from a WAN bench.
2. **Whole-market batch:** Athena vs reuse the EMR/Spark path already
   running the weekly adjustment job?
3. **Registry location:** co-locate `SourceSpec` with
   `equities.schemas` (table ids live there) or a new
   `readers/source_registry.py`?
4. **Scope of first PR:** registry-only (modularity, no perf change)
   first, then the columnar path second — or both together?
