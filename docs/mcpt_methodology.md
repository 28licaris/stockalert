# MCPT Research Methodology — how strategy validation works here

The operating manual for the statistical layer added 2026-07-02
(EXP-37..39). The enforcement rule lives in
[`standards/trading_subsystem.md`](standards/trading_subsystem.md#statistical-promotion-gate-mcpt);
registered hypotheses + verdicts live in
[`research_hypotheses.md`](research_hypotheses.md); experiment write-ups in
[`strategy_rnd_findings.md`](strategy_rnd_findings.md). Sources: Timothy
Masters' permutation-test framework, via neurotrader888/mcpt and
buildalpha.com/monte-carlo-permutation.

## Why

A backtest result is ONE draw. Before this layer, every EXP verdict was a
point estimate — no null distribution, no multiple-testing control across
36+ experiments of searching. The permutation test asks the only question
that separates edge from luck: *how often does data with the same return
distribution but NO temporal structure produce a result this good?*

## The test stack (run in this order)

| # | Test | Tool | Question | Cost |
|---|------|------|----------|------|
| 0 | Pre-register | edit `research_hypotheses.md`, commit BEFORE running | locks family, grid, universe, window, metric | free |
| 1 | Tier-1 in-sample MCPT | `mcpt_insample.py --null permutation` (1000 perms) | is the optimized in-sample result luck? | ~9s/perm (vectorized) |
| 2 | BH across the battery | `mcpt_report.py --battery '<glob>'` | which p-values survive the family-wise search? | instant |
| 3 | Noise test | `mcpt_insample.py --null noise` (params FIXED at real optimum) | fragile to exact price levels? ROBUST = ≥80% variants profitable AND p5 PF > 1 | ~2s/variant |
| 4 | Random-exit locator | `mcpt_insample.py --null random_exit` | does the edge live in entries or exits? (implementation constraint for Tier-2) | fast |
| 5 | Tier-2 full-engine MCPT | `mcpt_walkforward.py` on windows NOT used in selection | does the real portfolio result (honest fills, costs, sizing) beat the null? **q ≤ 0.05 required** | ~1.5 min/perm-year |
| 6 | DD bands | `dd_resample.py` | risk claims with confidence intervals, not one lucky path | one run + bootstrap |
| 7 | Forward paper | `sim/paper` | the only track record that counts | calendar time |

Learned models add: `mcpt_ranker_labels.py` (label-permutation null —
holdout skill vs models trained on shuffled labels).

## The permutation kernel (what the null preserves/destroys)

`app/services/sim/permutation.py`: each bar → log-space gap
(open vs prior close) + body (high/low/close vs own open); gaps and bodies
shuffled as two independent permutations; prices recomposed from an anchor.
Preserves exactly: return distribution (mean/vol/skew/kurtosis), OHLC bar
geometry, terminal price, and — via ONE master-calendar shuffle shared by
all symbols — cross-sectional correlation (so cross-sectional strategies
face an honest null). Destroys: autocorrelation, trends, regimes, gaps'
calendar alignment. Partial histories (IPO/delist) permute by restriction
of the master shuffle. `start_after` keeps a real prefix for walk-forward.

Blind spots (Masters/Build Alpha, documented): the null PRESERVES the
marginal distribution, so fat-tail/vol-clustering edges can partially
survive permutation; MCPT alone says nothing about costs or data-snooping.
Hence the stack: noise test (#3) for level-fragility, BH (#2) for
snooping, Tier-2 (#5) for costs.

## Compute guidance

- p-value resolution = 1/(n_perms+1): 1000 perms → 0.001; 96 → ~0.01.
- Tier-1 is vectorized over the whole universe — always 1000 perms.
- Tier-2 walltime = (single run time) × n_perms. A 16-yr 1000-name run is
  ~24 min → shard: pre-seed each shard's JSONL with the real row, launch
  with seed spacing ≥1000, merge with `mcpt_report.py --walkforward`.
  Runs checkpoint per-permutation and resume.
- Results land in `data/mcpt/` (gitignored, worktree-fragile). **Copy final
  numbers into `strategy_rnd_findings.md` the day they land.**

## Local vs cloud execution (dual-mode by configuration)

Every runner takes its bars from ClickHouse by default, or from an
immutable parquet snapshot via `--bars <path|s3://…>` — cloud workers
need no database. `scripts/research_bars.py` produces snapshots:

```bash
# once, from the machine with ClickHouse (S3 needs AWS_PROFILE=stock-lake):
poetry run python scripts/research_bars.py export \
  --config configs/<universe>.yaml --start 2006-01-01 --end 2026-06-30 \
  --out s3://$STOCK_LAKE_BUCKET/research/mcpt/<name>.parquet

# any worker, anywhere — no ClickHouse:
poetry run python scripts/mcpt_walkforward.py --config configs/<c>.yaml \
  --bars s3://$STOCK_LAKE_BUCKET/research/mcpt/<name>.parquet \
  --seed <base + worker_index> --n-perms <k> \
  --out s3://$STOCK_LAKE_BUCKET/research/mcpt/<study>_s<worker_index>.jsonl
```

Shards written to `--out s3://…` sync to S3 after EVERY permutation and
resume from S3 on restart — spot interruption costs at most one
permutation. Merge shards with
`mcpt_report.py --walkforward '<downloaded glob>'` (real-row agreement is
enforced). Seed spacing between workers ≥ 1000. Canonical snapshot:
`research/mcpt/universe1000_2006_2026.parquet` (1000-name clean universe).
Economics: a 200-perm 16-yr Tier-2 study ≈ 80 core-hours ≈ ~30-40 min and
a few dollars on one large spot box or an AWS Batch array job — run the
full stack on every survivor rather than rationing it.

Snapshots are immutable study inputs: re-export (new name) rather than
edit; the snapshot file pins the exact data a study read.

## Reading results honestly

- p ≤ 0.05 common minimum; we require **q ≤ 0.05 after BH within the
  registered family** at Tier-2. p > 0.10 = the strategy performs about as
  well on shuffled data — no sequence-dependent edge, stop.
- Retroactive registrations (H-1/H-2) are marked as such: their candidates
  survived many prior searches, so even a pass is an upper bound.
- A Tier-1 pass means "real temporal structure", NOT "tradeable". Costs,
  crowded fills, and crash-clustered signals are Tier-2/paper questions.
