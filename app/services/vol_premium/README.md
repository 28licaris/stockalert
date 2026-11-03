# vol_premium/ — the Vol Premium screen

**The daily question: where are options expensive relative to how much the
underlying actually moves?**

One ranked table. `/ppl` describes a symbol you already picked; this ranks the
whole options universe and tells you which one to pick.

Spec: [`docs/vol_premium_screen_spec.md`](../../../docs/vol_premium_screen_spec.md).

## The metric

```
ratio  = implied_vol / forecast_vol      # ranks the table — scale-free
points = implied_vol - forecast_vol      # what you size against
```

- **`implied_vol`** — what the options market charges. Mean IV of near-the-money
  calls *and* puts at the expiry nearest 30 days.
- **`forecast_vol`** — what the stock actually does. The PPL GJR-GARCH fit,
  annualized **at that expiry's horizon**.

The ratio leads because a 5-point gap is noise on a 60-vol semiconductor and a
large edge on a 15-vol utility. Only the ratio orders the universe coherently.

## Why PPL had to be calibrated first

This is the payoff for that work. Comparing option prices against an
*uncalibrated* volatility forecast produces a number that looks meaningful and
is not — the ratio is only as trustworthy as its denominator.

Two properties of that denominator matter here specifically:

**Term matching.** `variance_ratio(H)` mean-reverts, so a 7-day and a 60-day
option are each measured against a forecast for their own horizon. The naive
alternative — assume variance grows linearly forever — overstates the far end
and would fabricate premium on every long-dated row.

**Calendar vs trading days.** Schwab reports days-to-expiry in calendar days;
the fit is defined over trading bars. Feeding 30 calendar days in as 30 trading
days inflates the forecast ~17% and manufactures premium everywhere.

Validated against reality: forecast ÷ trailing-63-day realized volatility comes
out at a **median of 1.01×** across a dozen large caps (range 0.74–1.35). The
denominator tracks what the stocks are actually doing.

## Layout

| File | Owns |
|---|---|
| [`schemas.py`](schemas.py) | `ScreenParams`, `VolPremiumRow`, `VolPremiumScreen`, `MODEL_VERSION` |
| [`contract.py`](contract.py) | `VolPremiumBuilder` Protocol — the public interface |
| [`iv.py`](iv.py) | chain → at-the-money implied vol. **Pure** |
| [`forecast.py`](forecast.py) | closes → annualized forecast vol at a horizon. **Pure** |
| [`sources.py`](sources.py) | ClickHouse loaders. Reshapes rows, computes nothing |
| [`service.py`](service.py) | `VolPremiumService` — the orchestrator |

Both estimators are pure, and the service takes its data by injection, so the
whole screen runs on hand-built chains with no database
([`service_modules.md`](../../../docs/standards/service_modules.md)).

## Running it

```bash
poetry run python scripts/vol_premium_screen.py
poetry run python scripts/vol_premium_screen.py --source lake --limit 15
```

`--source lake` re-parses `options.schwab_chain_raw` in memory instead of
reading ClickHouse. It exists because option quotes were silently NULL in
ClickHouse until the 2026-08-09 parser fix (`bidPrice` vs `bid`); until a fresh
snapshot or the reparse backfill lands, the ClickHouse path has no bids and
correctly refuses every symbol.

## Why the raw IV column can't be used directly

Every filter in `iv.py` was written because the real data required it:

| Problem | Share of contracts | Handling |
|---|---|---|
| `-999` sentinel meaning "no value" | 1.1% | dropped |
| Solver blowups above 300% | 3.5% | dropped |
| No two-sided market | varies | dropped |
| Thin open interest / wide spread | varies | dropped |
| Single-sided expiry (all calls or all puts) | varies | whole expiry refused |

That last one matters more than it looks: a lone out-of-the-money put measures
*crash pricing*, not the level of volatility. Requiring both sides is what keeps
an ATM reading from quietly becoming a skew reading.

Nothing is repaired or substituted. A row that fails is **excluded with a
reason**, and the reasons are reported — a symbol quietly missing from a ranked
table reads as "nothing interesting here" rather than "we could not tell".

## The percentile, and why it beats the ratio

`vol_premium_history` pairs each historical day's at-the-money implied vol with
a forecast for the **same horizon**, giving a ratio per symbol-day. Today's
ratio is then ranked against that symbol's own distribution.

Measured over 2016-01 → 2026-08, 40 symbols, 81,015 rows:

```
historical vrp_ratio   p05 0.66   median 0.97   p95 1.39
```

**The median is below 1, which is worth not glossing over.** The variance risk
premium is usually quoted as implied *above subsequent realized* volatility.
This ratio is implied against a *forecast*, and a GARCH forecast whose long-run
level is estimated over a window containing 2018/2020/2022 sits slightly high
in calm regimes. A ~3% bias is consistent with the independent check that
forecast ÷ trailing-63-day realized comes out at a median of 1.01×.

**This is exactly why the percentile is the headline and the ratio is not.** A
constant bias in the denominator shifts every ratio for a symbol by the same
factor and therefore **cancels out of the percentile entirely**. The ranking
against a symbol's own history is robust to it; the raw cross-sectional ratio
is not.

The difference shows up immediately in practice:

| | ratio | percentile | reading |
|---|---|---|---|
| XLI | 1.26× | **64th** | normal for XLI |
| UNH | 1.22× | **90th** | genuinely unusual |

Ranking on the ratio puts XLI above UNH. The percentile corrects it.

`premium_percentile` is `None` — never 50 — when a symbol has no baseline. "No
history" and "unremarkable" are opposite claims and must not share a number.

## Reading the output — and the v1 limitation

**A positive premium is normal.** Implied volatility is a risk-neutral price
carrying compensation for bearing crash risk; a forecast is a physical-measure
expectation that does not. `ratio = 1.2` is not evidence of mispricing — it may
be exactly that symbol's resting level.

The correct reading is against the symbol's *own* history ("SPY's premium is at
its 95th percentile of ten years").

**The baseline for that now exists.** [`baseline.py`](baseline.py) reduces the
ThetaData end-of-day chain archive to `options_atm_iv_daily` in ClickHouse —
208,658,159 raw contract rows collapsed to 87,068 daily at-the-money readings,
40 symbols, 2016-01-04 onward. It is validated, not merely loaded: SPY's series
reproduces 2017's calm, 2018's Volmageddon, COVID's 77% peak and 2022's
sustained repricing.

The two sources divide by what each is good at:

| | Role |
|---|---|
| **ThetaData** | the yardstick — ten years of end-of-day implied vol per symbol |
| **Schwab** | today's reading, refreshed intraday |

**What is still missing is the other half of the ratio.** The baseline gives
historical *implied* volatility; percentiling the *premium* needs a historical
*forecast* too, which does not exist yet. So the screen today still reports a
cross-sectional ranking only.

Until that lands, the page must say so. A ranked list implies "the top row is
the opportunity", and that inference needs the historical comparison.

See [`docs/handoff_ppl_vol_premium.md`](../../../docs/handoff_ppl_vol_premium.md)
for the remaining design — parameters refit monthly, variance rolled daily.

## Failure handling

Result objects, not exceptions (`service_modules.md` rule 4):

| Status | When |
|---|---|
| `ok` | row computed |
| `no_chain` | nothing survived the quality filter; the reason names the gate |
| `no_forecast` | GARCH could not fit (too few bars, or the stationarity boundary) |
| `stale` | newest chain snapshot older than `freshness_hours` (36) |

## Testing

```bash
poetry run pytest app/services/vol_premium/tests
```
