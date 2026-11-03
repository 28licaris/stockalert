# Handoff — PPL + Vol Premium

**Merged to main 2026-11-03** via PR #28 (`feat/bayesian-alert-system`).
Written to be resumable **cold**, by someone with no memory of the session that
produced it.

> ## Read this first: merged ≠ finished
>
> PPL is **complete**. The Vol Premium screen is **not** — it is a working
> engine with no face on it.
>
> | | |
> |---|---|
> | Schwab quote-field fix | **done**, live in production |
> | `/ppl` — model, calibration, page | **done**, operator-gated |
> | ThetaData baseline (`options_atm_iv_daily`) | **done**, 87,068 rows verified present |
> | Vol Premium — estimator, service, CLI | **done** |
> | Vol Premium — historical percentile | **done** — `vol_premium_history`, 81,015 rows, 2016→2026 |
> | Vol Premium — API + page | **not started** (step 4) — the only thing left before it is a product |
>
> It was merged unfinished on purpose: the parser fix was a live production bug
> and could not wait, and the unfinished part **registers no route**, so it is
> inert in the running app rather than a half-exposed surface. The only way to
> reach it today is `scripts/vol_premium_screen.py`.

## What this work is for

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

### Done — in main as of PR #28

| | Evidence |
|---|---|
| **Parser fix** (`3dcbd5d`) | Verified against stored raw payloads. Recovers quotes on 86–96% of contracts. Regression test pins both field spellings. **Confirmed live 2026-11-03**: SPY writing real `bid 9.44 / ask 9.49 / mark 9.47`. |
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

### Step 3 — historical premium percentile ✅ DONE (2026-11-03)

`vol_premium_history`: **81,015 rows · 40 symbols · 2016-01-04 → 2026-08-05**.
Built by `scripts/build_vol_premium_history.py` from
`app/services/vol_premium/history.py` — GJR-GARCH parameters refit **monthly**,
conditional variance rolled **daily** off realized returns (~5,080 fits rather
than ~87,000). No look-ahead, and tested: rewriting the future after a cut
point must not move any past forecast.

Wired through the service and CLI. The payoff, on the first real screen:

| | ratio | percentile | reading |
|---|---|---|---|
| XLI | 1.26× | 64th | normal for XLI |
| UNH | 1.22× | 90th | genuinely unusual |

Ranking on the ratio alone puts XLI above UNH; the percentile corrects it.

**Recorded finding:** historical ratio comes out p05 0.66 / median 0.97 / p95
1.39 — the median slightly *below* 1 where the variance risk premium would
suggest above. That premium is quoted against subsequent *realized* vol; this
is against a *forecast*, and a GARCH long-run level fit through 2018/2020/2022
runs a little high in calm regimes. It does not affect the product: a constant
denominator bias cancels out of a per-symbol percentile, which is the reason
the percentile is the headline and the bare ratio is not.

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

**Resolved in practice 2026-11-03**: `:8000` now runs from the primary checkout
(`/Users/licaris/dev/stockalert`) on merged `main`, so it has the parser fix and
is the single owner of the loop. Every worktree's `.claude/launch.json` ships
with `OPTIONS_SNAPSHOT_ENABLED=false` for this reason.

The rule still stands for anyone adding a second server: disable it everywhere
else **first**, then enable it in one place.

Note the running process caches the Schwab token at startup, so after any
re-authorization `:8000` must be restarted or it keeps presenting the dead one.

### 2. ~~Merge shape~~ — RESOLVED 2026-11-03

Merged whole via PR #28 rather than cherry-picking the fix alone. The deciding
argument: the unfinished half registers no route, so it ships inert, and
splitting would have left a long-lived branch to re-rebase for no safety gain.
See the banner at the top for what "merged" does and does not mean here.

---

## Why collection was dead for three months — the actual cause

Earlier notes in this doc said collection "stopped on 2026-08-12". **That was
wrong, and the correction is worth keeping** because the wrong diagnosis points
at the wrong fix.

The loop never stopped. It ran every 15 minutes and **failed every single
time**, from mid-August until 2026-11-03:

```
Schwab token exchange failed: 400
  {"error_description":"Refresh token is invalid, expired or revoked"}
```

The Schwab **refresh token had expired** (~7-day life). Restarting the server
would not have fixed it; only re-authorization does.

What made it hard to spot: the **live streamer kept working the whole time**,
because its websocket authenticates once at connect and stays up. So
`ohlcv_1m` looked perfectly healthy while every REST call — option chains
included — was failing. *A healthy stream does not mean Schwab auth is healthy.*

Two traps in the recovery, both hit in practice on 2026-11-03:

1. **`SCHWAB_REFRESH_TOKEN` in `.env` overrides the token file.** Re-auth writes
   `data/.schwab_refresh_token`, but a stale value pinned in `.env` silently
   wins. Update or unset it.
2. **Each worktree has its own `.env` and `data/`.** Re-authorizing in the
   primary checkout does nothing for a worktree, which will keep failing with
   the old token and look like the re-auth did not work. Copy
   `data/.schwab_refresh_token` and the `.env` line across.

Verified working again 2026-11-03: SPY 1,080 contracts, 1,682 rows, real
quotes (`bid 9.44 / ask 9.49 / mark 9.47`, iv 17.35) — which also confirms the
parser fix in production.

## Data sources — what survives, what is gone (verified 2026-11-03)

Collection produced nothing between 2026-08-12 and 2026-11-03 (see above), and
the ThetaData subscription has since lapsed. What that costs, measured rather
than assumed:

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
