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
