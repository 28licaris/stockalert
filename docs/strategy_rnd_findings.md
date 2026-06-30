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
