# Vol Premium Screen — specification

**The daily question:** *where are options expensive relative to how much the
underlying actually moves?*

One ranked table, refreshed daily, one row per symbol. It is the answer to
"what should I look at today" that [`/ppl`](ppl.md)
cannot give on its own — PPL describes one symbol you already chose; this ranks
every symbol we have options for and tells you which one to choose.

PPL is the denominator. That is the whole reason the calibration work mattered:
comparing option prices against an *uncalibrated* volatility forecast produces a
number that looks meaningful and is not.

---

## 1. Product

| | |
|---|---|
| **Surface** | `/vol-premium` page + `GET /api/v1/vol-premium` |
| **Cadence** | Recomputed on a schedule (options snapshot → screen), read from ClickHouse |
| **Universe** | Whatever the options ingest covers (currently 40 underlyings) |
| **Row** | symbol · spot · expiry used · implied vol · forecast vol · **ratio** · quality |
| **Default sort** | ratio descending — most expensive options first |
| **Claim** | A *measurement*, like PPL. Not a trade recommendation. |

### 1.1 What it is not

- Not a signal, not an entry, not a strategy. Anything that becomes a trade rule
  goes through the MCPT gate in [`research/mcpt_methodology.md`](../research/mcpt_methodology.md)
  first, pre-registered.
- Not a claim that a high ratio is mispriced. See §6 — a persistent positive
  premium is *expected*, and v1 cannot yet say whether today's reading is
  unusual **for that symbol**.

---

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Rank by ratio `IV / forecast`, not by the difference.** | A 5-point gap means something very different on a 15%-vol utility than a 60%-vol semi. The ratio is scale-free, so a single column ranks the whole universe coherently. The difference is still reported, because it is what a trader sizes against. |
| D2 | **Forecast vol comes from the PPL GJR-GARCH fit, term-matched.** | `variance_ratio(H)` mean-reverts, so a 7-day and a 60-day option are each compared against a forecast for *their own* horizon. Naive √t scaling systematically overstates the long end and would manufacture fake premium at far expiries. |
| D3 | **One target horizon (~30 calendar days) drives the ranking; the full term structure is available per symbol.** | The ranking needs one comparable number per symbol. 30 days is the VIX convention and the most liquid part of the curve. |
| D4 | **ATM implied vol = mean IV of near-the-money contracts, calls and puts.** | Single-contract IV is noisy and skew makes a lone call or put unrepresentative. Averaging across a narrow band around spot is the standard robust estimator. |
| D5 | **Hard liquidity and sanity filters, and the row is dropped — never patched — when they fail.** | 1.1% of contracts carry Schwab's `-999` "no value" sentinel and 3.5% read above 300%. A screen that silently substitutes a fallback for those is worse than one with fewer rows. |
| D6 | **Computed by a scheduled job into ClickHouse; the page reads the table.** | A GARCH fit per symbol is ~0.2s; 40 symbols is ~8s. That is fine for a job and unacceptable for a page load. Matches the existing job/`ingestion_runs` pattern. |
| D7 | **Every row carries a data-quality block, and the page shows it.** | Contracts used, median relative spread, snapshot age. A ranking built on two stale wide-spread quotes must *look* different from one built on forty tight ones. |
| D8 | ~~**No per-symbol historical baseline in v1.**~~ **SUPERSEDED 2026-08-12 — see D9.** | Original reasoning: the Schwab options lake is ~6 weeks old and ClickHouse holds only the latest snapshot. That was true of *Schwab* and irrelevant, because a second source was already backfilled. |
| D9 | **The historical baseline comes from ThetaData, not from accumulating Schwab snapshots.** | `options.thetadata_greeks_eod` already held end-of-day greeks with implied volatility back to **2016-01**, for all 40 underlyings — the backfill had completed (10,160 S3 markers) before this spec was written. Reduced once to `options_atm_iv_daily` (208.7M rows → 87,068), it makes the per-symbol percentile available **now** rather than in six months. The two sources then split by what each is good at: ThetaData is the yardstick, Schwab is today's reading. Units differ — ThetaData sends decimals, Schwab percent — and everything downstream is percent. |

---

## 3. The computation

For each symbol in the options universe:

### 3.1 Implied volatility — what the market is charging

Filter the latest chain to contracts that are actually quotable:

```
snapshot_ts   within FRESHNESS_HOURS (36) of the newest snapshot for that symbol
volatility    1 <= iv <= 300          (excludes the -999 sentinel and solver blowups)
bid           > 0                     (only possible since the quote-parser fix)
ask           > bid
open_interest >= MIN_OPEN_INTEREST (10)
rel_spread    (ask - bid) / mark <= MAX_REL_SPREAD (0.25)
moneyness     |strike / underlying_price - 1| <= ATM_BAND (0.05)
```

Pick the expiry whose calendar days-to-expiry is closest to `TARGET_DTE` (30),
within `[MIN_DTE, MAX_DTE]` = `[14, 60]`. Then

$$\text{IV}_{\text{atm}} = \operatorname{mean}\big(\text{iv}_i\big)$$

over surviving contracts at that expiry, requiring at least `MIN_CONTRACTS` (4)
and **at least one call and one put** — an all-call average is a skew reading,
not an ATM reading.

### 3.2 Forecast volatility — what the stock actually does

From the symbol's daily bars, the PPL GJR-GARCH fit gives `sigma_next`
(next-bar conditional standard deviation) and the mean-reverting
`variance_ratio(H)`. Total variance over `H` trading days is

$$\operatorname{var}(H) = H \cdot \sigma_{\text{next}}^2 \cdot \text{vr}(H)$$

so annualizing (multiply by $252/H$) collapses to

$$\boxed{\;\sigma_{\text{ann}} = \sigma_{\text{next}}\sqrt{252 \cdot \text{vr}(H)}\;}$$

`H` is the option's horizon **in trading days**: Schwab reports calendar days, so
`H = round(dte * 252 / 365)`, floored at 1. Getting this wrong inflates the
forecast by ~17% and would fabricate premium everywhere.

Reported in percent to match the IV convention.

### 3.3 The metric

```
vrp_ratio  = iv_atm / forecast_vol          # the ranking column
vrp_points = iv_atm - forecast_vol          # the sizing column
```

`ratio > 1` — options priced above the stock's own measured behaviour.
`ratio < 1` — options priced below it.

### 3.4 Quality

Every row carries `contracts_used`, `median_rel_spread`, `snapshot_age_hours`,
and `calls`/`puts` counts. A row failing any §3.1 gate is **excluded with a
reason**, and the excluded set is counted in the run summary rather than
silently vanishing.

---

## 4. Architecture

```
app/services/vol_premium/
  schemas.py     VolPremiumRow, VolPremiumScreen, ScreenParams, MODEL_VERSION
  contract.py    VolPremiumBuilder Protocol
  iv.py          chain -> ATM implied vol per expiry (pure)
  forecast.py    PPL GARCH fit -> annualized forecast vol at a horizon (pure)
  service.py     the orchestrator; the only file that does IO
  README.md
  tests/
app/api/routes_vol_premium.py
frontend/src/routes/vol-premium.tsx
```

`iv.py` and `forecast.py` are pure — arrays and rows in, numbers out — so the
whole metric is testable without ClickHouse, S3 or a network
(`docs/standards/service_modules.md`).

Storage: `vol_premium_latest` in ClickHouse, `ReplacingMergeTree` keyed on
`(symbol, as_of)`, written by the job, read by the API.

### 4.1 Failure handling

Result objects, not exceptions (`service_modules.md` rule 4):

| Status | When |
|---|---|
| `ok` | row computed |
| `no_chain` | no contracts survived §3.1 |
| `no_forecast` | PPL could not fit (insufficient bars, or the GARCH boundary case) |
| `stale` | newest snapshot older than `FRESHNESS_HOURS` |

A symbol that fails is reported in the run summary with its reason. The screen
never substitutes a weaker estimate for a missing one.

---

## 5. The page

Ranked table, densest information first:

| Symbol | Spot | Exp | IV | Forecast | Ratio | Gap | Quality |
|---|---|---|---|---|---|---|---|
| TSLA | 341.20 | 32d | 58.4% | 41.2% | **1.42×** | +17.2 | 24 contracts |

- Ratio rendered as a bar as well as a number, so the distribution is scannable.
- Click a row → the PPL fan for that symbol, which is the evidence behind the
  forecast half of the ratio.
- Sort toggle: most expensive / cheapest.
- A persistent header stating §6 in one sentence.
- Quality shown per row; low-quality rows are visually demoted, not hidden.

---

## 6. How to read the number — and the v1 limitation

**A positive premium is normal.** Implied volatility is a risk-neutral price and
carries compensation for bearing crash risk; a forecast is a physical-measure
expectation and does not. Index options typically sit 10–30% above realized
volatility *persistently*. `ratio = 1.2` is therefore not evidence of
mispricing — it may be exactly this symbol's resting level.

The right reading is **relative to the symbol's own history**: "SPY's 30-day
premium is at its 95th percentile of the last ten years."

**Half of what that needs now exists** (D9). `options_atm_iv_daily` holds one
at-the-money implied volatility per symbol per session back to 2016-01, reduced
from the ThetaData archive and validated against known volatility events.

The missing half is a **historical forecast**, so the *ratio* can be
percentiled rather than implied volatility alone. Design, from the handoff:
refit GJR-GARCH parameters monthly and roll the conditional variance daily off
realized returns — ~5,080 fits rather than ~87,000, and closer to how the model
actually runs.

Until that lands, the screen supports one honest use: **cross-sectional** —
which symbols are expensive *relative to each other* today, adjusted for how
much each actually moves.

The page must say so. A ranked list implies "the top one is the opportunity",
and that inference needs the historical comparison.

---

## 7. Build phases

| Phase | Deliverable | Gate |
|---|---|---|
| V1 | `iv.py` + `forecast.py` + tests — the metric, pure and verified | unit tests |
| V2 | `service.py` + CH table + job; CLI to run and inspect | real numbers for 40 symbols look sane |
| V3 | API + page | operator-gated first |
| V4 | Per-symbol historical percentile | needs ~6 months of chain history |
| V5 | Any trade rule | MCPT, pre-registered. Not before V4. |

---

## 8. Open questions

- **Naming.** "Vol Premium" internally and on the nav. "Variance risk premium"
  is the correct term and is jargon; the page subtitle carries the plain-English
  version: *what options cost vs how much the stock actually moves*.
- **Should the ranking use put IV only?** Puts carry the crash premium most
  directly. Deferred — call/put average first, measure later.
