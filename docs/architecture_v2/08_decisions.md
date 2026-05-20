# 08 — Open Decisions (Approval Gates)

These need operator sign-off before Phase 1 implementation starts.
Each has a recommended default; amend if needed.

## Gate 1 — Table naming

**Question:** What's the Iceberg catalog/database prefix?

| Option | Catalog name | Table refs |
|---|---|---|
| **Recommended** | `data` | `lake.data.polygon_raw`, `lake.data.polygon_adjusted`, ... |
| `lake` | `lake.lake.polygon_raw` (redundant) |
| `equities` | `lake.equities.polygon_raw` |
| `prod` | `lake.prod.polygon_raw` |

**My pick:** `data` — short, neutral, matches the
"data lake" mental model. Spark refers to tables as
`lake.data.<table_name>` (catalog.database.table).

**Status:** ☐ Pending

## Gate 2 — `adj_factor` column on adjusted tables

**Question:** Include `adj_factor DOUBLE NOT NULL DEFAULT 1.0` in
`data.polygon_adjusted` and `data.schwab_universe`?

**Why include:**
- ML feature engineering can back-compute raw prices when needed:
  `raw = adjusted * adj_factor`.
- Audit trail — you can see "this bar was adjusted by factor 4.0"
  (i.e. there's a 4-for-1 split somewhere after this bar).
- Schwab's pass-through gets `1.0` (no info loss vs adding it later).

**Cost:** One DOUBLE column = ~8 bytes per row. Whole-market 5y
= ~40 GB extra. With zstd compression: ~5 GB extra. Trivial.

**My pick:** YES, include.

**Status:** ☐ Pending

## Gate 3 — Partition strategy

**Question:** Confirm `bucket(N, symbol), month(timestamp)` on the
adjusted/raw tables.

- `data.polygon_raw`: `bucket(32, symbol), month(timestamp)`
- `data.polygon_adjusted`: same
- `data.schwab_universe`: `bucket(16, symbol), month(timestamp)`
  (smaller N because the universe is smaller)
- `data.market_corp_actions`: `month(ex_date)` only — no symbol bucket

**Tradeoffs:**
- Larger N → more files per month → finer single-symbol skipping but
  Iceberg metadata overhead grows. 32 is the sweet spot for 12k symbols.
- Smaller N → fewer, larger files → faster whole-market scans but
  slower per-symbol. 16 is fine for the ~108-symbol universe.

**My pick:** YES, as specified.

**Status:** ☐ Pending

## Gate 4 — Compaction cadence

**Question:** How often do we run Iceberg file compaction?

| Schedule | Cost | When |
|---|---|---|
| Daily | ~$1/day = $30/month | If lake_archive_job runs hourly and creates many small files |
| **Weekly** (recommended) | ~$1/week = $4/month | Sunday morning, before Monday's reads |
| Monthly | ~$1/month | Slow growth datasets |

For:
- `data.schwab_universe`: **weekly** (grows hourly, lots of small files)
- `data.polygon_adjusted`: **monthly** (mostly static, only corp-action rewrites)
- `data.polygon_raw`: **monthly** (frozen + occasional nightly)
- `data.market_corp_actions`: **on-demand only** (small)

**My pick:** Mixed cadence as above; run via EMR Serverless cron.

**Status:** ☐ Pending

## Gate 5 — Compute platform

**Question:** Where do batch Spark jobs run?

| Option | Setup | Cost | When |
|---|---|---|---|
| **EMR Serverless** (recommended) | One-time `create-application` | Pay-per-job, ~$0.30/hr DBU | Production batch (weekly cron) |
| Local Spark | `pip install pyspark` | $0 | Dev / one-shot operator runs |
| EMR on EC2 | Manual cluster lifecycle | EC2 hourly | Heavy ongoing ETL (not needed at our scale) |
| Databricks | Subscription | Per-DBU | If team already has Databricks |

**My pick:** Local Spark for dev + EMR Serverless for production
cron. No standing infrastructure, no DBA work.

**Status:** ☐ Pending

## Gate 6 — Migration risk tolerance

**Question:** Are you comfortable with the 5-phase migration plan as
written?

- Phase 1 (additive Iceberg tables): zero risk to live tier.
- Phase 2 (lake writers redirect): zero risk to live tier; lake is
  dual-written during transition.
- **Phase 3 (live tier cuts over)**: the only point of behavior
  change. One-line revert if the latency gate fails.
- Phase 4 (lake-read endpoint): additive only.
- Phase 5 (drop legacy tables): destructive. **30-day quarantine**
  after Phase 3 before this runs.

Total active engineering: ~8 hours of code; ~3 hours of Spark
wall-clock during Phase 1; 30 days of observation before Phase 5.

**My pick:** YES — this is the right risk profile. Each phase
independently reversible; the only destructive phase is gated by a
month of clean v2 operation.

**Status:** ☐ Pending

## Gate 7 — `/api/v1/lake/bars` endpoint

**Question:** Build the lake-read FastAPI endpoint in Phase 4, or
keep deep-history queries operator-only via DuckDB CLI?

**Build it (Phase 4):**
- Pro: cockpit's chart can zoom out beyond CH retention seamlessly
- Pro: agents / MCP tools can query deep history via HTTP
- Con: ~2 hours of code; DuckDB-on-S3 has cold-start latency

**Keep operator-only:**
- Pro: zero code; just `duckdb` from a shell
- Con: chart can't show 5y of 1-min for new symbols (today's design has this gap anyway)

**My pick:** Build it (Phase 4). The cockpit will need it for chart
zoom-out beyond CH retention as soon as anyone scrolls back >1 year
at 1-min resolution.

**Status:** ☐ Pending

## How to approve

Reply with one of:

- **"All defaults"** — accepts all 7 recommendations as written.
- **"Approved with changes: [gate N: change Y]"** — selectively
  amend.
- **"More questions on [gate N]"** — pause for discussion.

Once approved, Phase 1 commit (`CV1`) lands in the next session.

## Decision log (post-approval)

| Date | Gate | Decision | Approver |
|---|---|---|---|
| (pending) | 1-7 | (pending) | (pending) |

This table gets updated as decisions land so the audit trail is
clear if anything is revisited in 6 months.

## See also

- [01_architecture.md](01_architecture.md) — what these gates affect
- [06_migration.md](06_migration.md) — the phases blocked by approval
