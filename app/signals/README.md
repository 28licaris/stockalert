# signals/

Pattern detectors. Each module here takes a price series plus one or
more indicator series (from [`app/indicators/`](../indicators/)) and
emits a **signal event** when a named pattern appears.

This is the layer between raw indicators and trading strategies:

```
price  ─►  indicators/   ─►  signals/   ─►  strategies/   ─►  orders
            (math)         (patterns)      (decisions)       (Phase 8+)
```

`indicators/` is stateless math (price → series). `signals/` is pattern
recognition (price + indicator → event). `strategies/` (future) composes
multiple signals into a trade decision. Keeping the layers separate
means each can be tested, swapped, and reused independently.

## Current contents

| File | Detectors | Consumed by |
|---|---|---|
| [divergence.py](divergence.py) | `detect_regular_bullish`, `detect_regular_bearish`, `detect_hidden_bullish`, `detect_hidden_bearish`, plus pivot helpers | [services/live/monitor_service.py](../services/live/monitor_service.py) |

The live monitor wires detectors via the `DETECTOR_MAP` dict in
`monitor_service.py`. Adding a detector = implement here, then add a
`{signal_type_name: function}` entry to that map.

## Detector contract

A detector is a function (not a class — these are pure transformations
on a window of data, not stateful objects):

```python
def detect_<pattern>(
    price: pd.Series,
    indicator: pd.Series,
    *,
    lookback: int,
    k: int,
    # ...detector-specific knobs
) -> dict | None:
    """Return a signal dict if the pattern fires on the latest bar,
    else None.

    Signal dict shape (canonical, what monitor_service persists):
        {
            "p1_ts": <timestamp of earlier pivot>,
            "p2_ts": <timestamp of later pivot>,
            "price": <price at p2>,
            "indicator_value": <indicator at p2>,
            # detector-specific fields are OK; persister picks what it needs
        }
    """
```

Rules:
- **Pure.** No I/O, no globals, no DB calls. Same inputs → same output.
- **Returns `None` when nothing fires.** Don't raise.
- **Operates on the latest bar.** Caller decides cadence; detector
  decides whether the window-ending-now contains a pattern.
- **Uses `lookback` to bound the window.** Don't scan unbounded history.
- **Reads tuning from arguments, not `settings`.** The caller
  (`monitor_service`) is responsible for pulling knobs from config and
  passing them in. Keeps detectors testable in isolation.

## TODO — signal detector library expansion (not started)

Today only divergence is implemented. The plan is to grow this folder
into a catalog of named detectors that strategies and agents can
compose. Tracked in
[BUILD_JOURNAL.md backlog](../../docs/BUILD_JOURNAL.md).

Candidate detectors, grouped by family:

- **Trend reversals:** Double top / double bottom, head-and-shoulders,
  triple top / triple bottom, V-bottoms
- **Continuations:** Bull/bear flag, pennant, ascending/descending
  triangle, cup-and-handle, channel breakouts
- **Moving-average crossovers:** Golden cross / death cross (SMA 50/200),
  EMA-stack alignment, MACD signal-line cross, MACD zero-line cross
- **Threshold crossings:** RSI overbought/oversold, RSI 50-line cross,
  Stochastic %K/%D cross, ADX trend-strength threshold
- **Volatility breakouts:** Bollinger Band squeeze release, Bollinger
  Band breakout, Keltner Channel breakout, ATR-expansion bar
- **Volume confirmations:** Volume climax, OBV divergence,
  volume-spread breakout, accumulation/distribution turn
- **Candlestick patterns:** Engulfing (bull/bear), hammer / hanging man,
  doji at S/R, morning star / evening star, three white soldiers /
  three black crows
- **Mean-reversion triggers:** Z-score reversion, Bollinger %B extreme,
  RSI-2 strategy trigger

Each gets its own file (or grouped file for closely-related detectors)
+ unit tests + an entry in the table above.
