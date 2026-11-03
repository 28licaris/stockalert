# ppl/ — Posterior Predictive Levels

Simulated forward-price distributions: a fan of Monte Carlo paths plus the
price levels extracted from where they land.

**This is a measurement instrument, not a signal.** It answers *"where is price
likely to trade, and how asymmetric is the risk?"* — never *"should I be
long?"*. There is no directional claim here, so there is no MCPT gate; the gate
is **distributional calibration** (PIT coverage), landing in P3.

Spec: [`docs/posterior_predictive_levels_spec.md`](../../../docs/posterior_predictive_levels_spec.md).

## Layout

| File | Owns |
|---|---|
| [`schemas.py`](schemas.py) | `PplParams`, `PplLevel`, `PplState`, `PplSnapshot`, `PplResult`, `MODEL_VERSION` |
| [`contract.py`](contract.py) | `PplBuilder` Protocol — the public interface |
| [`conditioning.py`](conditioning.py) | PSAR × TSI × MACD → one of 32 state codes |
| [`estimate.py`](estimate.py) | Shrunk conditional drift, time-of-day factors, EWMA vol + tail index (the `ewma_t` null) |
| [`garch.py`](garch.py) | **GJR-GARCH(1,1) fit + FHS residual pool** — the volatility model |
| [`simulate.py`](simulate.py) | Seeded path samplers (FHS + Student-t), deterministic |
| [`levels.py`](levels.py) | Terminal quantiles / KDE modes, `min_move` filter, in-repo KDE + peak-finding |
| [`calibrate.py`](calibrate.py) | PIT harness — the gate that opens the public route |
| [`service.py`](service.py) | `PplService` — the orchestrator, the only file that does IO |

## Calibration

```bash
poetry run python scripts/ppl_calibrate.py --symbols SPY,QQQ,IWM,DIA,GLD,TLT \
    --interval 1d --drift-mode unconditional --pooled
```

Add `--vol-model ewma_t` to re-run the previous model as the null.

**Status: the model passes.** Changing nothing but `--vol-model`:

| | `ewma_t` (old) | `gjr_fhs` (new) |
|---|---|---|
| **1h**, n≈4,950 pooled | KS 0.046, rejected for **5/6** symbols | KS **0.028**, rejected for **1/6** |
| **1d**, n=615 pooled | KS 0.082 (p=0.000), q0.35 miss z=−3.26 → **FAIL** | KS **0.048 (p=0.119)**, no miss → **PASS** |

1h is the run that decides it: $n\approx870$ per symbol against daily's 108, so
the test actually has power. The old model is rejected at p<0.05 for five of six
symbols there; the new one is not rejected for five of six.

Still open at 1d per symbol: SPY (KS p=0.041) and DIA (p=0.077) retain a small
**location** bias — mean PIT 0.546 / 0.529 against GLD 0.486, TLT 0.484. That is
**drift**, not volatility, and it works out to roughly one standard error of the
drift estimator itself (16%/√16yr ≈ 4%/yr). Tuning drift until PIT passes would
be fitting the evaluation window; see
[`docs/ppl_mathematics.md`](../../../docs/ppl_mathematics.md) §8.5.

`/ppl` stays operator-only pending a product call on opening it.

## The model

```
log r_h       = mu(state) + tod[slot_h] * eps_h
eps_h         = sigma_h * z_h                    z_h ~ empirical residual pool
sigma^2_{h+1} = omega + (alpha + gamma*1[eps_h<0]) * eps^2_h + beta * sigma^2_h
P_h           = spot * exp(cumsum(log r))
```

- **`mu(state)`** — mean forward log return per conditioning state, shrunk by
  `n/(n+kappa)` with `kappa=200`. States thinner than `min_state_obs` pool up
  to the unconditional mean and report `pooled=True`.
- **`sigma_h`** — **GJR-GARCH(1,1)**, fit by Gaussian QMLE on the deseasonalized
  series. Volatility clusters (`beta`), mean-reverts (`alpha + gamma/2 + beta <
  1`), and reacts asymmetrically to down moves (`gamma > 0`). It evolves *along
  each path*, so a path that draws a run of losses carries elevated volatility
  forward — that feedback is the only thing that produces the horizon skew the
  data shows.
- **`z_h`** — bootstrapped from the fitted standardized residuals (Filtered
  Historical Simulation). No parametric family is imposed; whatever skew and
  tail shape the asset has, the simulation inherits.
- **`tod[slot]`** — per-session-slot volatility factor, divided out before the
  fit and re-applied per step. Absorbs both the intraday U-shape *and* the
  overnight gap in one mechanism: slot 0's return spans the gap, so its factor
  comes out large on its own. No separate overnight term. At 1d it is `[1.0]`.

`mu` is a mean **log** return applied in log space, so there is no Itô
correction to make.

The variance recursion is driven by `eps_h` — the *deseasonalized, drift-free*
innovation — never by the realized log return. Feeding `mu` or the time-of-day
factor back in would make the model's persistence depend on its drift setting
and on what time of day the anchor happened to fall.

`nu` is still reported but no longer generates anything: it is a moment-matched
*description* of the bootstrap pool's tails, kept so the tail stays comparable
across the two models.

### Why not EWMA + skewed innovations

That was the first hypothesis and measurement killed it. Skew of a sum of iid
draws decays as `g/sqrt(H)`; SPY's realized skew *grows* from −0.44 at H=1 to
−1.68 at H=40, 24× what iid could produce. Horizon skew comes from path
dynamics, not from the marginal distribution. Full argument and numbers in
[`garch.py`](garch.py).

### `vol_model` — two models, one of them the null

`gjr_fhs` is the product. `ewma_t` is the previous constant-volatility
Student-t model, kept runnable so the PIT harness can **measure** what changed
instead of asserting it. Its persistence is structurally 1 (EWMA is IGARCH), so
its fan grows as `sqrt(H)` without bound and is symmetric by construction.

### Drift mode — why zero is the default

`drift_mode="zero"` (default) makes the fan a **pure volatility forecast**. Any
asymmetry then comes from fat tails and log compounding, not a directional claim.

The original plan applied the state-conditional drift, and measurement during P4
killed that. On live SPY the drift-to-noise ratio (μ·H against σ·√H) was 0.08 on
daily but **0.68 at 30m** — three of four quantiles above spot. The per-bar
shrinkage cannot control this, because the horizon-level ratio scales as **√H**.
And the tilt is not the sample period: the unconditional drift is tiny
(0.000026/bar at 30m), while the anchor's *state* measured ~5× that. A raw
per-state mean at n≈250 gives t ≈ 3.1 — about what the maximum of 32 noise draws
produces. That is the multiple-comparisons failure documented in DT-0..3 and
EXP-37/39/45.

So: no directional claim by default. `drift_mode="state"` still applies it, and
`state_drift_per_bar` is reported in **both** modes so the switch is observable
and the conditioning's claim stays inspectable. P3's PIT run decides whether the
drift earns its place — if coverage improves with it on, it goes back on.

## Levels

Four levels at fixed quantiles (0.15 / 0.35 / 0.65 / 0.85). `side` and `rank`
describe position **in the forecast distribution** — the two lowest quantiles
are `down`, the two straddling the median are `inner`. Under a strong drift a
`down` level can print above spot; `pct_from_spot` carries the truth.

`min_move` (the reference charts' "Profit level") is derived as
`sigma_horizon × min_move_sigma_mult`, not configured per symbol. That is what
reproduces the reference's scaling across timeframes. Levels inside it are
returned with `suppressed=True` and a reason — **never dropped**.

`method="density"` swaps quantiles for KDE modes. Not the default, and not
PIT-validatable: "the realized price falls below the 15th percentile 15% of the
time" is a statement about quantiles specifically.

## Determinism

The seed derives from `(symbol, interval, anchor_bar_ts, MODEL_VERSION)`, so a
chart is byte-identical across reloads and refreshes when a new bar closes.
Bump `MODEL_VERSION` whenever a change should invalidate cached snapshots or a
calibration run.

## Failure handling

`build()` returns a `PplResult`; predictable failures come back rather than
raising (`docs/standards/service_modules.md` rule 4):

| Status | When |
|---|---|
| `ok` | snapshot attached |
| `no_data` | the bar source returned nothing |
| `insufficient_data` | fewer than 600 bars; `metadata` carries available vs required |
| `error` | unsupported interval (PPL serves 30m / 1h / 4h / 1d only), **or the GJR-GARCH fit failed** |

A failed volatility fit is an `error`, not a fallback. Silently dropping to a
weaker model would put an uncalibrated fan on screen wearing the calibrated
model's name. It is rare — one anchor in 108 on TLT hit the non-stationarity
bound across the whole calibration run — and the harness reports skipped anchors
rather than substituting them.

**Staleness is tri-state.** `staleness_checked=False` means the check did not
run — the market is closed, or the calendar backend was unavailable — and must
not be read as "the feed is fine". A frozen feed *during* a session is the
failure this catches.

## Testing

```bash
poetry run pytest app/services/ppl/tests
```

Every layer is pure except `service.py`, and `PplService` takes `bars_fn` by
injection — the whole pipeline runs on synthetic bars with no ClickHouse, no S3,
and no network. See [`tests/conftest.py`](tests/conftest.py).
