# Posterior Predictive Levels — the mathematics

Companion to [`ppl.md`](ppl.md)
(what we're building and why) and [`app/services/ppl/`](../../app/services/ppl)
(the implementation). This document states the model precisely, derives the
Bayesian parts, and — as directly as possible — **audits which parts are
actually Bayesian and which are not**.

Short version of that audit, up front, because it is the honest headline:

| Component | Status |
|---|---|
| Conditional drift shrinkage | **Genuinely Bayesian** — exactly a conjugate Normal–Normal posterior mean (§3) |
| The path sampler | **Plug-in predictive, not a posterior predictive** (§4.1). The name describes intent, and §4.1 bounds the error |
| GJR-GARCH volatility | Not Bayesian — Gaussian quasi-MLE, a point estimate with no posterior (§4.3) |
| FHS innovations | Nonparametric bootstrap — frequentist, and deliberately assumption-free (§4.2) |
| Tail index ν | Frequentist moment matching, and now **purely descriptive** — nothing samples from it (§4.4) |
| PIT validation | Frequentist check of a Bayesian object — which is correct practice (§7) |

The model is therefore **Bayesian in its drift and frequentist in its
volatility**. That is stated plainly rather than papered over: the drift is the
part where a prior does real work (a per-state mean at $n\approx 250$ is mostly
noise and must be shrunk), and the volatility is the part where 4,000
observations make a prior almost irrelevant. §10 gives the concrete route to
closing the gap and §4.1 shows the gap is currently small.

> **Model history.** Volatility was EWMA with iid Student-t innovations through
> `ppl-0.1.0`. That model **failed** this document's own PIT gate, and §8
> records what it failed on and why the replacement was the specific one it was.

---

## 1. Notation

| Symbol | Meaning |
|---|---|
| $P_t$ | close of bar $t$; $P_0$ = anchor ("spot") |
| $r_t = \log(P_t / P_{t-1})$ | log return of bar $t$ |
| $H$ | horizon, in bars |
| $s_t \in \{0,\dots,31\}$ | conditioning state at bar $t$ (§2.1) |
| $\mu(s)$ | drift, in log space, per bar |
| $\sigma_t$ | conditional volatility of bar $t$ (deseasonalized) |
| $\varepsilon_t$ | innovation, $=\sigma_t z_t$ — mean zero, drift-free |
| $z_t$ | standardized innovation, mean 0 variance 1 |
| $f(\text{slot})$ | time-of-day volatility factor, mean 1 |
| $\omega,\alpha,\gamma,\beta$ | GJR-GARCH(1,1) parameters |
| $p = \alpha + \gamma/2 + \beta$ | variance persistence |
| $n_s$ | bars observed in state $s$ |
| $\kappa$ | shrinkage constant (`shrink_kappa`, default 200) |

Everything is in **log space**. This matters in §5.2.

---

## 2. The generative model

For each forward step $t = 1 \dots H$:

$$\boxed{\;
\begin{aligned}
r_t &= \mu(s_0) + f(\text{slot}(t))\,\varepsilon_t \\
\varepsilon_t &= \sigma_t z_t, \qquad z_t \sim \widehat F_z \;\;\text{(empirical, iid)} \\
\sigma^2_{t+1} &= \omega + \big(\alpha + \gamma\,\mathbf 1[\varepsilon_t < 0]\big)\varepsilon_t^2 + \beta\,\sigma_t^2 \\
P_t &= P_0 \exp\!\Big(\textstyle\sum_{i\le t} r_i\Big)
\end{aligned}\;}$$

Three things about this deserve emphasis, because each was a decision:

**The variance recursion is driven by $\varepsilon_t$, not $r_t$.** The
innovation is deseasonalized and drift-free. Feeding the realized return back
in would make the fitted persistence depend on the drift setting and on which
time-of-day slot the anchor happened to fall in — the volatility model would
change when you toggled a drift switch, which is incoherent.

**$\sigma_t$ is a per-path state variable, not a schedule.** Every simulated
path carries its own variance. This is the entire difference from the previous
model, and §8.2 shows it is the only mechanism that reproduces the observed
horizon skew.

**$\widehat F_z$ is the empirical distribution of the fitted standardized
residuals** — Filtered Historical Simulation, Barone-Adesi et al. (1998). No
parametric family is imposed. It is demeaned and rescaled to exactly mean 0,
variance 1, so that $\mu$ is the *only* source of drift (otherwise the risk
premium enters twice) and $\sigma_t$ is the true per-step standard deviation.

### 2.1 The conditioning state

Three indicators, each discretized, combined into one integer:

$$s = 16\,b_{\text{PSAR}} + 4\,b_{\text{TSI}} + b_{\text{MACD}},
\qquad b_{\text{PSAR}}\in\{0,1\},\; b_{\text{TSI}},b_{\text{MACD}}\in\{0,1,2,3\}$$

giving $|S| = 32$ states. The state at bar $t$ conditions the return from $t$
to $t+1$ — the one alignment that, if wrong, is an undetectable look-ahead bug.
It is performed exactly once, in `service._build_snapshot`.

---

## 3. The Bayesian core: shrinkage is a conjugate posterior

This is the part that genuinely deserves the name.

**Model.** Within state $s$, treat the forward log returns as
$r_1,\dots,r_{n_s} \mid \mu_s \sim \mathcal N(\mu_s, \sigma^2)$ with $\sigma^2$
known (estimated separately, §5).

**Prior.** $\mu_s \sim \mathcal N(0, \tau^2)$ — centred at zero because the
default belief is *no edge*.

**Posterior.** Normal likelihood with a Normal prior is conjugate. Precisions
add:

$$\frac{1}{\sigma^2_{\text{post}}} = \frac{1}{\tau^2} + \frac{n_s}{\sigma^2},
\qquad
\mu_{\text{post}} = \frac{\dfrac{0}{\tau^2} + \dfrac{n_s \bar r}{\sigma^2}}
                          {\dfrac{1}{\tau^2} + \dfrac{n_s}{\sigma^2}}
                  = \bar r \cdot \frac{n_s}{n_s + \sigma^2/\tau^2}$$

Setting

$$\boxed{\;\kappa = \sigma^2/\tau^2\;}$$

gives exactly the implemented estimator:

$$\mu(s) = \bar r_s \cdot \frac{n_s}{n_s + \kappa}$$

(`estimate.fit_drift`). So the shrinkage is not a heuristic damping factor —
it is the posterior mean under an explicit prior, and $\kappa$ is the **prior
precision ratio**: how many observations of evidence it takes to move the
estimate halfway from 0 to the sample mean. The posterior variance is
$\sigma^2/(n_s+\kappa)$.

### 3.1 What the prior actually claims — and why κ must scale with H

$\kappa = 200$ sounds strong. In horizon terms it is not, and this is the most
important quantitative result in this document.

Invert the definition: $\tau = \sigma/\sqrt{\kappa}$. Over $H$ bars the drift
contributes $H\mu$ while noise contributes $\sigma\sqrt{H}$, so the prior's
implied **drift-to-noise ratio at the horizon** is

$$c \;=\; \frac{H\,\tau}{\sigma\sqrt H} \;=\; \tau\frac{\sqrt H}{\sigma}
      \;=\; \sqrt{\frac{H}{\kappa}}$$

At $\kappa = 200$, $H = 40$: $c = \sqrt{40/200} = 0.45$. **The prior permits
the drift to be 45% of the horizon's noise** — that is not a sceptical prior,
it is a permissive one. It is also, quantitatively, why P4 measured a
drift-to-noise ratio of 0.68 at 30m: the data can push the posterior past the
prior scale because the prior barely constrains it.

Inverting for a target $c$:

$$\boxed{\;\kappa \ge H / c^2\;}$$

To assert "any drift claim is at most 10% of the horizon's noise" ($c = 0.1$)
at $H = 40$ requires $\kappa \ge 4000$ — twenty times the current value. Note
$\kappa$ must scale **linearly with $H$**: a fixed $\kappa$ makes a stronger
claim the further out you forecast, which is backwards.

This is the principled version of the "shrink harder" option, with a formula
instead of a fudge factor. It is not yet implemented — `shrink_kappa` is still
a flat 200 — and it is the first thing to try if state drift is ever revisited.

### 3.2 Pooling

States with $n_s <$ `min_state_obs` fall back to the unconditional mean and set
`pooled = True`. This is a crude stand-in for a hierarchical model: proper
partial pooling would place a hyperprior over $\{\mu_s\}$ and let each state
borrow strength from the grand mean in proportion to its own precision, rather
than switching discretely at a threshold. The discrete switch is defensible
only because $\kappa$ already dominates at small $n_s$ — at $n_s = 50$ the
shrinkage factor is $50/250 = 0.2$, so a thin state is mostly prior anyway.

---

## 4. Where this is **not** Bayesian

### 4.1 The sampler is a plug-in predictive

A genuine posterior predictive integrates over parameter uncertainty:

$$p(y_{\text{new}} \mid \mathcal D)
   = \int p(y_{\text{new}} \mid \theta)\; p(\theta \mid \mathcal D)\; d\theta$$

What `simulate.simulate_paths` computes is

$$p(y_{\text{new}} \mid \hat\theta), \qquad \hat\theta = (\hat\mu, \hat\sigma, \hat\nu)$$

— the likelihood at a **point estimate**. That is a *plug-in* (or "estimative")
predictive. It systematically understates dispersion, because it treats
parameters as known when they are estimated.

**How much does this matter here?** Quantify rather than hand-wave. Posterior
SD of the mean is $\sigma/\sqrt{n+\kappa}$, and over the horizon it contributes
$H \cdot \sigma/\sqrt{n+\kappa}$ in quadrature with $\sigma\sqrt H$:

| Mode | Effective $n$ | Extra horizon width |
|---|---|---|
| `zero` | — | none — μ is fixed, not estimated |
| `unconditional` | ~5,000 | +0.4% relative — negligible |
| `state` | ~250 | **+4% relative** — small but real |

For $\sigma$ the effect is smaller still: with $n \approx 4{,}000$ the posterior
for $\sigma^2$ is extremely tight. So the plug-in approximation is *good* at
our sample sizes — but it is an approximation, and the name "posterior
predictive" currently describes the intent rather than the computation. §10
gives the concrete fix.

### 4.2 FHS is a bootstrap, not a posterior — and that is the point

The innovations come from resampling $\widehat F_z$, the empirical distribution
of fitted standardized residuals. This is nonparametric and frequentist. It is
also the *right* choice here, for a reason worth stating.

A Bayesian treatment of the innovation shape would put a prior over a family —
Student-t, skew-t, a Dirichlet-process mixture — and integrate. Every one of
those choices commits to something about the shape. With $n \approx 4{,}000$
residuals, the empirical distribution already pins the shape far more tightly
than any of our priors could justify, and it commits to nothing. The Bayesian
machinery would buy accuracy we do not need at the cost of an assumption we
cannot defend.

There is a classical result worth explicitly *not* invoking. If
$r \mid \sigma^2 \sim \mathcal N(\mu,\sigma^2)$ with
$\sigma^2 \sim \text{Inv-Gamma}(\alpha,\beta)$, then marginally
$r \sim t_{2\alpha}$ — integrating out an unknown variance produces a Student-t.
The previous model's $\nu \approx 4.5$ was sometimes described that way. It was
never that:

- The **conjugate** $t$ comes from *epistemic* uncertainty about $\sigma$, and
  its degrees of freedom grow with the sample. At $n\approx 4{,}000$ it gives
  $\nu \approx 4{,}000$ — indistinguishable from Gaussian.
- A fitted $\nu \approx 4.5$ is an *aleatoric* property of returns, and persists
  no matter how much data is collected.

They compose; they do not substitute. Conflating them is a category error, and
FHS sidesteps the question by not parameterizing the shape at all.

### 4.3 GJR-GARCH is quasi-MLE, not a posterior

$$\sigma^2_t = \omega + \big(\alpha + \gamma\,\mathbf 1[\varepsilon_{t-1}<0]\big)\varepsilon^2_{t-1} + \beta\sigma^2_{t-1}$$

Glosten, Jagannathan & Runkle (1993), estimated by **Gaussian quasi-maximum
likelihood**. Gaussian and not Student-t deliberately: QMLE is consistent for
the GARCH parameters even when the true innovations are non-Gaussian (Bollerslev
& Wooldridge 1992), which is the correct robustness posture when the plan is to
bootstrap the residuals rather than trust a shape. Fitting with a $t$ likelihood
would be more efficient *if* the $t$ were right, and inconsistent if it were not.

$(\hat\omega,\hat\alpha,\hat\gamma,\hat\beta)$ is a point estimate; no posterior
is carried. A Bayesian treatment would be a stochastic-volatility model with a
posterior over the whole volatility path — a substantially bigger build, and
§4.1's numbers say it would buy very little at these sample sizes.

Two properties of the fit are load-bearing and both are asserted at fit time:

- **$p = \alpha + \gamma/2 + \beta < 1$** (stationarity). This is what makes
  variance mean-revert to $\omega/(1-p)$. Its absence is precisely EWMA's defect
  — EWMA is IGARCH with $p \equiv 1$. A fit that hits the bound raises
  `GarchFitError` rather than producing a fan whose forecasts diverge.
- **$\gamma > 0$** (leverage). Reported, not enforced: a symbol without a
  leverage effect should be allowed to say so.

### 4.4 ν is moment-matched, and now purely descriptive

For a Student-t with $\nu > 4$, excess kurtosis is $6/(\nu-4)$, so
$\hat\nu = 4 + 6/\widehat{\text{ex.kurt}}$, clamped to $[3.5, 30]$.

Under `ewma_t` this was the parameter the sampler drew from. Under `gjr_fhs`
**nothing is drawn from a Student-t** — $\hat\nu$ is now a moment-matched
*description* of the bootstrap pool's tails, reported so the tail stays
comparable across the two models and nothing more. Method-of-moments on a fourth
moment that barely exists was always unstable; demoting it to a diagnostic
removes that instability from the model rather than clamping around it.

---

## 5. Volatility and horizon

### 5.1 Time-of-day: deseasonalize, fit, re-seasonalize

$$\tilde r_t = \frac{r_t}{f(\text{slot}(t))}
\;\longrightarrow\; \text{GJR-GARCH fit}
\;\longrightarrow\; r_h = \mu + f(\text{slot}(h))\,\varepsilon_h$$

$f$ is the per-session-slot standard deviation normalized to mean 1. Estimating
it per slot absorbs both the intraday U-shape **and** the overnight gap in one
mechanism: slot 0's return spans the gap, so its factor comes out large without
a separate overnight term.

Dividing it out *before* the GARCH fit is not cosmetic (Andersen & Bollerslev
1997). The intraday U-shape is a deterministic, perfectly periodic pattern. Fed
to the optimizer raw, it reads as autocorrelation in squared returns —
indistinguishable to the likelihood from genuine volatility clustering — and
inflates $\beta$. The model would then forecast that the 09:30 bar's high
volatility *persists*, when in fact it reverts by 11:00 every single day.

At daily frequency $f = [1.0]$ and the whole step is a no-op.

### 5.3 Variance over the horizon

The $k$-step-ahead variance forecast decays geometrically toward the long-run
level $\bar v = \omega/(1-p)$:

$$\mathbb E[\sigma^2_{t+k}] = \bar v + p^k\big(\sigma^2_{t+1} - \bar v\big)$$

so the $H$-step variance has a closed form,

$$\operatorname{var}(H) = H\bar v + \big(\sigma^2_{t+1} - \bar v\big)\frac{1-p^H}{1-p}$$

and the diagnostic the snapshot reports is the ratio against naive
square-root-of-time scaling:

$$\text{variance\_ratio}(H) = \frac{\operatorname{var}(H)}{H\,\sigma^2_{t+1}}$$

This is **below 1 when today is loud and above 1 when today is quiet**. EWMA is
pinned at exactly 1.0 in both directions, and §8.1 measures the cost: SPY's
realized $\operatorname{var}(40)/[40\operatorname{var}(1)]$ is 0.715, so the old
fan came out roughly 18% too wide at $H=40$.

Note the ratio approaches its limit as $O(1/H)$, not geometrically — the
starting transient is a fixed quantity spread over a growing average. With a
typical ~75-bar half-life, even $H=5{,}000$ is still ~17% from the asymptote.

### 5.2 Why there is no Itô correction

A recurring error in this kind of model is adding $-\tfrac12\sigma^2$ when
exponentiating. That term is required when converting an *arithmetic* drift to
a log drift: if $\mathbb E[P_T/P_0] = e^{mT}$ then the log drift is
$m - \tfrac12\sigma^2$.

We never hold an arithmetic drift. $\mu$ is estimated directly as the mean of
**log** returns and applied in log space, so it is already the log drift. Adding
the correction would bias every fan downward by $\tfrac12\sigma^2 H$. The
consequence is worth stating explicitly: the fan's **median** is
$P_0 e^{\mu H}$, while its **mean** is $P_0 e^{\mu H + \sigma^2 H/2}$. The
levels we extract are quantiles, so the median is the relevant centre.

---

## 6. Levels

Given simulated terminals $\{P_H^{(i)}\}_{i=1}^{N}$, the default method takes
empirical quantiles at $q \in \{0.15, 0.35, 0.65, 0.85\}$. `side` and `rank`
describe position **in the forecast distribution**, not relative to spot.

The `min_move` filter is

$$\text{min\_move} = c_m \cdot \operatorname{sd}\!\big(\log(P_H/P_0)\big),
\qquad c_m = 0.25$$

Deriving it from horizon $\sigma$ rather than fixing a percentage is what
reproduces the reference charts' scaling across timeframes (1.57% at 30m vs
3.71% at 2h — a 2.36× that tracks $\sqrt{H}$ widening). $c_m = 0.25$ is
back-solved from **one** reference chart and remains a hypothesis.

Density-mode levels (KDE modes) are offered for comparison but are **not
validatable by PIT** — §7 is a statement about quantiles specifically.

---

## 7. Validation: the probability integral transform

### 7.1 The theorem

If $Y$ has continuous CDF $F$, then $F(Y) \sim \text{Uniform}(0,1)$. So if our
predictive CDF $F_i$ at anchor $i$ is correct, the realized price $y_i$ satisfies

$$u_i = F_i(y_i) \sim \text{Uniform}(0,1)$$

This is Rosenblatt (1952); Dawid (1984) named the property *probabilistic
calibration*. It is the reason a distributional forecast can be scored at all:
a fan chart cannot be graded on how close a point was, but uniformity of the
PIT series is a sharp, testable claim.

In practice $u_i$ is estimated as the fraction of simulated terminals below the
realized price. That estimate carries Monte Carlo noise of
$\sqrt{u(1-u)/N}$ — about 1.1pp at $N = 2{,}000$ — which is why the harness
reads the **full** terminal sample rather than the 500-path render subset.

### 7.2 Reading failures

| PIT shape | Diagnosis |
|---|---|
| Mass at 0 and 1 | Fan **too narrow** — reality escapes it more than claimed |
| Mass in the middle | Fan **too wide** — the model is hedging |
| Skewed | Fan **biased** — drift is wrong |

Coverage at quantile $q$ is $\widehat{C}(q) = \frac1n\sum \mathbb 1[u_i < q]$,
which should equal $q$.

### 7.3 Independence

Anchors closer together than $H$ share outcome bars, so their PIT values are
correlated and $n$ would overstate the evidence. The harness strides by $H$ by
default. This is the same overlapping-label problem the spec flags for Engine A
(§10.1 of the source plan) and that `sim/permutation.py` handles for strategy
backtests.

---

## 8. What the calibration actually found

First real run, `scripts/ppl_calibrate.py`, SPY daily, $H=40$, 2,000 paths,
stride 40, `ohlcv_daily` 2006–2026, $n_{\text{eff}} = 108$:

| Drift mode | mean PIT | q0.15 | q0.35 | q0.65 | q0.85 | Verdict |
|---|---|---|---|---|---|---|
| `zero` | 0.619 | −3.9pp | **−19.3pp** | **−21.5pp** | −5.4pp | FAIL |
| `unconditional` | 0.580 | −3.0pp | **−16.5pp** | **−10.4pp** | −0.7pp | FAIL |
| `state` | 0.594 | −3.0pp | **−14.6pp** | **−17.8pp** | −5.4pp | FAIL |

**All three fail.** Mean PIT well above 0.5 in every mode says the realized
price lands above the fan's centre far more often than it should — a **location
bias**, not a width problem (the extreme quantiles are close to nominal in the
`unconditional` run).

Two conclusions, and they point in opposite directions from the P4 decision:

1. **Zero drift is itself a claim, and PIT rejects it.** For an equity index
   the baseline drift is real — it is the equity risk premium, not a timing
   edge — and setting it to zero biases the fan low. `unconditional` is the
   best of the three on mean PIT and on three of four quantiles. The P4
   argument against *state* drift (a max-of-32 pick at t≈3) still stands
   untouched; what P3 shows is that "no timing claim" was wrongly collapsed
   into "no drift at all". Those are separable, and the code now separates them.
2. **Something beyond drift is still wrong** — and §8.2 identifies it.

### 8.2 A hypothesis that was wrong, and the measurement that killed it

The first diagnosis was **skew**. The per-symbol verdicts split cleanly along
one axis, which made it compelling:

| Symbol | mean PIT | Verdict | Skew of realized 40-bar returns |
|---|---|---|---|
| SPY | 0.580 | FAIL | **−1.68** |
| DIA | 0.565 | FAIL | **−1.63** |
| IWM | 0.540 | FAIL | **−1.40** |
| QQQ | 0.518 | pass | — |
| GLD | 0.486 | pass | +0.10 |
| TLT | 0.484 | pass | +0.25 |

The three failures were exactly the three with strong negative skew; the two
with skew ≈ 0 passed. The argument written down at the time: our innovations are
symmetric, so the simulated median equals its mean $\mu H$, while a left-skewed
asset's realized median sits *above* its mean. Proposed fix: **skewed
innovations** (skew-t, or two-piece Student-t).

**That fix would not have worked, and the reason is worth keeping.** For iid
innovations with one-bar skew $\gamma_1$, the skew of a sum of $H$ of them is

$$\operatorname{skew}\Big(\textstyle\sum_{t=1}^{H} r_t\Big) = \frac{\gamma_1}{\sqrt H}$$

Skew **decays** as $1/\sqrt H$ under iid — that is just the CLT arriving. So
measure both ends:

| | SPY, measured |
|---|---|
| Skew at $H=1$ | −0.44 |
| Skew at $H=40$, predicted by iid ($-0.44/\sqrt{40}$) | **−0.069** |
| Skew at $H=40$, **actually observed** | **−1.68** |

Observed skew is **24× the iid prediction**, and it moves in the opposite
direction — *growing* with horizon where iid says it must shrink. No choice of
innovation distribution can produce that, because the $1/\sqrt H$ decay is a
property of summing iid variables, not of which distribution they come from.

Horizon skew is not a property of the marginal distribution. It is produced by
**path dynamics**, where a negative draw raises the volatility of subsequent
draws — and that requires a leverage term, not a skewed innovation.

### 8.3 The real diagnosis: three measured violations

Measured on SPY daily over the fit window:

| Property | Measured | What `ewma_t` + iid-$t$ assumes |
|---|---|---|
| Volatility clustering | $\operatorname{corr}(\lvert r_t\rvert,\lvert r_{t-1}\rvert) = +0.32$ | captured — EWMA does cluster |
| **Leverage effect** | RMS after a down day is **1.28×** after an up day | symmetric — no distinction |
| **Variance mean-reversion** | $\operatorname{var}(40)/[40\operatorname{var}(1)] = 0.715$ | **1.0** — variance scales linearly |

The last two are the failures, and they are structural rather than
parametric — no value of $\lambda$ or $\nu$ fixes either:

- EWMA **is** IGARCH: $\lambda + (1-\lambda) = 1$, so persistence is identically
  1, no long-run variance exists, and the fan grows as $\sigma\sqrt H$ forever.
  At $H=40$ that made it ~18% too wide.
- iid symmetric innovations have no channel through which the *sign* of one bar
  can affect the next bar's variance.

GJR-GARCH(1,1) addresses both with one term each: $p = \alpha+\gamma/2+\beta < 1$
gives mean-reversion, and $\gamma > 0$ gives leverage. FHS then supplies the
innovation shape without assuming one.

### 8.4 Results after the rewrite

Same harness, same anchors, same drift mode (`unconditional`), $H = 40$, 2,000
paths, 6 ETFs. **The only thing that changes between columns is
`--vol-model`.**

#### 1h — where the test has power

This is the decisive run, and it is decisive *because* $n \approx 870$ per
symbol rather than daily's 108 (§8.1: at $n = 108$ the gate barely
discriminates).

| Symbol | `ewma_t` KS (p) | `gjr_fhs` KS (p) |
|---|---|---|
| SPY | 0.062 (**0.002**) | 0.038 (0.163) |
| QQQ | 0.071 (**0.003**) | 0.035 (0.396) |
| IWM | 0.055 (**0.011**) | 0.033 (0.289) |
| DIA | 0.058 (**0.006**) | 0.028 (0.484) |
| GLD | 0.054 (**0.012**) | 0.035 (0.241) |
| TLT | 0.035 (0.233) | 0.049 (**0.037**) |

**The old model is rejected at $p<0.05$ for five of six symbols. The new model
is not rejected for five of six.** Pooled ($n \approx 4{,}950$): KS 0.046 →
**0.028**, mean PIT 0.511 → **0.506**, and every quantile error inside 2.2pp
under `gjr_fhs`.

TLT is the one that goes the other way, and it is also the one where 58 of 886
anchors hit the boundary case in §4.3 — its 2008–09 windows straddle a
volatility regime change (annualized vol 8.9% → 16.3% → 18.1%). Recorded rather
than explained away.

#### 1d

| Pooled ($n \approx 615$) | `ewma_t` | `gjr_fhs` |
|---|---|---|
| mean PIT | 0.528 | **0.510** |
| KS vs Uniform | 0.082 (**p = 0.000**) | **0.048 (p = 0.119)** |
| q0.15 | −2.8pp (z = −1.96) | −2.5pp (z = −1.72) |
| q0.35 | **−6.3pp (z = −3.26) MISS** | −3.5pp (z = −1.80) ok |
| q0.65 | −2.8pp (z = −1.47) | **+0.5pp** (z = +0.27) |
| q0.85 | +1.7pp (z = +1.17) | +1.7pp (z = +1.16) |
| **Verdict** | **FAIL** | **PASS** |

| Symbol | `ewma_t` KS (p) | `gjr_fhs` KS (p) | mean PIT: old → new |
|---|---|---|---|
| SPY | 0.196 (**0.000**) | 0.133 (0.041) | 0.578 → 0.546 |
| DIA | 0.178 (**0.002**) | 0.121 (0.077) | 0.561 → 0.529 |
| IWM | 0.142 (**0.022**) | 0.101 (0.207) | 0.540 → 0.515 |
| QQQ | 0.065 (0.898) | 0.077 (0.738) | 0.517 → 0.493 |
| GLD | 0.084 (0.413) | 0.089 (0.342) | 0.488 → 0.486 |
| TLT | 0.065 (0.733) | 0.059 (0.838) | 0.483 → 0.484 |

IWM and DIA move from rejection of uniformity to no evidence against it. GLD
and TLT — which never had the problem — are unchanged within noise, which is
the correct **null** result: the fix targets a defect those two do not have, so
it should not have moved them.

#### The mechanism, checked directly

The improvement is not a black box. Current-window fits, all eight symbols
tried, both intervals, every one converging in the interior:

| | measured range |
|---|---|
| $\lvert r\rvert$ lag-1 autocorrelation, **raw** | +0.13 … +0.36 |
| $\lvert r\rvert$ lag-1 autocorrelation, **after the GARCH filter** | −0.04 … +0.10 |
| persistence $p$ | 0.935 … 0.989 (all interior) |
| shock half-life | 10 … 60 bars |
| variance ratio at $H{=}40$ | 0.86 … 1.33 |

The first two rows are the load-bearing check: the clustering the model claims
to remove **is** removed, which is what licenses resampling the residuals as
iid. It is reported on every snapshot so the premise stays falsifiable.

The leverage ratios are a cross-sectional sanity check the model was not tuned
to pass: equity indices come out 3.4–16×, TLT ≈ 1.0 (bonds have no crash
asymmetry), and **GLD 0.66–0.77 — below 1**, i.e. gold's *up* moves raise
volatility more than its down moves. A model mechanically manufacturing
$\gamma > 0$ everywhere could not produce that.

**Fit failures are reported, never substituted** (§4.3): 1 anchor in 615 at 1d,
58 in ~5,000 at 1h, all clustered in regime-change windows.

### 8.5 What remains is drift, and it is roughly one standard error

Every surviving per-symbol miss is on q0.35, all in the same direction, and all
on **US equity indices**: mean PIT SPY 0.549, DIA 0.531, IWM 0.516 against GLD
0.484, TLT 0.484. That is a **location** signature, not a width one — the widths
now pass.

Quantify it. Mean PIT 0.549 corresponds to a centring error of roughly
$\Phi^{-1}(0.549) \approx 0.12\,\sigma_H$. With $\sigma_H \approx 6\%$ at
$H = 40$, the fan's centre is about 0.74% low over 40 trading days — call it
**4.7%/yr** of missing drift.

Now the standard error of the drift estimator itself. Estimating a mean return
from $T$ years of data at annual volatility $\sigma_a$:

$$\operatorname{SE}(\hat\mu_{\text{annual}}) = \frac{\sigma_a}{\sqrt T}
= \frac{16\%}{\sqrt{16}} = 4\%\text{/yr}$$

**The residual miss is about one standard error of the estimator.** This is the
classical result that the equity risk premium is nearly unestimable from any
practical sample — Merton (1980): variance is estimable from high-frequency
data, the mean is not, and no amount of sampling frequency helps.

So the honest position is:

- The **width and shape** of the fan are now calibrated. That is what §8.4 shows
  and it is what the volatility model was responsible for.
- The **centre** carries an irreducible estimation error of order 4%/yr. Tuning
  drift upward until PIT passes would be fitting the model to the evaluation
  window — exactly the failure mode documented across EXP-26 and DT-0..3 — and
  is refused.
- The sample period matters: these anchors span a strong-return era, so a
  trailing mean *systematically* under-predicts within it. That is a property of
  the test window, not a bias to correct.

### 8.1 The gate is underpowered at single-symbol daily $n$

The spec's ±5pp tolerance needs enough observations to be meaningful:

#### Power

| $n$ | SE at $q{=}0.35$ | ±5pp expressed in SE |
|---|---|---|
| 108 | 4.59pp | **1.09** |
| 500 | 2.13pp | 2.34 |
| 1,000 | 1.51pp | 3.31 |
| 4,000 | 0.75pp | 6.63 |

At $n = 108$ a **perfectly calibrated** model misses ±5pp somewhere in four
quantiles a large fraction of the time, purely by chance. Reporting a pass/fail
against that gate at this $n$ would be close to meaningless.

The observed misses survive anyway — $z = -3.59$ and $-2.26$ are real effects,
not noise — so the FAIL verdict stands. But the gate needs
$n \gtrsim 1{,}000$ to discriminate, which means **pooling across symbols**
(40 liquid names × ~108 anchors ≈ 4,300). Cross-sectional correlation makes
those not fully independent, so pooled $n$ overstates evidence somewhat — far
less than a single symbol understates it.

Recommended amendment: judge a miss by **both** $|{\rm error}| > 5$pp
(product relevance) **and** $|z| > 2$ (statistical reality). `CoverageBand`
already computes both.

---

## 9. Look-ahead safety

Three places where the future could leak, and what prevents it:

1. **PSAR** is a path-dependent recursion. It is causal by construction, pinned
   by `test_psar_is_causal` (appending bars must not change earlier values).
2. **State/return alignment** — $s_t$ conditions $r_{t+1}$, done once in
   `_build_snapshot`.
3. **Calibration refits per anchor** on the prefix only. `run_pit` passes
   `frame.iloc[:i+1]`; `test_pit_refits_per_anchor_and_never_sees_the_future`
   asserts every call receives a distinct, monotonically growing prefix. A
   harness that fit once on all history and replayed would report excellent
   coverage for a model that cannot be run live.

---

## 10. What would make it a genuine posterior predictive

Concretely, and in rough order of value:

0. **Skewed innovations** (§8.2) — the one fix the data is actually asking for.
   Everything below is refinement; this is a correctness bug.
1. **Integrate σ² uncertainty.** Place a conjugate Inv-Gamma posterior on
   $\sigma^2$ and draw a fresh $\sigma^{2(i)}$ *per path*. At $n\approx4{,}000$
   this widens the fan very little — but it makes the computation match the
   name, and it composes correctly with the aleatoric fat tails (§4.2).
2. **Integrate μ uncertainty.** Draw $\mu^{(i)} \sim
   \mathcal N(\mu_{\text{post}}, \sigma^2/(n_s+\kappa))$ per path. Worth ~4%
   extra horizon width in `state` mode, negligible otherwise.
3. **Scale κ with H** per §3.1, $\kappa \ge H/c^2$, so the prior's drift claim
   stays a fixed fraction of horizon noise instead of growing with the forecast.
4. **Hierarchical pooling** over states, replacing the discrete
   `min_state_obs` switch with proper partial pooling.
5. **Stochastic volatility** with a posterior over the volatility path, in place
   of the EWMA point estimate. Largest build; do this only if PIT says the vol
   model is the binding constraint.

Items 1–3 are small and would let the name be used without qualification.
Until then the accurate description is: *a conjugate-shrinkage conditional
drift, plugged into a Monte Carlo simulation of a fat-tailed random walk, and
validated by PIT coverage.*

---

## References

- Rosenblatt, M. (1952). "Remarks on a Multivariate Transformation." *Ann. Math. Statist.* — the PIT theorem.
- Dawid, A. P. (1984). "Present Position and Potential Developments: Some Personal Views." *JRSS A* — probabilistic calibration.
- Gneiting, T., Balabdaoui, F., Raftery, A. (2007). "Probabilistic forecasts, calibration and sharpness." *JRSS B* — why calibration alone is not enough; sharpness subject to calibration.
- Gelman et al., *Bayesian Data Analysis*, 3rd ed. — conjugate Normal–Normal (Ch. 2), Normal–Inverse-Gamma and the Student-t marginal (Ch. 3), posterior predictive checking (Ch. 6).
- Stephens, M. A. (1970). "Use of the Kolmogorov–Smirnov… statistics." *JRSS B* — the small-sample KS correction used in `ks_uniform`.
- J.P. Morgan/Reuters (1996). *RiskMetrics Technical Document* — the λ = 0.94 EWMA estimator.
- Internal: [`research/mcpt_methodology.md`](../research/mcpt_methodology.md) (why validation is mandatory here), [`research/findings.md`](../research/findings.md) DT-0..3 / EXP-45 / EXP-55 (why directional claims default off).
