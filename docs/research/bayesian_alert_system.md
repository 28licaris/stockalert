> **Status (recorded 2026-11-05): half of this shipped, half was never built.**
>
> | | |
> |---|---|
> | **Engine B — Posterior Predictive Levels** (§1, §7 onward) | **SHIPPED.** Lives in `app/services/ppl/`. Current docs: [`../trading/ppl.md`](../trading/ppl.md), [`../trading/ppl_mathematics.md`](../trading/ppl_mathematics.md), [`../trading/vol_premium.md`](../trading/vol_premium.md). Those supersede this file wherever they disagree. |
> | **Engine A — Bayesian posterior / vibration detector** (§4, §5, §6) | **NOT BUILT.** There is no `vibration` anywhere in the codebase. This is the only record of that design. |
>
> Kept for Engine A. It is committed here rather than under `docs/trading/`
> because it is unbuilt design, not a description of the running system —
> see [`../README.md`](../README.md) ("no doc for unshipped work"). Had it
> been deleted it would have been lost outright: it had never been
> committed, existing only as an untracked file in one checkout.
>
> Before building Engine A, pre-register it in
> [`hypotheses.md`](hypotheses.md) and gate any return claim on
> [`mcpt_methodology.md`](mcpt_methodology.md). The triple-barrier labelling
> in §6 is a directional claim and is exactly what the MCPT gate exists for.

# Bayesian Vibration + Posterior Predictive Alert System
### Implementation Plan v1.0

---

## 0. What you are actually building

The source material describes **two distinct things**, and it's worth being clear that they are different engines that happen to share a Bayesian vocabulary:

| | Engine A — Discrete Signal | Engine B — Posterior Predictive Levels |
|---|---|---|
| **Source** | The BTS guide (vibration windows, long/short signal) | The GDXJ fan chart graphic |
| **Output** | One decision: long / short / flat | A *distribution* of future paths + extracted price levels |
| **Question answered** | "Should I be in a position right now?" | "Where is price likely to trade, and where should I take profit?" |
| **Math** | Naive Bayes / logistic posterior over a binary outcome | Monte Carlo simulation from a fitted conditional model |
| **Horizon** | 5–15 days (daily bars) | Intraday, 30-min bars, one session |

They compose well: **Engine A decides direction and whether to be in. Engine B decides where the targets and stops go.** Build A first, then B.

> ⚠️ Neither engine is the vendor's actual system. You are reconstructing something inspired by a description that deliberately omits the math. Treat every parameter below as a starting hypothesis to be validated, not a spec.

---

## 1. Reading the graphic

The GDXJ chart is a **posterior predictive fan**. Understanding exactly what it is tells you how to build it.

**What's on it:**
- **Anchor:** 107.00, current price, left edge
- **~200–500 simulated paths**, each one a 30-minute-bar random walk forward through one session
- **Color** appears to encode terminal outcome (cyan/blue = ended high, red/magenta = ended low) — purely a visualization choice
- **Green levels (108.49, 110.11):** upside levels extracted from the path density
- **Red levels (103.76, 101.22):** downside levels
- **Parameter box:** `P-Sar[0.03,0.227]`, `TSI[10,13,13]`, `MACD[9,16,9]`, `Profit level 0.85%`

**What the parameters mean for your build:**
- The three indicators are the **conditioning set**. The simulation isn't a plain random walk — its drift and volatility are conditioned on the current state of PSAR, TSI, and MACD. That is the Bayesian part: you're sampling from `P(future path | current indicator state)`.
- `Profit level 0.85%` is the **minimum move worth trading** on that timeframe. It's the filter that decides whether a level is far enough away to be worth alerting on. On 30-min GDXJ, anything closer than ~0.85% is noise.
- The nested levels (two green, two red) are almost certainly **quantile pairs** — e.g. the 60th/85th percentile of terminal price on the upside, 40th/15th on the downside. Inner = likely target, outer = stretch target.

**The critical thing this visualization does well:** it refuses to give you a point forecast. Note that the fan's downside tail runs to ~91 while the upside runs to ~119. Any single "price target" throws that asymmetry away. Your alerts should carry the distribution, not a number.

---

## 2. System architecture

```
                    ┌─────────────────┐
                    │   Data Layer    │  OHLCV + options chain
                    └────────┬────────┘
                             │
         ┌───────────────────┼───────────────────┐
         │                   │                   │
    ┌────▼─────┐       ┌─────▼──────┐     ┌─────▼──────┐
    │ Vibration│       │  Feature   │     │ Volatility │
    │ Detector │       │ Extraction │     │  Estimator │
    └────┬─────┘       └─────┬──────┘     └─────┬──────┘
         │                   │                   │
         └─────────┬─────────┘                   │
                   │                             │
          ┌────────▼─────────┐         ┌─────────▼──────────┐
          │  ENGINE A        │         │  ENGINE B          │
          │  Bayesian        │────────▶│  Posterior         │
          │  Posterior       │  drift  │  Predictive Sim    │
          │  (direction)     │         │  (paths + levels)  │
          └────────┬─────────┘         └─────────┬──────────┘
                   │                             │
                   └──────────┬──────────────────┘
                              │
                    ┌─────────▼──────────┐
                    │  Signal Fusion &   │
                    │  Position Manager  │
                    └─────────┬──────────┘
                              │
                    ┌─────────▼──────────┐
                    │  Alert Dispatcher  │  + chart render
                    └────────────────────┘
```

**Repo layout:**

```
bayes-alerts/
├── data/
│   ├── ingest.py          # vendor adapters, caching
│   └── universe.py        # SPY QQQ IWM GLD SLV GDX GDXJ USO UNG TLT IBIT
├── features/
│   ├── vibration.py       # pivot detection
│   ├── indicators.py      # PSAR, TSI, MACD, RSI, ATR
│   ├── options.py         # IV rank, skew
│   └── discretize.py      # continuous → bins
├── engines/
│   ├── posterior.py       # Engine A
│   ├── simulator.py       # Engine B
│   └── levels.py          # density → price levels
├── portfolio/
│   ├── sizing.py          # 20-tranche allocator
│   └── risk.py            # stops, violation logic
├── backtest/
│   ├── labels.py          # triple-barrier
│   ├── cv.py              # purged walk-forward
│   └── metrics.py         # Brier, reliability, PnL
├── alerts/
│   ├── dispatcher.py
│   └── render.py          # fan chart
└── config/
    └── params.yaml
```

---

## 3. Data layer

**Requirements:**
- Daily OHLCV, 10+ years, split/dividend adjusted, for Engine A
- 30-minute bars, 2+ years, for Engine B
- Options chain snapshots (IV surface) — daily is enough

**Non-negotiable:** store bars with an `as_of` timestamp separate from the bar timestamp. Adjusted price series get **retroactively rewritten** by every split and dividend. If you backtest on today's adjusted series you are using information that did not exist at the time. This single issue invalidates more backtests than any other.

**Practical vendors:** Polygon or Databento for bars, ORATS or CBOE DataShop for options history. Yahoo/yfinance is fine for prototyping and unusable for validation.

---

## 4. Vibration detector (Engine A input)

A "vibration window" as described is a **significant fractal pivot**. Two conditions:

```python
def detect_pivots(df, n=4, k=15, atr_mult=0.5):
    """
    n: bars on each side that must be lower (higher)
    k: lookback for the 'exceeds obvious recent HODs' test
    atr_mult: minimum prominence in ATR units
    """
    highs, lows = df['high'], df['low']
    atr = df['atr14']

    is_ph = (
        (highs == highs.rolling(2*n+1, center=True).max()) &
        (highs > highs.shift(1).rolling(k).max()) &
        ((highs - lows.rolling(k).min()) > atr_mult * atr)
    )
    is_pl = (
        (lows == lows.rolling(2*n+1, center=True).min()) &
        (lows < lows.shift(1).rolling(k).min()) &
        ((highs.rolling(k).max() - lows) > atr_mult * atr)
    )
    return is_ph, is_pl
```

**⚠️ Look-ahead trap:** `center=True` uses `n` future bars. A pivot is only *confirmed* `n` bars after it forms. In live and in backtest, shift confirmation forward by `n` bars. Only the confirmed version may feed a signal. Keep both columns and name them explicitly — `pivot_high_formed` vs `pivot_high_confirmed` — so you can never confuse them.

**Parameters to sweep:** `n ∈ {2,3,4,5}`, `k ∈ {10,15,20,30}`, `atr_mult ∈ {0.3,0.5,0.8}`. Choose per-symbol, not globally — GDX and TLT do not have the same pivot rhythm.

**Multi-timeframe:** run the same detector on daily, weekly, and 4-hour. Agreement across timeframes becomes a feature ("fractal, like an EW count").

---

## 5. Feature / evidence layer

Every feature must be **discretized into 3–5 bins** for naive Bayes. Bin edges fit on training data only.

| Feature | Definition | Bins |
|---|---|---|
| `pivot_state` | confirmed high / confirmed low / none | 3 |
| `bars_since_pivot` | count since last confirmed | 0-2, 3-5, 6-10, 11+ |
| `dist_from_pivot_atr` | (close − pivot) / ATR | 5 quantile bins |
| `rsi_zone` | RSI(14) | <30, 30-45, 45-55, 55-70, >70 |
| `macd_state` | sign of MACD hist + sign of its slope | 4 |
| `tsi_state` | TSI(10,13) vs signal(13) | above/below × rising/falling |
| `psar_state` | close vs PSAR(0.03, 0.227) | above / below |
| `rsi_divergence` | price HH + RSI LH (or inverse) | bull / bear / none |
| `vol_regime` | ATR(14) / ATR(60) | low / normal / high |
| `iv_rank` | IV percentile, 252d | 4 quartiles |
| `skew_25d` | 25Δ put IV − 25Δ call IV, z-scored | 3 |
| `trend_ctx` | close vs SMA(200) | above / below |

Note that the PSAR/TSI/MACD parameters from the graphic — `(0.03, 0.227)`, `(10,13,13)`, `(9,16,9)` — are all non-default. Someone tuned those. Use them as your starting values but re-fit; parameters tuned for 30-min GDXJ won't be optimal for daily SPY.

**Feature independence:** naive Bayes assumes conditional independence. MACD state and TSI state are heavily correlated, and both correlate with PSAR. Options:
1. Drop redundant features (check pairwise mutual information, cut anything above ~0.3)
2. Keep them and accept overconfident posteriors, then **calibrate the output** (see §11)
3. Move to regularized logistic regression, which handles correlation properly

Recommendation: start with (1)+(2), graduate to (3). The logistic version is barely more code and strictly better behaved.

---

## 6. Engine A — Bayesian posterior

**Labels (triple-barrier):** for each bar, over the next 15 trading days, did price first touch `+1.5×ATR` (label 1), `−1.5×ATR` (label 0), or neither (drop or label by sign of terminal return)? This is better than a fixed 10-day return because it matches how you'd actually manage the trade.

**Fitting:**

```python
import numpy as np
from collections import defaultdict

class BayesSignal:
    def __init__(self, alpha=1.0):
        self.alpha = alpha          # Laplace smoothing
        self.llr = defaultdict(float)
        self.prior_logit = 0.0

    def fit(self, X_binned, y):
        p = y.mean()
        self.prior_logit = np.log(p / (1 - p))
        for col in X_binned.columns:
            for val in X_binned[col].unique():
                m = X_binned[col] == val
                n_up = ((y == 1) & m).sum() + self.alpha
                n_dn = ((y == 0) & m).sum() + self.alpha
                tot_up = (y == 1).sum() + self.alpha * m.nunique()
                tot_dn = (y == 0).sum() + self.alpha * m.nunique()
                self.llr[(col, val)] = np.log((n_up/tot_up) / (n_dn/tot_dn))

    def predict_proba(self, row):
        logit = self.prior_logit
        contrib = {}
        for col, val in row.items():
            w = self.llr.get((col, val), 0.0)
            logit += w
            contrib[col] = w          # keep for explainability
        return 1 / (1 + np.exp(-logit)), contrib
```

Keeping `contrib` matters more than it looks — every alert should be able to say *"long 0.71, driven by confirmed pivot low (+0.34), bullish RSI divergence (+0.22), IV rank Q1 (−0.08)."* An unexplainable alert is an alert you won't trust at the moment it counts.

**Signal thresholds:**
- `p > 0.65` → long
- `p < 0.35` → short
- otherwise flat (cash is a position)
- Cooldown: no new signal on the same symbol within 3 bars
- Hysteresis: exit only when `p` crosses back through 0.50, not the entry threshold — prevents thrash

**Prior updating:** the guide describes posterior-becomes-new-prior. Implement as an EWMA on the prior logit with a slow half-life (~60 bars). Be careful: this makes the model path-dependent and much harder to backtest correctly. Ship the static-prior version first, A/B it later.

---

## 7. Engine B — posterior predictive simulator

This is what produces the fan chart. **This is where you'll get the most value**, because it forces honest uncertainty into every alert.

### 7.1 Model

For each 30-min step, simulate:

```
r_t = μ(state) + σ(state) · ε_t,    ε_t ~ Student-t(ν=4)
```

- **μ(state):** conditional drift. Estimate by binning history on the same indicator states as Engine A and taking the mean forward 30-min return per bin. Shrink hard toward zero — intraday drift is tiny and easily overfit. `μ_shrunk = μ_bin × n/(n + 200)`.
- **σ(state):** conditional volatility. A GARCH(1,1) fit per symbol, or simpler: EWMA vol scaled by a time-of-day factor (the U-shaped intraday volatility curve is a real and exploitable regularity — open and close are far more volatile than midday).
- **Student-t innovations, not Gaussian.** Financial returns have fat tails. ν≈4 is a reasonable default; fit it if you have data.
- **Optional:** add a mild negative autocorrelation term for mean reversion at the 30-min scale.

### 7.2 Simulation

```python
def simulate_paths(S0, n_steps, mu, sigma, n_paths=500, nu=4, seed=None):
    rng = np.random.default_rng(seed)
    # mu, sigma are arrays of length n_steps (allows time-of-day vol)
    eps = rng.standard_t(nu, size=(n_paths, n_steps)) / np.sqrt(nu/(nu-2))
    log_r = mu[None, :] + sigma[None, :] * eps
    paths = S0 * np.exp(np.cumsum(log_r, axis=1))
    return np.column_stack([np.full(n_paths, S0), paths])
```

500 paths is plenty for level extraction and renders cleanly. Use 10,000 when you need stable tail quantiles and just don't draw them all.

### 7.3 Extracting the levels

This is the part the graphic does that a plain quantile band doesn't. Two candidate methods — implement both, compare:

**Method 1 — terminal quantiles (simple, robust):**
```python
q = np.quantile(paths[:, -1], [0.15, 0.35, 0.65, 0.85])
red_outer, red_inner, green_inner, green_outer = q
```

**Method 2 — path-density modes (closer to what the image shows):**
```python
from scipy.stats import gaussian_kde
from scipy.signal import find_peaks

def density_levels(paths, S0, min_move=0.0085):
    terminal = paths[:, -1]
    grid = np.linspace(terminal.min(), terminal.max(), 500)
    dens = gaussian_kde(terminal)(grid)
    peaks, _ = find_peaks(dens, prominence=dens.max()*0.05)
    lv = grid[peaks]
    # apply the 'profit level' filter — must be far enough to be tradeable
    return sorted(l for l in lv if abs(l/S0 - 1) >= min_move)
```

The `min_move` filter is the `Profit level 0.85%` box from the graphic. Levels closer than that to spot get discarded as untradeable.

**Sanity check against the image:** anchor 107.00, levels at 108.49 (+1.39%), 110.11 (+2.91%), 103.76 (−3.03%), 101.22 (−5.40%). The downside levels are further from spot than the upside ones — consistent with a fat left tail. If your simulator produces symmetric levels, your innovation distribution is wrong.

### 7.4 Rendering

```python
import matplotlib.pyplot as plt

def render_fan(paths, levels_up, levels_dn, S0, symbol):
    fig, ax = plt.subplots(figsize=(14, 9), facecolor='#0b0e14')
    ax.set_facecolor('#0b0e14')
    term = paths[:, -1]
    norm = (term - term.min()) / np.ptp(term)
    cmap = plt.get_cmap('Spectral')
    for i in range(len(paths)):
        ax.plot(paths[i], color=cmap(norm[i]), lw=0.4, alpha=0.5)
    for l in levels_up:
        ax.axhline(l, color='#22c55e', lw=1.5)
        ax.annotate(f'{l:.2f}', (len(paths[0]), l), color='#22c55e')
    for l in levels_dn:
        ax.axhline(l, color='#ef4444', lw=1.5)
        ax.annotate(f'{l:.2f}', (len(paths[0]), l), color='#ef4444')
    ax.axhline(S0, color='white', lw=1.2)
    return fig
```

Attach the PNG to every alert. A trader glancing at a fan chart absorbs the risk asymmetry in about a second; the same information as text takes a paragraph and gets skipped.

---

## 8. Vibration violation logic

The guide's most specific tradeable claim: when a pivot level is breached, expect a fast move in the breach direction.

```python
def check_violation(bar, pivot_price, side, atr):
    buf = 0.15 * atr                      # avoid wick noise
    if side == 'long' and bar.close < pivot_price - buf:
        return 'violated'
    if side == 'short' and bar.close > pivot_price + buf:
        return 'violated'
    return 'intact'
```

**Test the flip before you trust it.** The claim is that violations "more times than not lead to a quick and strong move." That is an empirically checkable statement, so check it:

> Over all confirmed pivots, conditional on a close-through violation, what is the mean and median forward return over 1/3/5 bars in the violation direction, versus the unconditional base rate? Is the difference significant after transaction costs?

If it holds, enable the auto-flip. If it doesn't, exit-only. My expectation is it's real but much weaker than described, and highly regime-dependent — likely present in trending regimes and absent in chop. Consider gating the flip on `vol_regime` and `trend_ctx`.

---

## 9. Portfolio & sizing

Straight from the guide's tenets:

```yaml
portfolio:
  total_capital: 100000
  tranches: 20
  tranche_pct: 0.05
  max_tranches_per_symbol: 3
  max_gross_exposure: 0.80        # keeps cash a real position
  max_correlated_cluster: 0.25    # see below
```

**Correlation clustering** matters more than the guide admits. GDX, GDXJ, GLD, and SLV are one bet, not four. Group the universe:

- Precious metals: GLD, SLV, GDX, GDXJ
- Equity beta: SPY, QQQ, IWM, XLF, SMH, XBI
- Energy: USO, UNG
- Duration: TLT
- Crypto beta: IBIT

Cap total exposure per cluster at 25%. Otherwise a 20-tranche portfolio that "feels" diversified can be three tranches of genuine risk.

**Stops:** initial stop at the pivot level ± 0.25 ATR. Trail at the inner Engine B level once it's touched. Take partial profit at the inner level, run the remainder to the outer.

---

## 10. Backtesting

### 10.1 The overlap problem

Your label spans 15 days. Adjacent bars share 14 days of outcome. A naive train/test split leaks the future into the past and will make a worthless model look excellent.

**Use purged walk-forward CV with an embargo:**

```python
def purged_walk_forward(dates, n_splits=8, embargo_days=20, horizon=15):
    folds = np.array_split(np.arange(len(dates)), n_splits)
    for i in range(1, n_splits):
        test = folds[i]
        train_end = test[0] - horizon - embargo_days
        if train_end <= 0:
            continue
        yield np.arange(0, train_end), test
```

Train only on data ending `horizon + embargo` before each test fold. The embargo covers serial correlation that outlives the label window.

### 10.2 Cost model

Do not skip this. Assume per round trip:
- Commission: $0.005/share or $1 minimum
- Spread: half the median quoted spread, or 2bp for liquid ETFs
- Slippage: 0.05 × ATR on market orders
- Borrow cost on shorts: 25–300bp annualized depending on symbol (GDXJ and UNG can be expensive)

A strategy that clears 55% accuracy on 5-15 day holds can easily be net-negative after costs. Cost modelling is what separates a backtest from a fantasy.

### 10.3 Reporting

Report per-symbol *and* aggregate: CAGR, max drawdown, Sharpe, Sortino, hit rate, average win/average loss, average holding period, turnover, and PnL attribution by feature bin. Add a **randomized-entry benchmark**: same number of trades, same holding periods, random directions. If your edge doesn't clear that by a wide margin, you have a sizing artifact, not a signal.

---

## 11. Calibration — the step everyone skips

Engine A outputs a probability. Naive Bayes probabilities are almost always **overconfident** because of the independence violation. If it says 0.80 and is right 62% of the time, your sizing and thresholds are built on a lie.

**Reliability diagram:** bin predictions into deciles, plot predicted vs realized frequency. You want the 45° line.

**Brier score:** `mean((p - y)²)`. Compare against always predicting the base rate. If you can't beat that, stop and rethink before building anything else.

**Fix:** fit isotonic regression or Platt scaling on out-of-fold predictions, apply to live output.

```python
from sklearn.isotonic import IsotonicRegression
calibrator = IsotonicRegression(out_of_bounds='clip').fit(oof_probs, oof_y)
p_calibrated = calibrator.predict([p_raw])[0]
```

**For Engine B, use PIT (probability integral transform) coverage:** over history, what fraction of realized prices fell below your simulated 15th percentile? It should be 15%. If it's 4%, your tails are too fat; if it's 30%, too thin. Check all four levels. This is the only honest way to know whether your fan chart means anything.

---

## 12. Alerting

**Payload:**
```json
{
  "symbol": "GDXJ",
  "timestamp": "2026-08-06T14:30:00-04:00",
  "action": "LONG",
  "p_up_calibrated": 0.71,
  "spot": 107.00,
  "targets":    {"inner": 108.49, "outer": 110.11},
  "stop":       103.76,
  "invalidation_pivot": 103.90,
  "tranches": 1,
  "expected_hold_bars": 12,
  "drivers": [
    {"feature": "pivot_state=confirmed_low", "llr": 0.34},
    {"feature": "rsi_divergence=bull",       "llr": 0.22},
    {"feature": "iv_rank=Q1",                "llr": -0.08}
  ],
  "fan_chart": "s3://.../gdxj_20260806_1430.png"
}
```

**Channels:** Discord/Telegram webhook for immediate, email for daily digest, SQLite/Postgres for the full log.

**Log every alert with its full feature vector at generation time.** In six months this log is the most valuable asset you have — it's the only way to diagnose *why* performance changed. Also log alerts that were suppressed by cooldown or threshold; near-misses are informative.

---

## 13. Build phases

| Phase | Duration | Deliverable | Gate to proceed |
|---|---|---|---|
| **1. Data** | 1 wk | Clean OHLCV + options, point-in-time correct | Spot-check 20 random dates against an independent source |
| **2. Vibrations** | 1 wk | Pivot detector, both formed & confirmed variants | Visual overlay on 10 charts looks sane; zero look-ahead |
| **3. Features** | 1 wk | Full binned feature matrix | No feature pair with MI > 0.3 survives |
| **4. Engine A** | 2 wks | Fitted posterior + purged CV results | Brier beats base rate; reliability diagram roughly diagonal |
| **5. Backtest** | 2 wks | Full cost-aware backtest | Beats randomized-entry benchmark net of costs |
| **6. Engine B** | 2 wks | Simulator + level extraction + renderer | PIT coverage within ±5pp at all four levels |
| **7. Paper trade** | 8 wks | Live alerts, no capital | Live hit rate within 1 SE of backtest |
| **8. Live small** | ongoing | 25% of intended size | Monthly recalibration review |

**Do not skip phase 7.** The gap between backtest and paper is where nearly every subtle look-ahead bug reveals itself, and it costs nothing but patience.

---

## 14. Failure modes to watch

1. **Look-ahead via centered windows** — the pivot detector is the prime suspect. Audit it twice.
2. **Adjusted-price leakage** — using today's split-adjusted series for a 2019 backtest.
3. **Survivorship** — if you ever extend beyond ETFs, delisted names must be in the sample.
4. **Overfitting the parameter sweep** — you have `n × k × atr_mult × thresholds × bins`. That's thousands of combinations. Testing all of them against one holdout guarantees a false positive. Use nested CV, or fix parameters on symbols A–F and validate on G–L.
5. **Regime dependence** — a model fit on 2010–2021 saw one rate regime. Report performance broken out by volatility regime and by year.
6. **Correlation blindness** — described in §9. The single most likely way this blows up.
7. **Overconfident posteriors** — described in §11.
8. **Silent data outages** — a stale feed produces a flat price series, which produces confident nonsense. Add a staleness check that halts alerting.

---

## 15. Where to start tomorrow

1. Pull 10 years of daily bars for the 11-symbol universe.
2. Write `vibration.py` and overlay detected pivots on a chart. Eyeball whether they match what the guide's Figure 2 describes.
3. Label with triple-barrier, fit the naive Bayes, and plot the reliability diagram.

If step 3's reliability diagram is flat — predictions clustered near 0.5, or badly miscalibrated — you've learned something important for two weeks of work rather than six months. That's the cheapest possible test of whether this whole approach has an edge on your data.

---

*This document describes a technical implementation. Nothing in it is investment advice, and no backtest result implies future performance. Validate independently before committing capital.*
