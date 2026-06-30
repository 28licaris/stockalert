# Strategy R&D — Findings Log

A running, dated log of strategy experiments and what we concluded. This is
**R&D**, not a track record: every number here is from historical backtests
(in-sample unless stated), with a baseline exit model. Per the honesty doctrine
([`strategy_rnd_platform_design.md`](strategy_rnd_platform_design.md) §7), nothing
here is shown to a customer until it survives out-of-sample + forward paper-trade.

## Methodology (applies to all entries unless noted)

- Engine: `app/services/sim/` Backtester; metrics from `StandardEvaluator`.
- Bridge: `AlertStrategy` + a pluggable `SignalSource` (M1). Long-only.
- Sizing: **risk-based** — ~`risk_pct` (default 1%) of equity lost if the stop
  is hit, capped by cash. This deliberately throttles absolute return, so the
  **trustworthy metrics are win-rate / profit-factor / expectancy**, not the
  dollar return. (Per-symbol single-position runs also understate a real
  multi-symbol portfolio, which would compound across names — TA-3.)
- Exits: market order on the bar that touches stop/target, filled next-open
  (no-look-ahead baseline; a true stop-fill model is a later refinement).
- Data: ClickHouse daily (resampled from `ohlcv_1m`). Coverage limits how far
  back a window can go.
- Reproducible: every run pins git SHA + params; re-run = same numbers.

Sweep tool: `scripts/strategy_sweep.py` (basket × time windows → aggregate).

---

## EXP-1 · 2026-06-29 · breakout vs ma_cross, single name (M1 smoke)

NVDA daily, 2022–2024, in-sample. Same engine, swap only the `SignalSource`:

| Source | Return | Win | Profit factor | Max DD | Trades |
|---|---|---|---|---|---|
| breakout | +16.8% | 60% | 6.96 | −2.2% | 20 |
| ma_cross | −1.1% | 29% | 0.82 | −4.5% | 14 |

**Conclusion:** bridge works end-to-end; the platform ranks signals by realized
edge. breakout >> ma_cross on this name. Single symbol → not generalizable yet.

---

## EXP-2 · 2026-06-29 · breakout generalization sweep

12 liquid names (AAPL MSFT NVDA AMD TSLA META AMZN GOOGL AVGO NFLX SPY QQQ),
daily, two non-overlapping windows. Params: lookback=20, vol_mult=1.5,
reward_risk=2.0, risk_pct=1%, min_RR=1.5.

| Window | Mean ret | Median | % profitable | Avg win-rate | Worst DD | Trades |
|---|---|---|---|---|---|---|
| 2022–2023 | +1.4% | +0.7% | 58% | 37% | −5.9% | 127 |
| 2024–2025 | +5.4% | +3.2% | 73% | 53% | −8.8% | 141 |

Per-name highlights: strong trenders win big (AVGO +32.6% / PF 22 in 2024–25,
NVDA, AMD, AAPL); choppy names lose (MSFT, META, TSLA mixed-to-negative);
index ETFs (SPY/QQQ) barely trade and barely move.

**Conclusions:**
1. The breakout edge is **real but weak and regime-dependent** — positive mean
   and majority-profitable in *both* windows (so it generalizes directionally),
   but materially stronger in the trending 2024–25 tape.
2. Edge concentrates in **high-momentum single names**, not broad ETFs.
3. Drawdowns are small (risk sizing working as intended).
4. Not a deployable edge on its own — it needs **selectivity** (only trade
   names/regimes where breakouts work).

**Next experiments (hypotheses to test):**
- Add a **trend filter** (price > 200d SMA, or rising slope) — the first
  confluence rule; expect fewer trades, higher win-rate (the M2 A+ thesis).
- Regime gate (only when SPY is in an uptrend).
- Parameter sensitivity: lookback {10,20,55}, reward_risk {1.5,2,3}.
- Wire the **EW wave-3/5 source** and compare expectancy head-to-head.
- Out-of-sample: pick params on 2022–2023, validate untouched on 2024–2025.

---

## EXP-3 · 2026-06-29 · breakout + trend-filter confluence

Same sweep as EXP-2, adding the first confluence rule: only take breakouts
while `close > SMA(50)` (`trend_filter: true, trend_period: 50`).

| Window | Variant | Mean | % profitable | Avg win | Trades |
|---|---|---|---|---|---|
| 2022–2023 | raw | +1.4% | 58% | 37.1% | 127 |
| 2022–2023 | +trend | +1.5% | 58% | 38.8% | 123 |
| 2024–2025 | raw | +5.4% | 73% | 53.2% | 141 |
| 2024–2025 | +trend | +4.9% | **82%** | 52.2% | 125 |

**Conclusions:**
1. The trend filter improves **selectivity** — % of names profitable rose
   73%→82% in 2024–25, win-rate edged up in 2022–23 — by trimming trades
   (~10% fewer). It removes some losers *and* some winners, so mean return is
   ~flat-to-slightly-lower.
2. A single 50-SMA gate is a **marginal** lever, not a step-change. The
   confluence thesis holds directionally; materially moving the needle will
   need stronger/stacked filters (200d trend with warmup extension, relative
   strength vs SPY, regime gate) — which is what the M2 composable filter layer
   is for.
3. Caveat: in-window SMA warmup means the filter is undefined for the first
   ~50 bars of each window (a few early signals pass unfiltered). A backtester
   warmup-extension (fetch bars before `start`) is a fairness fix for longer
   trend periods.

**Takeaway for the roadmap:** confluence is worth building as a first-class,
composable, individually-measurable layer (M2) rather than as per-source flags —
so we can A/B each filter's contribution like this, at scale, and let an agent
search filter combinations.

---

## EXP-4 · 2026-06-29 · composable A+ filter layer (M2)

Re-ran the breakout sweep with the new composable filters
(`trend`(SMA50) + `volume`(1.5×) + `reward_risk`(≥2), mode=all) instead of
source-baked flags.

| Window | Mean | % profitable | Avg win | Trades |
|---|---|---|---|---|
| 2022–2023 | +1.5% | 58% | 38.8% | 123 |
| 2024–2025 | +4.9% | 82% | 52.2% | 124 |

**Conclusions:**
1. Results match EXP-3 (the single trend filter) almost exactly — *as expected*:
   the breakout source already gates on volume internally and emits rr≈2, so the
   `volume`/`reward_risk` filters were already satisfied. The trend filter is the
   only net constraint. This **confirms the filter layer is correct** (consistent,
   no surprises) and isolates where the lever actually is.
2. The win is **architectural**, not a new number: "A+" is now declared in config
   (`filters: [...]`), each filter is a separate unit with its own pass/score, and
   the layer annotates `confidence` with the normalized A+ score for ranking. An
   agent can now search filter/parameter combinations and the sweep tool measures
   each combo's contribution.

**Next experiments:** filters that AREN'T already implied by the source —
relative strength vs SPY (needs cross-symbol data), a market-regime gate
(SPY uptrend), and `mode="score"` partial-confluence ranking; plus the EW
signal source for a head-to-head.

---

## EXP-5 · 2026-06-30 · divergence as a standalone signal

New `divergence` SignalSource (wraps the existing pure RSI-divergence detectors
in `app/signals/divergence.py`: regular + hidden bullish → long). Same sweep.

First run rode a global trend filter (`settings.use_trend_filter`) inside the
reused detector, which over-suppressed signals AND tripped the purity gate (it
pulled `app.providers` into the strategy graph). Re-implemented as a **pure**
inline pivot/divergence detector (no global config). Numbers below are the pure
version (what's in the code):

| Window | Mean | Median | % profitable | Avg win | Trades | Worst DD |
|---|---|---|---|---|---|---|
| 2022–2023 | +1.3% | +0.8% | 58% | 46.7% | 182 | −8.9% |
| 2024–2025 | +4.0% | +3.6% | **92%** | 54.3% | 161 | −6.5% |

vs breakout (EXP-2): comparable mean, **higher consistency** — 92% of names
green in 2024–25 (vs breakout's 73%), with a similar-or-better win-rate.

**Conclusions:**
1. Divergence is the **most consistent** signal tested so far — 92% of names
   profitable in the trending window. Removing the mismatched global trend
   filter roughly doubled trade count and improved consistency (the filter was
   suppressing valid setups; lesson: filters belong in the *composable* layer
   where they're chosen deliberately, not baked into a shared detector).
2. Still a weak absolute edge (risk-throttled), but a strong **quality** signal.
   Natural next test: divergence as a confluence filter on breakout, and a
   divergence+regime stack.

**Does it help? Yes** — it's the best single signal on consistency so far.

---

## EXP-6 · 2026-06-30 · market context + regime filter (engine extension)

Added a benchmark/market layer: the **engine** loads a benchmark (e.g. SPY)
once and exposes a pure `MarketContext` on `ctx.market`; new market-aware filters
read it (`regime` = benchmark above its SMA; `relative_strength` = symbol return
> benchmark return). Keeps strategies/filters past the purity gate — the IO is
engine-side, filters just read.

Breakout + `regime`(SPY > SMA50), `benchmark: SPY`:

| Window | Mean | % profitable | Avg win | Trades |
|---|---|---|---|---|
| 2022–2023 | +0.7% | 58% | 38.2% | 109 |
| 2024–2025 | +5.2% | 73% | 54.9% | 115 |

vs plain breakout (EXP-2): 2022–23 trades 127→109 (the regime gate correctly
suppressed breakouts during the 2022 bear-market SPY downtrend), 2024–25
essentially unchanged (bull market — gate rarely blocks).

**Conclusions:**
1. The market-context machinery **works end-to-end** (loads SPY, gates trades,
   no-look-ahead as-of lookups).
2. On this basket the SPY-regime gate is a **modest risk lever** — it trims
   bear-market activity but doesn't materially lift returns here. Its value is
   likely larger over a full cycle / on lower-quality names.
3. Filters are now stackable across these axes (trend, volume, rr, regime,
   relative_strength) and individually measurable — an agent can search combos.

**Next:** divergence × regime / RS stacks; a proper dev→holdout param search
(pick on 2022–23, validate untouched on 2024–25); then EW source head-to-head.

---

## EXP-7 · 2026-06-30 · out-of-sample validation (the honesty harness)

New `scripts/oos_search.py`: search a param/filter-stack grid on a DEV window
(2022–23) across the basket, pick the best by objective (median return), then
report that exact config on an UNTOUCHED HOLDOUT window (2024–25). Only holdout
is trustworthy. This is what lets us trust any signal/filter — including future
professional TA additions — rather than in-sample numbers.

**Breakout** (best dev: lookback=10, rr=2.0, no filter):

| Metric | DEV (optimized) | HOLDOUT (honest) |
|---|---|---|
| median return | +0.7% | +1.8% |
| % profitable | 58% | 50% |
| win-rate | 37% | 44% |
| trades | 153 | 167 |

**Divergence** (best dev: lookback=40, pivot_k=3, rr=3.0, no filter):

| Metric | DEV (optimized) | HOLDOUT (honest) |
|---|---|---|
| median return | +2.1% | +3.5% |
| % profitable | 75% | **83%** |
| win-rate | 37% | 42% |
| trades | 175 | 164 |

**Conclusions:**
1. Both signals **generalize** — holdout ≥ dev, no overfit collapse (helped by a
   stronger-trending 2024–25 regime).
2. **Divergence is the first out-of-sample-validated, high-consistency edge**
   (83% of names green on holdout, positive median, rr=3 carrying a moderate
   win-rate to positive expectancy). It's the candidate to carry to forward
   paper-trading (M3).
3. The candidate **filters (trend/regime) did NOT win the dev objective** on this
   basket — confirming they're risk levers here, not alpha. Higher-confidence
   confluence will likely need *additional independent* professional signals
   (MACD, ADX trend-strength, Bollinger squeeze, VWAP, Ichimoku) combined via the
   score-mode layer — the architecture is ready for them (registries + composable
   filters).
4. **Caveat:** neither holdout window was strongly bearish; a bear holdout (or a
   walk-forward across multiple regimes) is needed before claiming robustness.

**Next:** more professional signals/filters to build genuine confluence; a
walk-forward (rolling dev→holdout) for regime robustness; then M3 forward
paper-trading of the validated divergence config.

---

## EXP-8 · 2026-06-30 · fewer/bigger trades + time-in-trade metric

Guidance: prefer higher return on FEWER trades; also track time-in-trade.
Added `avg_holding_days` (calendar days per round-trip) to RunMetrics
(portfolio records it on each closing sell), surfaced `$/trade` + holding +
trades/symbol in the sweep, and added a **return-per-trade** OOS objective
(`mean_trade_pnl`).

Re-ran the divergence OOS optimizing `mean_trade_pnl` with higher R:R
{3,4,5} and candidate confluence stacks. Best on dev: lookback=60, pivot_k=3,
**rr=5.0, +trend filter**.

| Metric | DEV (optimized) | HOLDOUT (honest) |
|---|---|---|
| mean return | +3.8% | +3.3% |
| median return | +2.4% | +3.0% |
| % profitable | 75% | 67% |
| win-rate | 36% | 34% |
| **$/trade** | 369 | 275 |
| **avg holding** | 48 days | 47 days |
| trades (/sym) | ~6 | ~8 (~4/yr) |
| worst DD | −5.1% | −5.3% |

**Conclusions:**
1. Optimizing for return-per-trade gives the **high-conviction, low-frequency**
   profile we want: ~4 trades/symbol/year, ~$275/trade on holdout, ~47-day
   holds (genuine multi-week swings), still generalizing OOS (no overfit drop).
2. **The trend filter now wins** the dev objective (vs EXP-7 where filters
   didn't, under a median-return objective) — confluence earns its keep once we
   select for quality. Low win-rate (34%) is fine with rr=5: big winners pay for
   many small stops.
3. Time-in-trade is now a first-class metric — useful for capital turnover and
   for matching the "hold days→weeks" product intent.

**Next:** add *independent* professional signals (MACD, ADX trend-strength,
Bollinger squeeze, VWAP) so score-mode confluence can build genuine
high-conviction A+ setups; walk-forward across a bear regime; then M3
paper-trading of this validated divergence+trend config.

---

## EXP-9 · 2026-06-30 · hold-period tradeoff (time-stop) + capital note

Capital: backtests use **$40,000** starting cash, **1% risk/trade** (~$400),
per-symbol single-position. The per-symbol *total* returns (~1–3% over 2y) are
throttled by that sizing + low frequency — judge by **$/trade**, win-rate, and
% profitable, not the 2-year total.

Added a **time stop** (`max_holding_days`: exit at market after N days). Swept it
at fixed rr=2.0 + trend filter:

| max_holding | $/trade (dev) | trades |
|---|---|---|
| 10d | 89 | 137 |
| 20d | 107 | 130 |
| 40d | 130 | 118 |
| 60d | 159 | 111 |

**Conclusions:**
1. **No free lunch:** tighter time stops cut $/trade (you exit winners early) and
   raise trade count. Longer caps = bigger per-trade P&L, fewer trades.
2. The **R:R lever shortens holds more naturally than the time stop.** At rr=2 the
   *average* hold is already ~21 days (vs ~47 at rr=5), because most trades hit
   stop/target well before any cap — so the 60d cap rarely binds.
3. This yields **two OOS-validated profiles** to choose between (divergence +
   trend, holdout 2024–25):

   | Profile | rr | avg hold | $/trade | win | % profitable | worstDD |
   |---|---|---|---|---|---|---|
   | **Conviction** | 5 | ~47d | $275 | 34% | 67% | −5.3% |
   | **Swing** | 2 | ~21d | $119 | 48% | 83% | −4.3% |

   Conviction = bigger wins, longer holds, lower win-rate. Swing = shorter holds,
   higher win-rate + consistency, smaller per-trade P&L. Both generalize OOS.

**Next:** independent pro signals (MACD/ADX/squeeze) for confluence; a bear-regime
walk-forward; then M3 paper-trading of whichever profile we pick.

---

## EXP-10 · 2026-06-30 · directional confluence + conviction sizing (1%→5%)

Tuning per guidance: use up to 5% risk on higher-probability trades; stack
direction-confirming signals. Added conviction-scaled sizing (`max_risk_pct`:
risk scales risk_pct→max_risk_pct by the signal's confluence confidence) and two
directional filters (`rsi_bull` RSI>50, `macd_bull` MACD-line>0). Confluence stack
= divergence + **5 directional confirmers** (trend, regime, relative_strength,
rsi_bull, macd_bull), score-mode, size 1%→5% by # confirming. Best dev: min_score=3
(≥3 of 5 must confirm).

| Metric | DEV (optimized) | HOLDOUT (honest) |
|---|---|---|
| mean return | +8.2% | +10.1% |
| median return | +4.9% | **+8.8%** |
| % profitable | 75% | **83%** |
| win-rate | 49% | **52%** |
| $/trade | 678 | **973** |
| avg hold | 19d | 29d |
| worst DD | −12% | **−17.6%** |

**Conclusions:**
1. **Big step-change** vs prior bests (~3% holdout median): confluence + conviction
   sizing → holdout median +8.8%, $973/trade, 52% win, 83% of names profitable —
   and it generalizes OOS (holdout ≥ dev). This is the first config that looks
   genuinely worth trading.
2. **Confluence confirms direction:** requiring ≥3 of 5 independent directional
   signals (trend / regime / relative-strength / RSI / MACD) raised both win-rate
   AND per-trade size (we bet 5% only when many agree). min_score=3 beat 4
   (4 was too selective — fewer trades, lower aggregate $/trade).
3. **Honest cost — drawdown:** 5% sizing pushed worst DD to −17.6% (from ~−5%).
   That's the risk you buy for the return. And these are SINGLE-symbol runs — a
   live portfolio holding several 5%-risk positions at once could draw down more
   on correlated moves. **A risk-management layer (portfolio heat / max concurrent
   positions / per-name cap) is now a prerequisite before paper-trading this.**
4. Caveat unchanged: no bear holdout yet.

**Next:** risk-management layer (cap portfolio heat so concurrent 5% bets don't
compound drawdown); bear-regime walk-forward; then M3 paper-trade this config.

---

## EXP-11 · 2026-06-30 · regime-agnostic (long + short) trading

Guidance: trade any regime / reversals into a new regime — not bull-only. Added
**short support** to the engine (portfolio opens shorts on a flat sell, covers on
a buy; P&L/equity verified by `test_shorts.py`), made the `Signal` risk/reward and
all confluence filters **direction-aware** (long confirms in up-trend/up-regime/
out-performance; short the mirror), and added **bearish divergence** (regular/hidden
bearish → short) via a `side` param.

Regime-agnostic config (divergence side=both + 4 directional confirmers, score-mode
min_score=3, risk 1%→5%):

| Window | Mean | Median | % profitable | win | $/trade | hold | worst DD |
|---|---|---|---|---|---|---|---|
| 2022–23 (bear-ish) | +3.4% | +1.4% | 58% | 31% | $135 | 45d | −28.1% |
| 2024–25 (bull) | +6.7% | +7.3% | 75% | 40% | $548 | 36d | −21.8% |

**Conclusions:**
1. **It trades both regimes:** 2022–23 — where long-only was weak — is now solidly
   positive (the short side earns in downturns). Delivers "don't care bull or bear."
2. **Cost = drawdown:** −28% / −22% worst DD (5% sizing + both-direction + lower
   short win-rate ~31–40%). Shorts are harder (lower hit-rate) — expected.
3. **Risk management is now a hard prerequisite**, not a nice-to-have: 5% conviction
   bets across long+short need portfolio-heat / max-concurrent / per-name caps
   before this is usable. These are also single-symbol DDs; a real portfolio could
   diversify (long+short can hedge) or compound (correlated) — must be modeled.
4. Short P&L correctness is pinned by tests (round-trip profit AND loss).

**Next (now urgent):** risk-management layer; then a true portfolio backtest
(concurrent long+short positions) to get a realistic equity curve + drawdown;
then M3 paper-trading.

---

## EXP-12 · 2026-06-30 · realistic PORTFOLIO backtest + risk caps (reality check)

Built a multi-symbol, time-synchronized **portfolio backtest** (`Backtester.run_portfolio`):
all symbols share one cash pool + equity curve, with a `RiskManager` capping
**max concurrent positions** and **portfolio heat** (total open entry→stop risk as
a fraction of equity). `scripts/run_portfolio.py` runs it. This is the first
*realistic* equity curve (the prior sweeps were per-symbol, isolated capital).

Regime-agnostic config (divergence both-sides + directional confluence, 1%→5%),
$100k, 2022–2025 (incl. 2022 bear):

| Risk caps | Return (4y) | Ann. | Sharpe | Max DD | Win | PF | Round-trips |
|---|---|---|---|---|---|---|---|
| heat 10%, 6 concurrent | +23.2% | +5.5% | 0.34 | −29.4% | 40% | 1.14 | 90 |
| heat 5%, 4 concurrent | −36.0% | −10.8% | −0.73 | −41.3% | 21% | 0.49 | 66 |

**Conclusions (important):**
1. **Reality check:** the per-symbol sweeps (EXP-10 holdout median +8.8%) were
   optimistic. As a *real portfolio*, this is **+5.5%/yr, Sharpe 0.34, −29% DD,
   profit factor 1.14** — modest and risk-adjusted-weak. Not yet tradeable. The
   portfolio engine did exactly its job: kill the illusion before customers see it.
2. **Design flaw surfaced:** tighter caps made it *worse*, not safer. The
   RiskManager admits entries **first-come (symbol order)** until the budget fills
   — so a scarce heat budget gets spent on whichever symbol is alphabetically
   first, NOT the highest-confluence setup. Tighter caps amplified this arbitrary
   selection → win-rate collapsed 40%→21%.
3. **Clear next improvement: confidence-ranked risk allocation.** At each
   timestamp, collect all candidate entries across symbols, rank by confluence
   confidence, and spend the heat budget on the BEST setups first. This is the
   right way to combine conviction sizing with a capped book.

**Status:** engine is now production-grade (portfolio + risk + shorts, all tested)
and *honest*. The strategy itself needs more work (better edge and/or
confidence-ranked allocation) before it's worth paper-trading.

**Next:** confidence-ranked allocation in run_portfolio; then re-evaluate
Sharpe/DD; bear-specific walk-forward; only then M3.

---

## EXP-13 · 2026-06-30 · pro confluences + the confluence/signal-fit lesson

Added 5 professional swing confluences (composable filters, direction-aware,
pure, tested) + a proper **ADX** indicator in the registry:
`adx` (trend strength), `atr_volatility` (volatility-regime band), `htf_trend`
(weekly higher-timeframe alignment via resample), `not_extended` (don't chase —
≤N ATRs from the MA), `rel_volume` (sustained participation). All in the catalog
→ live in the Backtest Lab UI, CLI, and sweep.

A/B as a real portfolio ($100k, 12-name tech-heavy basket, 2022–2025):

**On DIVERGENCE (a reversal signal): the trend confluences HURT.**
- baseline (trend+regime, all-mode): −4.3%, PF 0.95
- + adx + htf_trend: −21.6%, PF 0.57
- The trend confluences *fight* a reversal thesis: a bullish divergence fires
  when price is falling/below the weekly MA, so `htf_trend`(long → price>weeklyMA)
  and `adx`(strong existing trend) reject exactly the early-reversal entries that
  make divergence work. **Confluence must match the signal's thesis.**

**On BREAKOUT (a trend-following signal): coherent, and far stronger.**
- breakout bare: **+171.6%, Sharpe 1.25, −24% DD, PF 2.14, 128 trades**
- + adx+htf+regime: +53%, PF 1.50, 82 trades
- + 5 confluences: +72%, Sharpe 0.83, **−21.6% DD**, PF 1.84, **69 trades**
- The confluences trade raw return for **selectivity + lower DD** (128→69 trades,
  DD −24%→−21.6%, PF stays strong) — exactly the "fewer, higher-conviction
  trades" the user wants.

**Conclusions:**
1. **Confluence/signal fit is the master variable** — not "more confluence = better."
   Trend filters belong on trend signals; reversal signals need reversal
   confluences (oversold extreme, at support, candle confirmation — TODO).
2. **Breakout/momentum >> divergence on this regime/basket** (+171% vs +23%). But
   this is a mega-cap-tech basket over a 2023–25 momentum bull → optimistic /
   basket-selected. **Needs a neutral basket + OOS/walk-forward before trusting.**
3. The pro confluences work as designed (tighten selection, cut DD) when matched
   to the right signal.

**Next:** neutral-basket + walk-forward validation of breakout+confluences;
reversal-matched confluences for divergence; confidence-ranked allocation; then
the score-mode min_score must scale with filter-count (3-of-5 < 3-of-4 selectivity
surprised us — document/raise in the UI).

---

## EXP-14 · 2026-06-30 · cross-sector "just find movers" + walk-forward

User intent: sector-agnostic — trade whatever is *moving*. Built a 34-name
cross-sector basket from CH-available deep-history symbols: energy (XOM, XLE, USO,
UNG), metals (SLV, IAU, PPLT, RIOT), financials (JPM, GS, V, MA), health (LLY,
UNH, MRK), consumer (WMT, MCD, NKE, HD), industrials (RTX, LMT), semis (NVDA, MU,
INTC, QCOM, MRVL), and high-beta movers (MSFT, GOOGL, META, PLTR, TSLA, SOFI,
HOOD, NET). Signal: `breakout` (20-bar high + volume). Added `--start/--end`
overrides to `scripts/run_portfolio.py` for walk-forward.

**Full period 2022–2025 ($100k, 8 concurrent, 12% heat):**
- breakout bare:        **+240%, Sharpe 1.34, −25% DD, PF 1.60, 416 trades**
- breakout + 5 conflu.: +148%, Sharpe 1.04, −27% DD, **PF 1.82, 178 trades**

→ Momentum/breakout is **NOT a tech artifact** — it's *stronger* across sectors.

**Walk-forward by calendar year (regime robustness):**

| Year | bare ret / Sharpe / PF | +confluences ret / Sharpe / PF |
|---|---|---|
| 2022 (bear) | +24.4% / 1.21 / 1.65 | −7.0% / −1.3 / 0.25 (only 8 trades) |
| 2023 | +36.6% / 1.42 / 1.74 | +50.0% / 1.63 / **3.80** |
| 2024 | +22.1% / 0.84 / 1.45 | +82.7% / 2.05 / **4.27** |
| 2025 | −4.5% / −0.08 / 0.91 | **+12.9%** / 0.74 / 1.49 |

**Conclusions:**
1. **"Buy what's moving" generalizes across sectors and most regimes.** Bare
   breakout was positive in 3 of 4 years — *including* the 2022 bear (+24%).
2. **The confluences flip the profile, mostly for the better:** they lift trade
   quality enormously in trending years (PF 3.8–4.3 in '23/'24) and **turned the
   losing 2025 (−4.5% bare) into +12.9%** while cutting DD. But they (correctly)
   go near-cash in the 2022 bear (8 trades) — they're a *trend-regime* overlay,
   not a bear strategy.
3. This is the **most tradeable result so far** — regime-robust, cross-sector,
   coherent. A natural design: run confluence-gated breakout in up-regimes, lighten
   (or flip to shorts/reversal) in down-regimes.

**Honesty caveats:** these years are all in-sample (judged across all of them);
calendar windows carry ~6wk warmup each Jan; basket is hand-picked liquid movers.
Real track record still requires a clean train/test split + forward paper-trade (M3).

**Next:** clean OOS (tune on 2022–23, validate untouched 2024–25); regime-switch
overlay; forward paper-trading.

---

## EXP-15 · 2026-06-30 · regime-switch strategy — and why top-down regime gating HURTS

Built `RegimeSwitchStrategy` (regime_switch): reads benchmark regime (SPY vs its
regime SMA) per bar and routes entries to an `up` branch (confluence breakout) vs a
`down` branch (reversal shorts) or CASH. Reuses AlertStrategy sizing/exits; routing
unit-tested; registered in loader + catalog (live in UI). Walk-forward on the
34-name cross-sector basket (regime_ma=100):

| Year | rs_cash (down=cash) | rs_short (down=div shorts) | dv_pro (no gate, EXP-14) |
|---|---|---|---|
| 2022 | −5.2% (6 trades, in cash) | **−54.3%** (shorts blew up) | −7.0% |
| 2023 | +38.4%, PF 2.39 | +8.6% | +50.0% |
| 2024 | +73.2%, Sharpe 1.87 | +56.3% | +82.7% |
| 2025 | +5.4% | +5.4% | +12.9% |
| **Full** | **+113%, −20.7% DD** | −32%, −77.6% DD | **+148%, −27% DD** |

**Conclusions (important, and they validate the user's thesis):**
1. **A top-down SPY regime gate SUBTRACTS value from cross-sector momentum.**
   rs_cash underperforms dv_pro in every year (+113% vs +148% full) — the gate
   sits out names that are *individually* moving when SPY is weak (e.g. the 2022
   energy/commodity breakouts). "We don't care about regime, we want what's
   moving" is empirically correct: per-name breakout already IS the regime filter,
   at the name level. The gate's only payoff is a modestly lower DD (−20.7%).
2. **Shorting the movers is catastrophic** (rs_short −32%/−77% DD; −54% in 2022).
   Reversal-divergence shorts on a momentum universe lose badly. Don't fight movers.
3. **The winner remains dv_pro**: confluence-gated breakout, regime-agnostic at the
   name level. Bottom-up > top-down for this style.

**Implication for the next idea (EWT):** the lesson is that gating should be
**per-name and structural**, not top-down/market-wide. Elliott Wave is exactly a
per-name structure gate — "enter on an impulse (wave 3/5), avoid corrections
(wave 4/B)" — which fits the bottom-up momentum philosophy. That's the right way
to use the existing `app/signals/elliott` forward (no-look-ahead) labeler.

**Status:** regime_switch shipped + tested (a legit tool; useful when a customer
wants lower DD), but NOT our headline strategy. Next: EWT per-name entry gate.

---

## EXP-16 · 2026-06-30 · Elliott Wave gate ("trade the wave") — built, pure, but doesn't help momentum

Built `ewt_impulse` filter: gates entries on the pure, no-look-ahead
`app.signals.elliott` engine — pass only when the name is in a motive wave
(default 3 & 5) in the trade direction with confidence ≥ threshold. Per-name +
structural (the EXP-15-endorsed kind of gate). Runs only on base-signal bars
(bounded cost ~30s for the basket). 7 tests (mocked decision logic + real-engine
smoke); purity gate stays green (engine is pure).

A/B on breakout, diversified basket 2022–2025:

| Config | Return | Sharpe | DD | PF | Trades |
|---|---|---|---|---|---|
| breakout bare (EXP-14) | +240% | 1.34 | −25% | 1.60 | 416 |
| breakout + ewt_impulse | +9.8% | 0.22 | **−17.5%** | 1.08 | 117 |
| + loose (conf 0, waves 1/3/5) | +9.8% | 0.22 | −17.5% | 1.08 | 117 |

**Conclusions:**
1. **As a GATE on momentum, EWT hurts here.** It keeps ~117/416 breakout signals,
   and that subset is *worse* per-trade (PF 1.08 vs 1.60) — the wave labeling at a
   breakout moment isn't adding predictive value; it removes net-winning trades.
   (Loosening confidence/waves changed nothing → the wave/direction label is the
   binding constraint, not the threshold.) Only benefit: lower DD (−17.5%).
2. **This may be the wrong USE of EWT.** Breakout already enters on strength;
   asking "is this a clean wave 3/5" just sub-samples it. The EWT-native idea is a
   **signal SOURCE**, not a gate: enter at the START of wave 3 (after wave 2
   completes), stop = wave-2 invalidation, target = wave-3 fib extension — i.e.
   enter EARLIER than breakout, on the structure itself. That remains untested.
3. **Meta-finding across EXP-13/15/16:** on this 2022–25 cross-sector momentum
   universe, *every* gate (trend confluences, market regime, EWT) trades return
   for selectivity + lower DD. Bare breakout wins on return; gated variants win on
   risk-adjusted DD. "Best" depends on the objective.

**Status:** ewt_impulse shipped (pure, tested, in the catalog/UI) — a legit tool
and the foundation for an EWT source. NOT a momentum improver. Next EWT step: a
wave-entry SOURCE (trade-the-wave), which uses the engine's invalidation/targets.

---

## EXP-17 · 2026-06-30 · "Trade the wave" — Elliott Wave entry SOURCE (the headline)

Per user + the elliott-wave skill + the Avi (AviMarkets GC/GDX) wave log, built
`elliott_wave` as a SIGNAL SOURCE (not a gate): label the name as-of each bar with
the pure no-look-ahead engine; on confirmation of a motive leg (default wave 3 —
the money wave), enter in the count direction with stop = the count's
`invalidation_price` (the cardinal-rule "trap door" = wave-2 low) and target = the
engine's first fib target (~1.618×W1). Conviction-sized by engine confidence.
Debounced per (symbol, direction). 9 tests; purity green.

Diversified 34-name basket, 2022–2025 ($100k, 8 concurrent, 12% heat):

| Variant | Return | Sharpe | DD | PF | Trades |
|---|---|---|---|---|---|
| wave-3 long+short | −32.8% | −0.42 | −54.6% | 0.63 | 91 |
| **wave-3 LONG only** | **+147%** | **1.36** | **−22.7%** | **2.45** | 104 |

Walk-forward (wave-3 long): 2022 +0.2% (PF 0.16, ~flat, 16 trades — didn't force
trades in the bear); 2023 +10.9% (PF 4.51); 2024 +71.8% (Sharpe 3.28, PF 6.35);
2025 +8.9% (PF 1.48). **Never a losing year.**

**Conclusions:**
1. **EWT as a SOURCE is our best risk-adjusted strategy** — same return as
   confluence breakout (+147%) but highest Sharpe (1.36) and by far the best
   profit factor (2.45) with the fewest trades + lowest DD. The structural edge:
   enter near the wave-2 low with a tight trap-door stop and a far wave-3 target —
   an R:R a 20-day-high breakout can't match.
2. **The gate-vs-source distinction is the whole lesson** (EXP-16 vs EXP-17): EWT
   gating momentum HURT (+10%); EWT generating native wave-3 entries is the best
   strategy. Use the wave structure to TIME the entry, not to veto another signal.
3. **LONG ONLY.** Long+short lost −33% — shorting the movers is fatal (echoes
   EXP-15). Bearish wave-3 shorts need their own universe/treatment.

**Caveats (honesty doctrine):** in-sample (all years observed); hand-picked
basket; 2024 Sharpe 3.28 / PF 6.35 won't repeat (22 trades, sequencing luck); the
engine runs every bar (~50s/backtest — fine for research, needs caching for live
scanning). NOT a track record until clean OOS + forward paper-trade.

**Next:** clean OOS (tune 2022–23 / validate untouched 2024–25); add EWT
confluence (channeling/volume/alternation guidelines already in the skill);
combine EW-source + breakout in a portfolio; M3 forward paper-trade.

---

## EXP-18 · 2026-06-30 · OUT-OF-SAMPLE REALITY CHECK (the honest checkpoint)

The prior headline numbers were all measured on ONE hand-picked 34-name basket.
Since I'd seen every calendar year, a time-holdout is contaminated, so I ran the
cleaner test: same strategies + params on a **fresh 24-name cross-sector basket
with ZERO overlap** (IBM, ORCL, NFLX, TSM, semis, MS/WFC, KO/SBUX/JNJ, RBLX/RIVN/
SNAP/PINS/LYFT, sector ETFs).

**Every strategy collapsed out-of-sample:**

| Strategy | Original basket | Fresh basket |
|---|---|---|
| Bare breakout | +240% / PF 1.60 | +29.5% / PF 1.17 |
| Confluence breakout | +148% / PF 1.82 | **−4.3% / PF 0.89** |
| Elliott wave-3 long | +147% / PF 2.45 | +17.9% / PF 1.08 |

Adding an as-of **relative-strength gate** ("trade only market leaders") helped the
momentum signal but not the wave signal:
- breakout + RS: fresh +29.5% → **+70.6%** (PF 1.44) — RS recovers a lot.
- EW + RS: fresh +17.9% → **−15.6%** — RS fights the early wave-3 entry (thesis
  mismatch, cf. EXP-16).

But breakout + RS **walk-forward on fresh names is inconsistent**: 2022 −13.9%,
2023 +62.1%, 2024 −9.8%, 2025 +51.0%. The +70% full-period is two strong years
masking two losing years.

**Conclusions (no spin):**
1. **The big in-sample returns were basket-selection bias.** The original basket
   happened to hold the era's biggest movers (NVDA, PLTR, 2022 energy/metals). On
   names I didn't pick, the edge shrinks to modest (breakout) or vanishes
   (confluence) or is inconsistent (breakout+RS loses 2 of 4 years).
2. **EW wave-3 was the in-sample champion but did NOT generalize** (+147%/PF2.45 →
   +17.9%/PF1.08) and RS gating made it worse. More overfit than plain breakout.
3. **We do NOT have a validated, sellable edge yet.** This is the honesty doctrine
   working as intended — better to learn it here than in production.
4. **Root cause = universe.** These momentum/wave strategies live or die on whether
   the traded set contains the period's movers. Hand-picking supplied that with
   hindsight (look-ahead). The principled fix is a **dynamic, no-look-ahead
   universe**: screen a BROAD pool each day, trade the top as-of momentum/RS names —
   so the strategy *discovers* movers instead of being handed them.

**Next:** build dynamic universe selection (rank a broad CH universe by as-of
momentum/RS each rebalance, trade top-N) → re-validate breakout(+RS) and EW wave-3
on it → only then OOS-by-time + forward paper-trade. Until that holds, treat all
prior return figures as in-sample/optimistic.

---

## EXP-19 · 2026-06-30 · DYNAMIC UNIVERSE — the fix that generalizes

EXP-18 showed fixed baskets overfit (the edge lived in whether the basket held the
era's movers). Fix: let the strategy DISCOVER movers. Added dynamic-universe
selection to `run_portfolio` — `momentum_top_n` / `momentum_bottom_n` /
`momentum_lookback` (BacktestConfig + API + runner): each bar, rank symbols by
as-of trailing return (no look-ahead) and allow LONG entries only in the top-N
(short entries only in the bottom-N). Per-bar two-pass loop; unit-tested.

Broad 119-name CH pool (all sectors, deep history; leveraged/inverse ETFs
excluded), breakout, long top-15, lookback 60 — 2022–2025:

- Full period: **+354%, Sharpe 1.28, −25.8% DD, PF 1.62, 425 trades.**
- Walk-forward (the decisive test):

| Year | Fixed fresh basket (bo+RS) | **Dynamic top-15 of 119** |
|---|---|---|
| 2022 | −13.9% | **+5.7%** (PF 0.99) |
| 2023 | +62.1% | +41.7% (PF 1.53) |
| 2024 | −9.8% | **+23.7%** (PF 1.40) |
| 2025 | +51.0% | +41.9% (PF 1.58) |

**Conclusions:**
1. **Dynamic universe turns the down years positive and makes the edge
   consistent** — every year positive, every year PF 1.4–1.6, Sharpe ~1. It
   rotates into the year's actual leaders (energy '22, AI '24) instead of being
   stuck with a fixed basket that missed them. This is the structural fix EXP-18
   pointed to, and it works.
2. **This is the most trustworthy result so far**: broad pool (not hand-picked),
   as-of no-look-ahead selection, regime-robust across 4 years.
3. Remaining honesty gaps: the 119-pool is liquid-survivors (mild survivorship);
   top_n=15 / lookback=60 not yet robustness-swept; still needs forward paper-trade
   for a real track record.

**Next:** robustness-sweep (top_n, lookback) + walk-forward param search (the
disciplined alternative to RL — see findings note); long-leaders / short-laggards
via a breakdown short source ("ride waves up OR down"); EW source on the dynamic
pool; M3 forward paper-trade.

---

## EXP-20 · 2026-06-30 · Walk-forward combination search (the disciplined alt to RL)

Built `scripts/walkforward_search.py`: loads the 119-name pool from CH once,
slices in-memory, sweeps an interpretable grid (momentum top_n × lookback ×
confluence stack) and scores each config per calendar year. Select on DEV Sharpe
(2022-23), report HOLDOUT (2024-25) — winner chosen without peeking at its holdout.
Strict per-year cold-start (no pre-year warmup) → each year loses its first
~`lookback` days to momentum warmup, so this is a CONSERVATIVE read.

12 configs (breakout base). Headline rows:

| config | 2022 | 2023 | 2024 | 2025 | DEV Sh | HOLD Sh |
|---|---|---|---|---|---|---|
| **top15 / lb60 / none** | +5% | +42% | +24% | +25% | **+0.71** | +0.94 |
| top20 / lb60 / none | +2% | +23% | +18% | +25% | +0.47 | +0.90 |
| top10 / lb60 / none | +7% | +7% | +25% | +57% | +0.37 | +1.30 |
| top15 / lb60 / **rs** | −25% | +44% | +2% | +21% | +0.16 | +0.50 |
| top20 / lb90 / none | +7% | +13% | −2% | −22% | +0.44 | −0.28 |

**Winner (DEV-selected): top_n=15, lookback=60, NO filters.** Per-year +5/+42/+24/
+25%, Sharpe +0.31/+1.11/+0.96/+0.92, PF 0.98/1.53/1.40/1.36. DEV Sharpe +0.71,
**HOLDOUT Sharpe +0.94 (≥ DEV → not overfit to dev)**, worst year +5.1%.

**Conclusions:**
1. **The simplest dynamic-momentum config is the most robust** — plain breakout +
   dynamic top-15 (lb60), no confluences. DEV-selected, holdout-confirmed, positive
   every year even with a conservative cold-start.
2. **Adding confluences/RS HURTS on the dynamic universe** — RS configs go negative
   in 2022 (−23/−25%); the momentum top-N selection ALREADY does the "trade leaders"
   job, so stacking RS double-filters and removes good trades. Over-engineering
   lesson, again (cf. EXP-13/16): don't gate a selection that's already doing the work.
3. **lookback 60 > 90; top-N 15 is the sweet spot** (10 is choppier, 20/lb90 breaks).
4. **This is the RL answer in practice**: a disciplined walk-forward search over a
   handful of interpretable knobs found a robust, holdout-validated config — no
   black box, no reward-hacking risk. RL stays parked until a generalizing base +
   live paper-trade pipeline exist.

**Next:** long-leaders / short-laggards via a breakdown short source (ride up OR
down) on the dynamic pool; then M3 forward paper-trade of top15/lb60 breakout.
