# No-Look-Ahead / Anti-Cheating Audit

Scope: every strategy backtested and paper-traded on the sim engine
(`app/services/sim/*`). Question: does any strategy use data it could not have had
at decision time, or otherwise cheat?

## Verdict
**No look-ahead found in the trading logic.** Decisions use only data up to and
including the current (completed) bar; fills happen at the *next* bar's open;
appending future bars never changes a past trade (proven by truncation invariance).
Known *methodology biases* (not look-ahead) are listed at the end — surfaced, not
hidden.

## What was audited + how it's guaranteed

1. **Signal generation is causal.** Sources/filters read only `ctx.history`
   (bars ≤ current) via `on_bar`; the engine advances one bar at a time, so a
   source physically cannot see future bars. Static sweep of `app/indicators` +
   `app/services/sim` + `app/signals/elliott` found no future shifts
   (`shift(-n)`), centered rolling windows (`center=True`), or forward indexing.

2. **Indicators are causal.** SMA/EMA/RSI/MACD/ATR/ADX/Bollinger use trailing
   windows only. Golden tests pin warmup NaNs and values.

3. **Pivots confirm in the future, and are only used once confirmed.** Pivot
   detection needs `k` bars on each side, so the latest usable pivot lags by `k`.
   Sources act only on confirmed pivots. Tests:
   `app/indicators/tests/test_pivots_unit.py`,
   `app/signals/elliott/tests/test_elliott_no_lookahead.py` (11 tests).

4. **Fills are next-open, never current-close.** `fill_price(action, next_bar)`
   returns `next_bar.open`; with no next bar it returns NaN → **no trade**. So the
   decision is made on a completed bar and executed at the next bar's open — you
   can never trade on a bar that hasn't opened. (`app/services/sim/fees.py`.)

5. **Dynamic-universe ranking is as-of.** Each bar ranks symbols by trailing
   return computed from closes ≤ current bar; eligibility gates entries filled next
   open. No future prices enter the rank.

6. **Benchmark/regime is as-of.** `MarketContext.value_asof` / `above_ma_asof` /
   `return_over_asof` read only history ≤ the query timestamp.

7. **Paper / simulated trading is forward-only.** The paper record's forward slice
   is strictly *after* `go_live` — data that postdates the strategy commitment.
   Past-date "replays" are explicitly labeled backtest, not the live record.

8. **Tuning never touches the evaluation data.** `scripts/improve_strategy.py`
   tunes on TRAIN (2022-23) and reveals HOLDOUT (2024-25) once, selecting strictly
   on train (EXP-22 — it caught an overfit and kept the baseline).

## The proof: truncation invariance
`app/services/sim/tests/test_no_lookahead.py` runs the full pipeline (source +
filters + dynamic ranking + fills) on a series, then on the series truncated of its
last K bars, and asserts the truncated run's trades are an **exact prefix** of the
full run's. If any component used future data, the early trades would differ. Passes
for breakout (+ dynamic top-N ranking) and ma_cross, plus a "last bar can't fill"
check. Pivot-based sources are covered by (3).

## Known methodology BIASES (not look-ahead — surfaced per the honesty doctrine)
These do not let a strategy see the future, but they do flatter historical results;
the **forward paper record is the only bias-free measure**:

- **Survivorship.** The tradable pool = symbols that exist in ClickHouse *today*
  with history back to 2022; delisted/failed names are absent → upward bias.
  Mitigations: a broad cross-sector pool; forward paper is clean of this.
- **In-sample development.** Strategy parameters were chosen with knowledge of the
  full history, so all historical/backtest returns are in-sample and optimistic.
  Only the post-go-live paper record is genuinely out-of-sample.
- **Point-in-time universe membership** is not reconstructed (we don't rebuild
  historical index constituents); the pool is fixed.
- **Split-adjusted reads** incorporate splits announced after a given backtest bar.
  Splits are pre-announced and non-directional, so the effect is negligible, but
  noted for completeness.
