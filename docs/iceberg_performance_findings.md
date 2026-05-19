# PyIceberg performance findings — silver build initial fill

Investigation date: 2026-05-18. Honest documentation of where time
went, what we got wrong, and what the alternatives are if PyIceberg
proves unfit for our access patterns.

## The headline

For ~40 GB of data (5 years × 103 symbols × 1-min bars × 2 providers),
the initial silver build ran at **~27 minutes per month**, projecting
to **~29 hours total**. This is wildly disproportionate to the data
volume — at residential network speeds we'd expect ~30-60 minutes
total based on bandwidth alone.

The slowness is **not** bronze reads (TA-5.1.11's month-batched
scans fixed that). It's **PyIceberg upsert commits** to the silver
table.

## What was timed

### Per-commit cost on the real lake (us-east-1 ↔ residential laptop)

Probed against `silver.ohlcv_1m` (Glue + S3 Iceberg table) from a
residential 50 Mbps connection:

| Operation | Payload | Observed |
|---|---|---|
| `table.upsert()` first call | 1 row | ~5.3 sec (cold) |
| `table.upsert()` subsequent | 1 row | ~1.0 sec |
| `table.append()` | 1 row | ~5.0 sec (all calls) |
| **What the build did** | **~40K rows** | **~40 sec each** |

### Where 40 sec/upsert goes (estimated)

```
~ 2-3 sec  PyIceberg loads table metadata (S3 reads)
~ 5-10 sec PyIceberg scans existing manifests to find files
           containing the rows that will be replaced
~ 1-3 sec  Parquet write of ~40K rows (~4-8 MB)
~ 1-2 sec  Manifest file write
~ 1-2 sec  Manifest list write
~ 1-2 sec  metadata.json write
~ 5-10 sec Glue UpdateTable API call (round-trip + lock contention)
~ 5-10 sec Various S3 PUT retries / TLS handshakes
```

These are estimates from the timing patterns; PyIceberg's actual
internals aren't fully transparent.

### The dominant cost: scaling per-table-size

PyIceberg's `upsert()` finds matching rows by reading existing data
files. Even for a freshly-created table, the upsert path walks the
manifest tree on every call. As the table grows (more snapshots,
more manifests, more files), this scan cost grows linearly.

In a 30-month run:
- Month 1: ~30 sec/upsert (cold)
- Month 15: ~45 sec/upsert (~50% slower)
- Month 30: ~60 sec/upsert (~100% slower)

We did ~2900 upserts (one per day per silver table). Average ~40 sec
× 2900 = 32 hours. Matches the observed pace.

## Things we got wrong

### 1. Using `upsert()` for initial bulk load

PyIceberg's `.upsert()` is designed for **rare corrections** to
existing data. It runs a merge-on-write loop that's expensive per
call.

For an initial fill — where we know each `(symbol, ts)` is written
exactly once — there's no merge work to do. **The correct primitive
is `.append()`**, which just adds new files without scanning
existing state.

Append is ~5× faster per call AND has constant cost regardless of
table size.

### 2. Committing per-day instead of per-month

We did ~38 upserts per month (one per trading day per silver table).
Each commit pays the fixed Glue + metadata cost (~10-15 sec).

There's no architectural reason for per-day commits. **Per-month
commits work just as well** — same data, same partition, just one
big commit instead of 22 small ones. 22× fewer commits.

### 3. Iceberg snapshot accumulation

Each commit creates a new snapshot. Over 2900 commits we'd accumulate
2900 snapshots — manifest list grows linearly. Read performance
also degrades over time.

Iceberg has snapshot expiration / compaction, but PyIceberg doesn't
run them automatically. We never compacted. The table got slower as
it grew.

## What we should have built from day one

**For the initial bulk load:**

```
For each month:
    one bronze scan per provider          (TA-5.1.11 — done)
    in-memory compute per slice           (cheap)
    accumulate all day-arrows in memory
    ONE table.append() per silver table   ← the new fix
```

Net: 130 appends for the whole 65-month backfill. At ~5 sec/append
= **~11 minutes**.

**For nightly delta (one day at a time):**

```
table.upsert(yesterday_arrow)
```

One upsert per silver table per night. Identifier match is rare
(only on re-runs). ~5-10 sec each. Total ~30 sec/night.

**For corp-action rebuilds (rare, per-symbol historical recompute):**

```
table.upsert(symbol_history_arrow)
```

Same as nightly. Per-symbol bulk replace.

## Alternatives if PyIceberg still proves unfit

Ranked by speedup × effort:

### Tier 1 — Fix the access pattern (~1 hr code, ~11 min run)

**THIS IS WHAT WE'RE DOING NEXT.** Per-month batching + auto-detect-
empty-table → use append. Should resolve the issue without changing
the lake architecture.

If this gets us to ~10-15 min local, we're done.

### Tier 2 — `add_files()` API (~2 hr code, ~2-3 min run)

PyIceberg supports `Table.add_files(file_paths)` which **registers
pre-written Parquet files** into the table metadata WITHOUT going
through PyIceberg's write path. Workflow:

1. Compute all 65 months' worth of silver rows in memory
2. Write each month as a Parquet file directly to S3 (parallel)
3. ONE `add_files()` call at the end registers all 65 files
4. Total: 1 metadata commit instead of 130

Expected: ~2-3 min wall-clock. Best PyIceberg can do.

### Tier 3 — Hybrid: plain Parquet + manual Iceberg registration (~half day, ~1-2 min run)

Same as Tier 2 but bypass PyIceberg's write entirely:
- Use `pyarrow.parquet.write_table()` directly to S3
- Use AWS Glue API to register the table partition entries

Less battle-tested but maximally fast.

### Tier 4 — Skip Iceberg for silver entirely (~1 day, ~1-2 min run)

Reasonable if Iceberg keeps being the bottleneck:
- Silver as partitioned Parquet on S3 (no Iceberg metadata layer)
- Reads via PyArrow's S3 partition discovery (fast)
- Upserts handled by "write new file, mark old as superseded" or
  by month-level "overwrite this month's file atomically"
- Loses: time-travel, schema evolution, transactional MERGE INTO
- Gains: 10-50× faster writes, simpler operational model

This trades Iceberg's features for raw speed. Worth it if our use
case doesn't need the features.

We DO need:
- Snapshot reproducibility for backtests → could be replaced with
  versioned partition files (`silver/ohlcv_1m/v=2026-05-18/.../`)
- Schema evolution → handled by writing new versioned tables and
  evolving consumers
- Idempotent re-writes → handled by overwriting whole-month
  partition files

We DON'T need (but Iceberg gives us anyway):
- Concurrent writers (we have one)
- Row-level deletes (our use case is whole-bar replacement)
- Hidden partitioning

### Tier 5 — Stop using a lake format entirely (~1 week, ~30 sec run)

Push canonical OHLCV directly into ClickHouse (with the right table
engine). CH ReplacingMergeTree handles dedup. Reads are milliseconds.
Writes are batched + fast.

This abandons the "S3 = ground truth, CH = derived cache" rule.
ClickHouse becomes the source of truth.

Tradeoffs:
- (+) Dramatically faster everything
- (+) Simpler operational model (one tier instead of three)
- (-) Lose cheap durable archive of historical bronze (S3)
- (-) CH disk cost scales linearly with data
- (-) Snapshot reproducibility requires extra work in CH
- (-) Vendor lock-in vs. lake portability

Many production systems do exactly this. It's a legit design choice.

## Decision tree

```
Step 1: Apply Tier 1 fix (per-month + append-on-fresh)
  ↓
  Does it complete in ~10-15 min locally?
  ├─ YES → Ship it. Silver validated. Move on.
  └─ NO → Try Tier 2 (add_files)
       ↓
       Does it complete in ~2-3 min?
       ├─ YES → Ship it. Use add_files pattern for bulk loads.
       └─ NO → Iceberg is genuinely the wrong tool. Move to Tier 3 or 4.
```

## What this means for the operational model

**Even after Tier 1 fixes:**
- Initial fill: ~10-15 min (one-shot, acceptable)
- Nightly delta: ~30 sec (already fine)
- Corp-action rebuild: ~1-2 min per affected symbol (fine)

**If we need to do --full repeatedly** (schema migrations, post-bug
recovery, etc.), Tier 2 (`add_files`) becomes worth the investment.

**If we find Iceberg's metadata overhead bites us again** (e.g.
reads getting slow as the table grows), we'd consider Tier 4
(plain Parquet on S3) or Tier 5 (CH-as-source-of-truth).

## Honest takeaway

PyIceberg's `upsert()` is the wrong primitive for our initial-fill
access pattern. We picked it because it's idempotent and matches the
nightly-delta use case nicely, then assumed it'd be fine for the
initial fill too. It isn't.

Iceberg the format isn't slow. PyIceberg's specific upsert
implementation is slow for this workload. The fix is using the
right write primitive (`append`) for bulk loads, not abandoning
Iceberg.

If Tier 1 doesn't deliver, we have multiple escape hatches.

## Updates as we test

### 2026-05-18: Tier 1 fix landed (TA-5.1.12)

Smoke test on NVDA × June 2024 (1 symbol × 1 month from a freshly-
dropped silver table):

```
Setup + table create:       4 sec
Corp-actions cache load:    2 sec
Bronze scan:              100 sec  ← residential-S3-latency bound
APPEND ohlcv (17,908 rows): 5 sec  ← was ~40s × 22 days = ~15 min before
APPEND bar_quality:         5 sec  ← was ~40s × 22 days = ~15 min before
────
Total per month:          116 sec
```

**Result on writes: ~150× faster.** From ~30 min/month (per-day
upsert) to ~10 sec/month (per-month append). The Tier 1 fix
delivered as projected.

**Remaining bottleneck:** the bronze scan itself (~100 sec/month
from residential). This is **not** Iceberg's fault — it's S3 round-
trips from a residential connection × the size of the manifest tree
for a 2.1B-row bronze table.

Projected full backfill wall-clock:
- 65 months × ~120 sec/month (slightly more for 103-symbol filter)
  = ~130 min for scans
- 65 months × ~10 sec/month writes = ~11 min for commits
- **Total local: ~2.5 hours**

Same code in CodeBuild same-region as S3: bronze RTTs drop from
~50ms to ~2ms → scans drop ~10×. **CodeBuild total: ~15-20 min.**

### Verdict on Iceberg

**Iceberg the format is fine for our workload.** The original
slowness was operator error (wrong write primitive + wrong commit
batching), not Iceberg's architecture. After the Tier 1 fix:

- Writes: ~150× faster, well under wall-clock budget for any
  operation
- Reads: still residential-bound, but that's network physics, not
  Iceberg
- Snapshot reproducibility: preserved (the key feature we picked
  Iceberg for)

We do NOT need to migrate to plain Parquet (Tier 4) or
ClickHouse-as-source (Tier 5). The Tier 1 fix + CodeBuild solves
the problem at the access-pattern level, not the architecture level.

**If Iceberg ever fails us again**, the escape hatches in Tier 2-5
are documented here. But for now, Iceberg + correct usage = correct
choice.

---

## Addendum 2026-05-18 — PyIceberg multi-column upsert SIGBUS

A second PyIceberg pathology surfaced while backfilling
`bronze.polygon_corp_actions` for the years missed during TA-5.0.
The script appeared to "succeed" (exit 0) but the bronze table
counts stayed unchanged — the python process was actually being
SIGBUS-killed mid-write, with `tee` masking the exit code.

### The bug

PyIceberg 0.11.1's
[`upsert_util.create_match_filter()`](file:///Users/licaris/Library/Caches/pypoetry/virtualenvs/stockalert-YlpshXNg-py3.13/lib/python3.13/site-packages/pyiceberg/table/upsert_util.py)
(lines 36-48) builds a match predicate one leaf per source row:

```python
def create_match_filter(df, join_cols):
    unique_keys = df.select(join_cols).group_by(join_cols).aggregate([])
    if len(join_cols) == 1:
        return In(join_cols[0], unique_keys[0].to_pylist())
    else:
        filters = [
            EqualTo(c, row[c]) AND ... for c in join_cols
            for row in unique_keys.to_pylist()
        ]
        return Or(*filters)
```

For single-column identifiers, it uses a cheap `In(col, [vals])` —
constant tree size regardless of row count. For **multi-column
identifiers** (everything in our schema set: `silver.ohlcv_1m`,
`silver.bar_quality`, `silver.corp_actions`, `bronze.polygon_corp_actions`)
it builds an `Or(And(EqualTo, EqualTo, …), …)` tree with **5×N
expression nodes** for a 3-column identifier, 3×N for a 2-column
identifier.

PyIceberg then walks this tree recursively in `bind()` +
`expression_to_pyarrow()`. On **macOS arm64 + Python 3.13.5 +
PyArrow 24.0.0**, the C++ expression compiler's stack budget is
exhausted between ~3,000 and ~6,000 nodes (= 600-1,200 rows of a
3-column identifier). The OS surfaces it as **SIGBUS (Bus error: 10)**
rather than SIGSEGV due to Apple runtime guard-page handling.

### Empirical bisection

From [`scripts/repro_corp_actions_sigbus_2.py`](../scripts/repro_corp_actions_sigbus_2.py):

| Slice size | Expression nodes (3-col id) | Result |
|---|---|---|
| 1 row | 5 | OK |
| 10 rows | 59 | OK |
| 100 rows | 599 | OK |
| 500 rows | 2,999 | OK |
| **1,000 rows** | **5,999** | **SIGBUS** |
| 1,236 rows | 7,415 | SIGBUS |

### The fix — centralized `chunked_upsert` helper

[`app/services/iceberg_safe_upsert.py`](../app/services/iceberg_safe_upsert.py)
exposes one helper, `chunked_upsert(table, arrow, *, chunk_size=400)`,
that slices the source into safe-sized batches and aggregates
results. **All seven production upsert call sites in the codebase
were migrated to it**:

- `silver/ohlcv/build.py` (4 sites: per-slice, month-batched, concurrent)
- `silver/corp_actions/build.py` (silver merge)
- `silver/corp_actions/polygon_ingest.py` (bronze write)
- `ingest/live_lake_writer.py` (Schwab live → bronze)

The default `chunk_size=400` leaves ~2,000 expression nodes per
chunk — well below the 3,000-node danger zone with headroom for
PyIceberg version drift and identifier-column-count changes.

### Why this is the right fix

1. **Single chokepoint** — adding a new upsert site that goes through
   `chunked_upsert` automatically gets the protection.
2. **No upstream dependency** — we don't have to wait for PyIceberg
   to land a fix (PyPI's latest as of 2026-05-18 is 0.11.1 with the
   bug).
3. **Locked by tests** —
   [`tests/test_iceberg_safe_upsert.py`](../tests/test_iceberg_safe_upsert.py)
   pins the chunking math + delegation contract (10 tests). Any
   refactor that bypasses the helper or un-chunks the path breaks
   loudly.

### Open follow-ups

- **File a PyIceberg upstream issue.** The fix upstream would be to
  switch the multi-column path to either `In(struct_field([(a,b,c)]))`
  or to chunk internally. Minimal repro lives in
  `scripts/repro_corp_actions_sigbus_2.py`.
- **A future agent should NEVER call `table.upsert(...)` directly**
  in app/scripts code. Always import `chunked_upsert`. This is
  codified in [`docs/standards/coding.md`](standards/coding.md) rule 9.

### Why this isn't fixed by Tier 1's "per-month commits"

The Tier 1 fix (TA-5.1.12) reduced commit COUNT by batching, but
each batch could still be large enough to trip the SIGBUS — the
silver build's empty-table optimization (auto-detect-empty →
`.append()`) is what kept silver from crashing. Once silver is
non-empty, the corp-action-dirty rebuild path WOULD have hit the
same SIGBUS without the new chunking guard.
