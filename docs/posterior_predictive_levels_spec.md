# Spec — Posterior Predictive Levels (PPL)

Status: **DRAFT — pending approval** (2026-08-06)
Branch: `feat/bayesian-alert-system`
Source material: `bayesian-alert-system-plan.md` (Engine B), vendor reference
charts (BAYESAIN, SMH 30m + 2h, 2026-08-06)

---

## 1. Product

A page that renders, for any symbol and interval, the **posterior predictive
distribution of forward price paths** as a fan chart — plus four extracted
price levels (two above spot, two below) and the calibration evidence that the
distribution means anything.

This is a **measurement instrument, not a signal.** It answers *"where is price
likely to trade, and how asymmetric is the risk?"* — never *"should I be
long?"*. There is no directional claim in v1, therefore no MCPT gate and no
promotion path. The gate here is **distributional calibration** (PIT coverage),
which is the honest analogue for an instrument rather than a strategy.

This mirrors the platform's own precedent: the surviving result of the entire
GEX chapter (EXP-55/56) was a *measurement* — GEX forecasts next-day volatility
— after every directional rendering died. Instruments have survived here;
directional stories have not.

**Out of scope for v1** (the user's stated later phases, listed so scope creep
is visible):

- Engine A — the Bayesian *directional* posterior
- PPL-derived alerts / signals
- Backtesting PPL-derived trades

---

## 2. Decisions (2026-08-06)

| # | Decision | Rationale |
|---|---|---|
| D1 | Intervals **30m / 1h / 4h / 1d**. No 2h. | 2h does not tile a 6.5h session and would touch the shared `_INTERVAL_SECONDS` map and every consumer. 1h and 4h bracket the reference's 2h. |
| D2 | **PIT harness + `ohlcv_30m` land before the public route.** Admin-only preview route unblocks visual iteration. | An uncalibrated fan chart is exactly the artifact that looks authoritative and isn't. |
| D3 | **Any symbol** with sufficient bar depth; typed error below the floor. | Compute per fan is milliseconds; restricting the universe only makes the page less useful. |
| D4 | Levels are **explicit quantiles**, labeled as such in payload and on hover. | "85th percentile of simulated terminal price" is what the number *is*. "Target" is a claim we haven't earned. |
| D5 | Indicator params **fixed per interval**, fit once on training data. **No per-symbol optimization.** | See §3.1. The reference tunes per symbol × per timeframe; that is a fitting-noise generator and the trap named in the plan's §14.4. |
| D6 | `min_move` ("profit level") **derived from simulated horizon sigma**, not configured per symbol. | See §4.2. Reproduces the reference's observed timeframe scaling for free and removes a knob. |
| D7 | Default level method = **terminal quantiles**. KDE modes available behind a param, not default. | Quantiles are the only version PIT can validate. |
| D8 | ~~**No new heavy dependencies.** σ via EWMA × time-of-day, **not** GARCH.~~ **REVERSED 2026-08-08 — see D11.** KDE and peak-finding remain in-repo (pure numpy). | Original reasoning: the pieces are ~25 lines each and arch is not a dependency. That held until the model was measured. |
| D11 | **σ via GJR-GARCH(1,1) + filtered historical simulation**, fit with `arch`. Reverses D8's volatility half. | D8 was a cost decision made before evidence. The evidence arrived: EWMA+iid-t **failed** the D2 gate, and the failure is structural, not parametric — EWMA is IGARCH (persistence ≡ 1, so variance never mean-reverts and the fan runs ~18% wide at H=40) and symmetric innovations have no channel for the leverage effect (measured 1.28× RMS after down days). No value of λ or ν fixes either. Re-running the same harness with only `--vol-model` changed moves pooled KS from 0.082 (p=0.000, FAIL) to 0.048 (p=0.119, PASS). `arch` is Sheppard's reference implementation and is used for the **fit only**; simulation stays in-repo and seeded. Full argument: [`docs/ppl_mathematics.md`](ppl_mathematics.md) §8. |
| D12 | **The fit window (`fit_lookback_bars`) is applied in `build_from_frame`**, not in the bar loader. | Found while calibrating D11: the PIT harness hands in an *expanding* prefix, so it was calibrating against up to ~35,000 bars at 1h while production always fetched 4,000. The gate was measuring a model that never ships. It is also not merely a cost question — a GARCH fit spanning 20 years of volatility regimes is pushed toward spurious IGARCH (Lamoureux & Lastrapes 1990), which is precisely how the bug surfaced (TLT: 271 non-stationary fits). Truncation is part of the model, so it lives on the shared entry point. |
| D9 | **Drift is three separable modes** — `zero`, `unconditional`, `state`. | Added during P4, amended by P3's measurement — see §4.1 and §6.5. `state` (a timing claim) stays off; `unconditional` (the asset's baseline drift) is NOT a timing claim and PIT rejects setting it to zero. |
| D10 | Live intraday bars are **restricted to the ET regular session** (`rth_only`). | The research tables the PIT harness calibrates against are RTH; the live tier resamples `ohlcv_1m`, which carries the full ET 04:00-20:00 day (32 bars/session at 30m vs 13). Two session geometries mean two time-of-day profiles and two volatilities — coverage measured on one would say nothing about the other. |

---

## 3. Reading the reference charts

Both images are SMH on 2026-08-06 at the same anchor, 569.46:

| | 30-minute | 2-hour |
|---|---|---|
| Green outer / inner | 600.34 (+5.42%) / 585.09 (+2.74%) | 645.37 (+13.33%) / 608.89 (+6.92%) |
| Red inner / outer | 558.85 (−1.86%) / 543.55 (−4.55%) | 502.06 (−11.84%) / 468.29 (−17.77%) |
| Profit level | 1.57% | 3.71% |
| Conditioning | P-Sar[0.028, 0.214] · TSI[8,8,6] · MACD[10,14,8] | P-Sar[0.03, 0.187] · TSI[13,8,12] · MACD[8,22,11] |

Three inferences drive the design:

**(a) The indicator parameters are tuned per symbol × per timeframe.** Every
chart carries a different non-round parameter set (the plan doc's GDXJ chart is
a third set again). We fix one set per interval instead — D5.

**(b) "Profit level" scales with horizon.** 1.57% → 3.71% is 2.36×, close to
the √4 = 2× expected from a 4× longer bar at a fixed bar count. It is a
volatility-derived quantity, not a per-symbol constant — D6.

**(c) The two timeframes disagree about direction.** At one instant the 30m fan
leans up (+2.74 / −1.86 inner) while the 2h fan leans hard down (+6.92 /
−11.84). This can be defensible — a short-horizon up-drift inside a
longer-horizon down-drift — but it is precisely the incoherence calibration
exposes. §5.3 makes it a reported check.

---

## 4. The model

Per `(symbol, interval)`, anchored on the **last completed bar** (never the
forming bar):

```
log r_t = μ(s) + σ_t · ε_t          ε_t ~ standardized Student-t(ν),  t = 1..H
P_t     = S0 · exp(cumsum(log r))
```

### 4.1 Conditioning state `s`

The reference's three indicators, discretized:

| Component | Definition | Bins |
|---|---|---|
| `psar_side` | close vs PSAR(af_start, af_max) | above / below (2) |
| `tsi_state` | TSI vs its signal × sign of its slope | above/below × rising/falling (4) |
| `macd_state` | sign(MACD histogram) × sign(Δ histogram) | 4 |

Cartesian product = **32 states**. `app/indicators/` already has TSI and MACD;
**PSAR is new** (§7, P0).

Conditional drift, shrunk hard toward zero:

```
μ(s) = mean_forward_log_return(s) × n_s / (n_s + κ)      κ = 200
```

States with `n_s < n_min` (default 50) pool up to the unconditional mean rather
than being silently dropped.

> **Expected consequence, recorded before building.** With 2 years of 30m bars
> (≈6,800) spread over 32 states, mean n_s ≈ 210. After κ=200 shrinkage, μ is
> *near zero nearly everywhere*. Our fans will therefore be markedly **more
> symmetric than the reference's**. Asymmetry should come from fat-tailed
> innovations and log-space compounding, not from a drift claim. If the built
> fan is visually boring, that is the honest answer and not a bug.

> **↑ That expectation was WRONG, and measuring it is why D9 exists.**
> Measured on live SPY during P4: the drift-to-noise ratio (μ·H against σ·√H)
> came in at **0.08 on daily but 0.68 at 30m** — at 30m three of four quantiles
> sat above spot. Two reasons the per-bar shrinkage failed to control it:
>
> 1. Shrinkage acts on the *per-bar* mean, but the horizon-level ratio scales as
>    **√H**. Controlling μ per bar does not control the claim the fan makes.
> 2. The tilt is not the sample period — the unconditional drift is tiny
>    (0.000026/bar at 30m). It is the *state*: the anchor's state measured ~5×
>    the unconditional, sitting at the top of the 32-state range.
>
> And that is the problem. A raw per-state mean at n≈250 with per-bar σ≈0.0012
> gives **t ≈ 3.1** — which is about what the *maximum of 32 noise draws*
> produces. This is the multiple-comparisons failure documented across DT-0..3,
> EXP-37/39/45. Hence D9: **`drift_mode="zero"` is the default**, the fan makes
> a volatility claim only, and the state estimate is still reported as
> `state_drift_per_bar` so switching modes is observable. P3's PIT run
> adjudicates whether the drift earns its place — if coverage improves with it
> on, it goes back on.
>
> Verified after the change: zero-drift puts the median at **+0.003%** of spot,
> with residual level asymmetry of −5.80% / +6.21% coming purely from log
> compounding and fat tails, exactly as the original expectation described.

### 4.2 Volatility — GJR-GARCH(1,1)

**Amended 2026-08-08 (D11).** The original spec called for
`σ_t = σ_ewma × tod(t)` with λ = 0.94. That model failed the §6 gate; §6.5
records the measurement and D11 the reversal.

```
tilde_r_t     = r_t / tod(slot(t))                    # deseasonalize, then fit
sigma²_{t+1}  = ω + (α + γ·1[ε_t < 0])·ε²_t + β·sigma²_t
r_h           = mu + tod(slot(h)) · sigma_h · z_h     # re-seasonalize
```

- **`ω, α, γ, β`** — fit by Gaussian quasi-MLE (`arch`). γ is the leverage
  term; persistence `p = α + γ/2 + β` must be `< 1` or the fit is rejected.
- **`tod(t)`** — time-of-day factor, the per-session-slot standard deviation
  normalized to mean 1, identically 1 for `1d`. Divided out **before** the
  GARCH fit and re-applied per simulated step (Andersen & Bollerslev 1997):
  fed the raw intraday series, the optimizer reads the deterministic U-shape as
  volatility clustering and inflates β.
- The variance recursion is driven by `ε` — the deseasonalized, drift-free
  innovation — never by the realized return. Otherwise the fitted persistence
  would depend on the drift setting.

The slot factor absorbs the overnight gap without a separate term: slot 0's
return spans the gap, so its factor comes out large on its own. This replaces
the original spec's requirement for an explicit overnight-gap variance term.

State-conditional σ is still deliberately **not** in v1: the vol-clustering
term dominates, and the strongest σ conditioner we own is GEX (§10).

### 4.3 Innovations — filtered historical simulation

**Amended 2026-08-08 (D11).** Originally a standardized Student-t,
`ε = T(ν)/sqrt(ν/(ν−2))`, with ν moment-matched from residual kurtosis.

Now the standardized GARCH residuals `z_t = ε_t / σ_t` are resampled directly
(Barone-Adesi, Bourgoin & Giannopoulos 1998). No parametric family is imposed;
the pool is demeaned and rescaled to exactly mean 0 / variance 1 so that drift
lives in exactly one place and `σ_t` is the true per-step standard deviation.

ν is still reported but is now **descriptive only** — a moment-matched summary
of the pool's tails. Nothing samples from it.

> **A rejected fix, recorded because it was wrong for an instructive reason.**
> §6.5's first diagnosis was symmetric innovations and the proposed fix was a
> skew-t. It would not have worked: skew of a sum of iid draws decays as
> `γ₁/√H`, so at SPY's one-bar skew of −0.44 the H=40 prediction is −0.069
> against an **observed −1.68**. Observed skew *grows* with horizon where iid
> says it must shrink. Horizon skew is a property of path dynamics, not of the
> marginal distribution, and only a leverage term produces it.

### 4.4 Horizon

`H` default **40 bars**, configurable per interval. The payload reports it as
both a bar count and a calendar span, because "40 bars" means four different
things across our four intervals.

### 4.5 Determinism

Seed derived from `(symbol, interval, anchor_bar_ts, model_version)`, with an
explicit `seed` param to override. The same chart is byte-identical on reload,
matching the reproducibility norm the backtester already holds.

---

## 5. Levels

### 5.1 Extraction

`method="quantile"` (default): quantiles of terminal price at
`{0.15, 0.35, 0.65, 0.85}` →

```
red_outer = q15   red_inner = q35   green_inner = q65   green_outer = q85
```

`method="density"`: Gaussian KDE over a grid of terminal prices (Silverman
bandwidth), local maxima with prominence ≥ 5% of peak density. Implemented in
numpy per D8. **Not PIT-validatable, therefore not the default** — offered for
comparison against the reference's apparent method.

### 5.2 The `min_move` filter

```
min_move = c × σ_H          σ_H = std of terminal log return across paths
```

Default `c = 0.25`, back-solved from the reference (30m: σ_H ≈ 6%, profit level
1.57% → c ≈ 0.26). **This is a starting hypothesis to review against real fans,
not a fitted constant.**

Levels closer to spot than `min_move` are **not silently dropped**. They are
returned with `suppressed: true` and a reason, and rendered dimmed — per
`docs/standards/coding.md`, a filtered-out result is still a reported outcome.

---

## 6. Calibration — the gate

### 6.1 PIT harness

Historical replay over `ohlcv_30m` / `ohlcv_hourly` / `ohlcv_daily`. For each
anchor bar in the eval window:

1. Fit parameters using **only data ≤ anchor**.
2. Simulate H bars forward.
3. Record `u = fraction of simulated terminal prices below the realized price
   at anchor + H`.

Over many anchors, `u` must be ~Uniform(0, 1).

**Overlap:** consecutive anchors share H−1 bars of outcome. Stride anchors by
≥ H for near-independent observations and report **effective n**, not raw n.
This is the same trap the plan's §10.1 names for Engine A's labels.

### 6.2 Pass criteria — all must hold to unblock the public route

- **Coverage within ±5pp at each of the four quantiles.** This is the primary,
  binding gate (plan §11).
- KS statistic + p vs Uniform, **reported not binding** — at large n, KS
  rejects on trivially small deviations.
- Run **per interval** and **split by volatility regime**, per this repo's
  standing habit of regime-split reporting.

### 6.3 Cross-timeframe coherence — reported, not binding

A 30m fan run to horizon H and a 4h fan run to the *same calendar horizon*
should agree on terminal quantiles within a stated tolerance. This is the check
the reference images visibly fail (§3c). Report it; a failure is a design
conversation, not an auto-block.

### 6.5 First results — the model FAILS its own gate

SPY daily, H=40, 2,000 paths, stride 40, `ohlcv_daily` 2006-2026, n_eff = 108:

| Drift mode | mean PIT | q0.15 | q0.35 | q0.65 | q0.85 | Verdict |
|---|---|---|---|---|---|---|
| `zero` | 0.619 | −3.9pp | **−19.3pp** | **−21.5pp** | −5.4pp | FAIL |
| `unconditional` | 0.580 | −3.0pp | **−16.5pp** | **−10.4pp** | −0.7pp | FAIL |
| `state` | 0.594 | −3.0pp | **−14.6pp** | **−17.8pp** | −5.4pp | FAIL |

Mean PIT well above 0.5 in every mode = the realized price lands above the
fan's centre far too often. A **location bias**, not a width problem.

**This partially reverses D9 as originally written.** Zero drift is itself a
claim, and PIT rejects it: for an equity index the baseline drift is the equity
risk premium, not a timing edge. The P4 argument against *state* drift stands
untouched — what it got wrong was collapsing "no timing claim" into "no drift
at all". The code now separates them; `unconditional` is best of the three.

Something beyond drift is still wrong (mean PIT 0.58, q0.35 light by 16.5pp,
z = −3.6). Untested candidates: λ=0.94 mis-set for daily, volatility
mean-reversion over a 40-bar horizon, or missing parameter uncertainty.

**The gate is also underpowered at this n.** At n=108, SE at q=0.35 is 4.59pp,
so ±5pp is 1.09 SE — a perfectly calibrated model would miss it often by
chance. The observed misses survive (z = −3.59, −2.26), so the FAIL is real,
but discriminating power needs n ≳ 1,000, i.e. **pooling across symbols**.
Amendment: judge a miss by both |error| > 5pp (relevance) AND |z| > 2
(significance). `CoverageBand` computes both.

Full derivations and the audit of which parts are actually Bayesian:
[`ppl_mathematics.md`](ppl_mathematics.md).

### 6.4 Shown on the page

```
PIT coverage (n_eff = 412, 2019-01 → 2026-06)
15th → 14.2%   35th → 33.8%   65th → 66.1%   85th → 85.9%
```

This strip is the product differentiator. The vendor shows a fan; we show a fan
plus the evidence it is honest.

---

## 7. Architecture

```
bars_gateway  (ClickHouse | lake, resampled)
      │
      ▼
app/services/ppl/
  conditioning.py  psar/tsi/macd → state           (needs app/indicators/psar.py)
  estimate.py      μ(s), σ_ewma, tod factor, ν
  simulate.py      seeded numpy path sampler
  levels.py        quantiles | KDE modes, min_move
  calibrate.py     PIT replay harness
  service.py       from_settings() → PplSnapshot
  schemas.py  contract.py  README.md  tests/
      │
      ├── app/api/routes_ppl.py       GET /api/v1/ppl/{symbol}
      ├── frontend/src/routes/ppl.tsx + canvas fan renderer
      └── app/mcp/tools/ppl.py        (follow-up, not v1)
```

Module layout follows `docs/standards/service_modules.md`. Cross-service imports
come from `schemas.py` / `contract.py` only.

### 7.1 Contracts

```
PplParams        interval, horizon_bars, n_paths, n_render_paths, method, seed,
                 psar_af_start, psar_af_max, tsi_long, tsi_short, tsi_signal,
                 macd_fast, macd_slow, macd_signal, ewma_lambda, shrink_kappa,
                 nu, min_move_sigma_mult

PplLevel         price, quantile, side ("up"|"down"), rank ("inner"|"outer"),
                 pct_from_spot, suppressed, suppressed_reason

PplSnapshot      symbol, interval, as_of, spot, horizon_bars, horizon_end,
                 levels[4], paths, terminal_quantiles, sigma_horizon, min_move,
                 state, n_fit_bars, stale, stale_age_bars,
                 calibration, model_version, seed

PplCalibration   window, n_effective, coverage{quantile: realized},
                 ks_stat, ks_p, computed_at
```

### 7.2 Payload size

300 render paths × 41 points ≈ 12,300 floats. Rounded to 2dp server-side ≈
90 KB JSON. `n_render_paths` capped at 500; `n_paths` (simulated) defaults
higher than rendered so tail quantiles are stable without drawing them all.

### 7.3 Failure modes — no silent failures

| Condition | Behaviour |
|---|---|
| Insufficient bars to fit | Typed 422 stating required vs available |
| Last bar older than N intervals | `stale: true` + age in payload; page shows a banner (plan §14.8 — a frozen feed produces confident nonsense) |
| Unknown symbol / no data | 404 |
| Every request | Logged with symbol, interval, n_fit_bars, elapsed |

### 7.4 Caching

Snapshot cached on `(symbol, interval, anchor_bar_ts, model_version)`. Because
the fan is deterministic given that key, the whole snapshot caches — not just
the fit. TTL bounded by the interval duration.

---

## 8. The page

Routes `/ppl` and `/ppl/:ticker`, registered in `frontend/src/routes/router.tsx`
plus nav. Customer-visible once D2's gate passes; the preview route is
`adminOnly`.

`lightweight-charts` v5 is the wrong tool for 300 overlapping line series — the
fan is a custom `<canvas>`, which also buys the reference aesthetic cheaply.

Layout, following the reference:

- Symbol + "as of" pill, interval selector (30m / 1h / 4h / 1d), ticker box
- Canvas fan — paths colored by terminal value on a spectral ramp, white anchor
  line, level lines spanning the plot with right-edge colored labels
- Parameter box, bottom-left: conditioning params + `min_move`
- **Calibration strip** below the chart (§6.4)
- Footer: horizon label — "40 bars · 30-minute · through Aug 8 14:00 ET"

Hover on a level: *"65th percentile of simulated terminal price · +2.74% ·
min tradeable move 1.57%"* — D4.

Follows the app's existing theme tokens.

---

## 9. Build phases

| Phase | Deliverable | Gate to proceed |
|---|---|---|
| **P0** | `app/indicators/psar.py` + registry entry + tests | Matches a reference PSAR on a fixture series |
| **P1** | `app/services/ppl/` core — conditioning, estimate, simulate, levels + unit tests | Deterministic (same seed → identical paths); σ recovers a known value on synthetic GBM; ν recovers on synthetic Student-t |
| **P2** | `scripts/build_ohlcv_30m.py`, sibling of `build_ohlcv_hourly.py` | 30m bars roll up **exactly** to `ohlcv_hourly` and `ohlcv_daily` |
| **P3** | `calibrate.py` + PIT run across all four intervals | **Coverage within ±5pp at all four quantiles.** Blocks P5. |
| **P4** | `routes_ppl.py` + admin-only preview page | Fan renders; stale and insufficient-data paths surface correctly |
| **P5** | Public `/ppl` route + calibration strip + nav | P3 passed; verified in the browser |

**P3 is the real gate.** If PIT fails badly, we have learned something cheap
and the page does not ship in that form.

---

## 10. Future hooks (explicitly not v1)

- **σ conditioned on GEX.** EXP-55's surviving finding — GEX forecasts next-day
  volatility, 2.1× monotone, incremental to vol clustering — is a direct σ
  input for the daily fan, on a 295M-row options lake we already own. The
  strongest v2 candidate, and cleanly motivated by our own validated research
  rather than by the vendor.
- **Engine A**, the directional posterior — would require pre-registration in
  `docs/research_hypotheses.md` and the full MCPT gauntlet, against a strong
  prior set by DT-0..3, EXP-36/37/39/42/45.
- **PPL-derived alerts and backtesting** — the user's stated phase 3.

---

*Nothing in this document is investment advice. The levels described are
quantiles of a simulated distribution, not price predictions.*
