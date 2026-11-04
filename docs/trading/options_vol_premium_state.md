# Options + Vol Premium — data state and known gaps

What the options data actually covers, what is permanently unrecoverable, and
the traps that have cost real time. Current as of **2026-11-03**.

Specs: [`ppl.md`](ppl.md),
[`ppl_mathematics.md`](ppl_mathematics.md),
[`vol_premium.md`](vol_premium.md).
Operational rules (single-writer snapshot loop, token recovery, the 300-symbol
cap) live in [`architecture/runbook.md`](../operations/runbook.md).
Remaining build work is tracked in [`TODO.md`](../TODO.md#vol-premium--remaining-build).

---

## 1. What the two surfaces are

1. **`/ppl`** — simulated forward-price distributions per symbol: a fan of
   Monte Carlo paths plus the price levels read off where they land. A
   measurement instrument, not a signal — the gate is PIT calibration, not
   MCPT, because it makes no directional claim.
2. **Vol Premium screen** — ranks the options universe by *"where are options
   expensive relative to how much the underlying actually moves?"*. PPL is its
   denominator, which is why PPL had to be calibrated first.

`/ppl` is shipped and operator-gated. The Vol Premium **engine, baseline and
percentile all exist**; it has no API route or page, so the only way to reach
it is `scripts/vol_premium_screen.py`.

---

## 2. Data sources — what survives, what is gone

| Source | State | Recoverable? |
|---|---|---|
| **ThetaData EOD options** | `options.thetadata_greeks_eod`: 2017-01-03 → 2026-08-05. SPY alone is 20.2M rows, IV 100% populated | **Permanently ours.** The archive is in the lake, so `options_atm_iv_daily` rebuilds any time with **no subscription**. Only *new* data is lost (subscription lapsed) |
| **Schwab option chains** | `options.schwab_chain_raw`: 5,550 payloads over 20 days, 2026-06-29 → 2026-08-12 | Raw payloads replay fine, but **2026-08-12 → present is permanently unfillable** — `/chains` returns only the current chain |
| **Equity / futures bars** | Stale whenever the system is down | **Yes** — `scripts/schwab_history_backfill.py`, `scripts/futures_history_backfill.py`, then `rebuild_ch_from_lake.py` |
| **Polygon** | Lapsed; minute lake frozen at 2026-06-12 | Not renewing. Daily bars come via REST grouped-daily |

Two consequences worth carrying forward:

1. **The baseline is safe but frozen at 2026-08-05.** Nine and a half years is
   plenty for a percentile; the hole is at the recent end, which matters more
   for regime work than for ranking.
2. **Chain history accrues only while something is running** — see the
   single-writer rule in the runbook.

### The baseline is validated, not just loaded

SPY's history reproduces every known volatility event, which is the check that
the extraction *and* the decimal→percent conversion are both right:

```
2017  median  9.8%              the famously calm year
2018  peak   30.6%              Volmageddon + Q4 selloff
2020  median 22.4%  peak 77.0%  COVID
2022  median 23.0%              the rate-hike bear market
```

---

## 3. Why the percentile is the headline, not the ratio

`vol_premium_history`: 81,015 rows · 40 symbols · 2016-01-04 → 2026-08-05,
built by `scripts/build_vol_premium_history.py`. GJR-GARCH parameters refit
**monthly** with conditional variance rolled **daily** off realized returns
(~5,080 fits rather than ~87,000). No look-ahead, and tested by rewriting the
future after a cut point and asserting no past forecast moves.

| | ratio | percentile | reading |
|---|---|---|---|
| XLI | 1.26× | 64th | normal for XLI |
| UNH | 1.22× | 90th | genuinely unusual |

Ranking on the raw ratio puts XLI above UNH; the percentile corrects it.

**Recorded finding:** the historical ratio comes out p05 0.66 / median 0.97 /
p95 1.39 — the median slightly *below* 1, where the variance risk premium
would suggest above. That premium is quoted against subsequent *realized* vol;
this is against a *forecast*, and a GARCH long-run level fit through
2018/2020/2022 runs a little high in calm regimes. It does not affect the
product: a constant denominator bias cancels out of a per-symbol percentile,
which is exactly why the percentile is the headline.

---

## 4. Gotchas

- **`AWS_PROFILE=stock-lake`** is required for every lake/S3 call. Without it,
  calls hang 45-90s and then fail.
- **ClickHouse must be running** for screen and baseline queries
  (`docker compose --profile ch up -d`). The test suite does not need it.
- **Never `upsert` the options canonical table.** PyIceberg falls back to
  copy-on-write and rewrites every file containing a match — measured >10
  minutes for a single symbol. A full-table rebuild is the correct operation.
- **IV units differ by source.** ThetaData sends a decimal (`0.2141`); Schwab
  sends percent (`21.41`). Everything downstream is percent.
- **Calendar days ≠ trading days.** Schwab reports calendar DTE; the forecast
  is defined over trading bars. Missing the conversion inflates the forecast
  ~17%.

---

## 5. Known gaps — recorded, not hidden

- **`/ppl`'s default interval (30m) has no calibration.**
  `scripts/build_ohlcv_30m.py` is written but was never run, so the research
  table it needs does not exist. The 1h and 1d paths are calibrated; 30m is not.
- **The lake's historical Schwab quotes are still NULL.** Deliberately
  deprioritized — ThetaData covers history better and the rebuild is slow. Raw
  payloads are intact, so it stays recoverable.
- **The screen has no scheduled job.** CLI only. A page needs a job writing to
  ClickHouse first: a GARCH fit is ~0.2s/symbol, so 40 symbols is ~8s — fine
  for a job, unacceptable on page load.
- **PPL keeps a small location bias** on SPY/DIA — about 4.7%/yr against a
  drift estimator whose own standard error is ~4%/yr. That is one SE and
  essentially irreducible; tuning drift until PIT passes would be fitting the
  evaluation window. See `ppl_mathematics.md` §8.5 — deliberately not "fixed".
- **Any return claim needs MCPT**, pre-registered in
  [`research/hypotheses.md`](../research/hypotheses.md) first. No exemption.

---

## 6. Verify from cold

```bash
poetry run pytest -m "not integration" -q
poetry run python scripts/ppl_calibrate.py --symbols SPY,QQQ,IWM,DIA,GLD,TLT \
    --interval 1d --drift-mode unconditional --pooled
AWS_PROFILE=stock-lake poetry run python scripts/vol_premium_screen.py --source lake
```

`--source lake` re-parses raw payloads in memory. `--source ch` refuses symbols
with `no_two_sided_market` when the stored quotes are NULL — that is the system
working, not a bug.
