# Handoff — PPL + Vol Premium (`feat/bayesian-alert-system`)

Written 2026-08-13 as a clean stopping point. Everything below is committed;
the working tree is clean. This is written to be resumable **cold**, by someone
with no memory of the session that produced it.

## What this branch is for

Two surfaces and one bug fix, in dependency order:

1. **`/ppl`** — simulated forward-price distributions per symbol. A fan of Monte
   Carlo paths plus four price levels read off where they land.
2. **Vol Premium screen** — ranks the whole options universe by *"where are
   options expensive relative to how much the underlying actually moves?"*. PPL
   is its denominator, which is why PPL had to be calibrated first.
3. **The Schwab quote-field fix** — every option bid/ask we had ever ingested
   was NULL. Found while building (2).

The product goal behind all of it: something a subscriber opens **daily**. A
fan chart is a reference tool; a ranked list that says *"SNOW's options are at
their 96th percentile of ten years"* is a reason to log in.

---

## Status

### Done and committed

| | Evidence |
|---|---|
| **Parser fix** (`3dcbd5d`) | Verified against stored raw payloads. Recovers quotes on 86–96% of contracts. Regression test pins both field spellings. |
| **PPL / GJR-GARCH + FHS** (`79e7639`) | Passes its PIT calibration gate. At 1h the old model is rejected for 5 of 6 symbols, the new one for 1 of 6. At 1d: FAIL (p=0.000) → PASS (p=0.119). |
| **Vol Premium screen** (`c025ca0`) | Runs end-to-end from CLI. Forecast validated against trailing realized vol: **median ratio 1.01×**. |
| **ThetaData baseline** (this commit) | `options_atm_iv_daily`: **208,658,159 raw rows → 87,068 daily rows**, 40 symbols, 2016-01-04 → 2026-08-05. |

Test suite: **1,790 passing**. The 2 failures in `app/services/news/tests/test_econ.py`
are **pre-existing** — verified failing on a clean tree with this branch stashed.

### The baseline is validated, not just loaded

SPY's reduced history reproduces every known volatility event, which is the
check that the extraction and the decimal→percent conversion are both right:

```
2017  median  9.8%              the famously calm year
2018  peak   30.6%              Volmageddon + Q4 selloff
2020  median 22.4%  peak 77.0%  COVID
2022  median 23.0%              the rate-hike bear market
```

---

## What's left, in order

### Step 3 — historical premium percentile (NOT STARTED)

**Goal:** percentile of the *premium* (IV ÷ forecast), not of implied vol alone.
`options_atm_iv_daily` gives the IV half for every historical day; the forecast
half does not exist yet.

**Design already decided** (do not re-litigate):

- Refit GJR-GARCH parameters **monthly**, roll the conditional variance
  **daily** off realized returns between refits. Naively refitting per day is
  ~87,000 fits ≈ 5 hours; monthly is ~5,080 fits ≈ 17 minutes, and it is closer
  to how the model is meant to run.
- `garch.variance_ratio_from(current_variance, persistence, long_run_variance,
  horizon)` already exists **specifically for this** — it is the closed form
  detached from the fit object so a rolled-forward variance can use it.

**Still to write:** `app/services/vol_premium/history.py` (pure), a build
script, a `vol_premium_history` ClickHouse table, and the run.

### Step 4 — API + page (NOT STARTED)

`app/api/routes_vol_premium.py` and `frontend/src/routes/vol-premium.tsx`.
Spec §5 describes the table. Needs a scheduled job writing to ClickHouse first —
a GARCH fit per symbol is ~0.2s, so 40 symbols is ~8s, fine for a job and
unacceptable for a page load.

### Step 5 — backtest (NOT STARTED, gated)

Ten years of history makes this possible now. **MCPT, pre-registered in
`docs/research_hypotheses.md`, before any return claim.** No exemption.

---

## Blocking decisions — these need a human

### 1. Which server owns the options snapshot loop

**Exactly one process may run it.** Measured 2026-08-12: with the loop enabled
on both `:8000` and `:8001`, concurrent Iceberg appends hit optimistic
concurrency (`branch main has changed`) and **all 40 symbols failed on both
sides with `rows_written=0`** — including the raw payload archive, which is the
recovery source. Two writers is strictly worse than one.

The fix in `3dcbd5d` only takes effect where the code runs. As of this writing
`:8000` runs from the `hungry-joliot-f3a184` worktree with no `--reload`, so it
keeps writing NULL quotes and overwrites any reparse within ~15 minutes.

To hand the loop to this branch: set `OPTIONS_SNAPSHOT_ENABLED=false` on
`:8000`, **then** flip it true in this worktree's `.claude/launch.json`.

### 2. Merge shape

Recommended: **cherry-pick `3dcbd5d` to main now, on its own.** It is four lines
plus tests, sits at the base of the branch, and is the only thing standing
between production and correct options data. Merge the rest once step 4 gives
vol_premium a surface — merging it today adds a module the app cannot reach.

---

## Data sources — what survives, what is gone (verified 2026-11-03)

The system stopped collecting on **2026-08-12**, and the ThetaData subscription
has since lapsed. What that costs, measured rather than assumed:

| Source | State | Recoverable? |
|---|---|---|
| **ThetaData EOD options** | `options.thetadata_greeks_eod` holds **2017-01-03 → 2026-08-05**. SPY alone is 20.2M rows with implied vol 100% populated. | **Permanently ours.** The archive is in the lake, so `options_atm_iv_daily` can be rebuilt any time with **no subscription**. Only *new* data is lost. |
| **Schwab option chains** | `options.schwab_chain_raw` holds 5,550 payloads over 20 days, **2026-06-29 → 2026-08-12**. Stops the day the loop was disabled. | Raw payloads replay fine. But the **2026-08-12 → present gap is permanently unfillable** — `/chains` returns only the *current* chain, so a day not snapshotted is gone. |
| **Equity / futures bars** | Stale since the system stopped. | **Yes** — Schwab REST has history endpoints. `scripts/schwab_history_backfill.py`, `scripts/futures_history_backfill.py`, then `rebuild_ch_from_lake.py`. |
| **Polygon** | Lapsed; minute lake frozen at 2026-06-12. | Not renewing. Daily bars come via REST grouped-daily. |

Two consequences worth carrying forward:

1. **The baseline is safe but its endpoint is frozen at 2026-08-05.** Nine and a
   half years of history is plenty for a percentile; the hole is at the recent
   end, which matters more for regime work than for ranking.
2. **Option-chain history only accrues while something is running.** Unlike
   bars, it cannot be backfilled. Whichever server owns
   `OPTIONS_SNAPSHOT_ENABLED` should be running whenever the market is, and
   every day it is not is a permanent hole.

## Gotchas a fresh session will trip on

- **`AWS_PROFILE=stock-lake`** is required for every lake/S3 call. Without it,
  calls hang 45–90s and then fail.
- **ClickHouse must be running** for the screen and baseline queries:
  `docker compose --profile ch up -d`. The test suite does *not* need it.
- **Never `upsert` the options canonical table.** PyIceberg falls back to
  copy-on-write and rewrites every file containing a match — measured >10
  minutes for a single symbol. A full-table rebuild is the correct operation.
- **IV units differ by source.** ThetaData sends a decimal (`0.2141`); Schwab
  sends percent (`21.41`). Everything downstream is percent.
- **Calendar days ≠ trading days.** Schwab reports calendar DTE; the forecast is
  defined over trading bars. Missing the conversion inflates the forecast ~17%.

---

## Known gaps, recorded rather than hidden

- **`/ppl`'s default interval (30m) has no calibration.** `scripts/build_ohlcv_30m.py`
  is written but was never run, so the research table it needs does not exist.
  The 1h and 1d paths are calibrated; 30m is not.
- **The lake's historical Schwab quotes are still NULL.** Deliberately
  deprioritized — ThetaData covers history better, and the rebuild is slow.
  Raw payloads are intact, so it remains recoverable at any time.
- **Daily bars ran ~2 days stale** during this work (last bar Aug 5 while Aug 6–7
  were trading days). The forecast keys off the most recent move.
- **The screen has no scheduled job.** CLI only.
- **PPL keeps a small location bias** on SPY/DIA — about 4.7%/yr against a drift
  estimator whose own standard error is ~4%/yr. That is one SE and essentially
  irreducible; tuning drift until PIT passes would be fitting the evaluation
  window. Documented in `ppl_mathematics.md` §8.5, deliberately not "fixed".

---

## Verify the branch from cold

```bash
poetry run pytest -m "not integration" -q          # expect 1790 pass, 2 known fail
poetry run python scripts/ppl_calibrate.py --symbols SPY,QQQ,IWM,DIA,GLD,TLT \
    --interval 1d --drift-mode unconditional --pooled
AWS_PROFILE=stock-lake poetry run python scripts/vol_premium_screen.py --source lake
```

`--source lake` re-parses raw payloads in memory and is the only path that
shows quotes until the parser fix is deployed. `--source ch` will correctly
refuse every symbol with `no_two_sided_market` until then — that is the system
working, not a bug.

Specs: [`posterior_predictive_levels_spec.md`](posterior_predictive_levels_spec.md),
[`ppl_mathematics.md`](ppl_mathematics.md),
[`vol_premium_screen_spec.md`](vol_premium_screen_spec.md).
