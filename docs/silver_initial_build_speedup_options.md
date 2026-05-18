# Speeding up the initial silver build — your options

The initial `scripts/run_silver_ohlcv_build.py --full` run is the
one slow operation in this pipeline. After it lands, nightly delta
runs take 2–7 min/night for the seed universe (see
[BUILD_JOURNAL.md](BUILD_JOURNAL.md) and the runbook). This doc
covers ways to shorten the **one-shot** initial backfill.

**Baseline (today):** ~18–25 hr local sequential, $0.

## TL;DR

| Combination | Wall-clock | Cost | Setup effort |
|---|---|---|---|
| Baseline (do nothing) | 18–25 hr | $0 | none |
| **A: parallelize, run local** | 3–4 hr | $0 | 2 hr code |
| **B: sequential, run in cloud** | 6–12 hr | $2–5 | 20 min – 1 hr |
| **A + B (recommended)** | **30–60 min** | **~$3** | 2–3 hr |
| C: rewrite in Athena SQL | minutes | ~$1/run | 3–5 days |

**Recommendation: A + B (CodeBuild variant).** Best ROI. The
parallelization helps every future run too (nightly delta,
`--full` rebuilds, TA-5.5 wipe-and-rebuild).

---

## Option A — Parallelize the build [✅ LANDED 2026-05-18, TA-5.1.10]

Each `build_slice(symbol, day)` is independent. The previous
orchestrator's `for symbol in symbols` loop wasted 80–95% of
available capacity on S3 round-trips.

**What landed:**
- New `SilverOhlcvBuild.compute_slice(symbol, day)` — the
  read+normalize+merge half of build_slice with no writes.
- New `_build_window_concurrent()` — fans out compute_slice via
  `asyncio.Semaphore(N)` + `asyncio.to_thread`, then does ONE upsert
  per silver table per day (batched to keep PyIceberg commit churn
  low — concurrent upserts to the same table cause retry storms).
- `build_window` and `run_full` accept `max_concurrency=N` (default
  1 = sequential, opt-in to parallelism).
- CLI flag `--concurrency N` on `scripts/run_silver_ohlcv_build.py`.

**Run it:**
```bash
poetry run python scripts/run_silver_ohlcv_build.py --full \
    --symbols active \
    --concurrency 8 \
    --out-json full_backfill.json
```

**Expected speedup:**

| Concurrency | Speedup factor | Why it caps |
|---|---|---|
| 1 (today) | 1× | sequential baseline |
| 4 | 3–4× | S3 latency hides into other slices' work |
| 8 | 5–8× | sweet spot — S3 saturated, commits manageable |
| 16 | 7–10× | diminishing returns; commit conflicts start |
| 32 | 6–8× | conflict retries cancel out the extra parallelism |

**Sweet spot: N=8.**

**Risk:** PyIceberg upsert commit conflicts (optimistic
concurrency). Mitigated by batching slices into per-day upserts
instead of per-slice. Worst case: a few automatic retries with
backoff — no data loss.

**Effort:** ~2 hr code + tests, self-contained in
`app/services/silver/ohlcv/build.py`.

---

## Option B — Run in the cloud

S3 latency is the dominant cost. From an EC2 / Fargate / CodeBuild
host in the **same AWS region as your S3 bucket**, each S3 GET
drops from ~30–100 ms to ~2–10 ms. With ~520 K S3 GETs in the full
backfill, that's 4–14 hr saved before any code changes.

Three flavors, ordered by setup cost:

### B1 — One-shot EC2 (most flexible)

```bash
aws ec2 run-instances \
    --image-id ami-... \
    --instance-type c6i.2xlarge \
    --iam-instance-profile Name=YourS3ReadWriteRole \
    --user-data file://bootstrap.sh
# bootstrap.sh: clone repo, install deps, run --full, terminate
```

- **Cost:** ~$2–5 for the run.
- **Wall-clock:** ~6–12 hr sequential, ~30–60 min with Option A.
- **Setup:** ~1 hr first time, scripted afterward.

### B2 — ECS Fargate task

Same idea, no instance management. Define a Fargate task with the
repo image, run once, it terminates.

- **Cost:** ~$3–8.
- **Setup:** ~1 hr (task def, image push).

### B3 — AWS CodeBuild (easiest)

- **Cost:** ~$1–3.
- **Setup:** ~20 min. Paste a `buildspec.yml`, point CodeBuild at
  the repo, click Start.
- **Cap:** 8 hr max per job — needs Option A's parallelization to
  fit within that.

---

## Option C — Rewrite in Athena CTAS/MERGE

Express the whole silver build as Trino SQL: read bronze, apply
normalization via window functions, merge with precedence via
`row_number() over (...)`, upsert silver via `MERGE INTO`. Server-
side execution in AWS, no data shipping to the operator.

**Pros:**
- Initial backfill: minutes, not hours.
- No code paths for "operator runs it on their laptop."

**Cons:**
- ~3–5 days to rewrite the normalization math + merge + bar_quality
  computation in SQL.
- Athena scan costs: ~$5/TB. Initial backfill ≈ 5 TB scan ≈ $25/run.
- Loses Python-side iteration speed (test cycle: pytest in seconds
  vs. deploy-Athena-and-eyeball in minutes).
- Provider-pluggability today is "add an entry to `_PROVIDER_ROUTING`";
  in SQL it'd be "edit the merge query."
- The corp-actions cache (loaded once per run in Python) becomes a
  per-row JOIN in SQL, ~5× more scanned data.

**When this becomes worth it:** after the design stabilizes (no more
provider additions, no more schema changes), as a steady-state
infrastructure decision. **Not now.**

---

## Decision matrix

| Your constraint | Pick |
|---|---|
| Want it done tomorrow with minimal effort | A (parallelize, run local overnight) |
| Cloud is fine, want fastest | A + B3 (CodeBuild) |
| Don't want any code changes | B1 (EC2 sequential) |
| Want this to work forever, willing to invest a week | C (Athena rewrite) |
| Already in the same region as S3 + want minimal cost | A only |

---

## What to do next

The parallelization (Option A) is pure code, well-tested, benefits
every future operation. It's the highest-leverage single change.

**If you say yes, I'll build A in one commit:**
- `--concurrency N` flag on `run_silver_ohlcv_build.py` (default 1)
- `build_window_concurrent(symbols, start, end, max_concurrency)`
  method on `SilverOhlcvBuild`
- Per-day batched upserts (one commit per provider per day, not
  per slice)
- Tests covering: no slice dropped at concurrency N, per-symbol
  independence, semaphore limits respected, commit-conflict retry
- Runbook updated with recommended N values

B (cloud) is a follow-up commit when you're ready: a
`scripts/codebuild/buildspec.yml` + a wrapper script. Doesn't
depend on A landing first.
