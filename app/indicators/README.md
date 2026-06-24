# indicators/

Modular technical-analysis library. Each indicator is a small class that
subclasses [`Indicator`](base.py) and implements `compute(close, high?, low?)`
returning a `pd.Series` aligned to the input index.

## Current contents

| Family | File | Class | Notes |
|---|---|---|---|
| Base | [base.py](base.py) | `Indicator` (ABC) | All indicators subclass this |
| MA | [sma.py](sma.py) | `SMA` | Simple moving average |
| MA | [ema.py](ema.py) | `EMA` | Exponential MA, pandas `ewm(adjust=False)` |
| MA | [wma.py](wma.py) | `WMA` | Linear-weight MA |
| Momentum | [rsi.py](rsi.py) | `RSI` | Wilder's RSI |
| Momentum | [macd.py](macd.py) | `MACD` | Plus `compute_signal` / `compute_histogram` / `compute_full` |
| Momentum | [tsi.py](tsi.py) | `TSI` | True Strength Index |
| Momentum | [stochastic.py](stochastic.py) | `StochasticOscillator` | %K + %D via `compute_full` |
| Volatility | [atr.py](atr.py) | `ATR` | Wilder's true-range smoothing |
| Volatility | [bollinger.py](bollinger.py) | `BollingerBands` | Upper/middle/lower/bandwidth/%B via `compute_full` |
| Registry | [registry.py](registry.py) | — | `get_indicator(name, **params)`, `list_indicators()` |

Consumers reach indicators **by name** via the registry, never by
importing classes directly:

- **Strategies** call `ctx.indicator("sma", period=20)` inside `Context`.
- **The live monitor** wires via `INDICATOR_MAP` in
  [monitor_service.py](../services/live/monitor_service.py).
- **The dashboard + MCP agents** call the `IndicatorReader` and
  HTTP/MCP indicator endpoints — see
  [docs/indicator_exposure_design.md](../../docs/indicator_exposure_design.md).

## Multi-output indicators

MACD, Bollinger, and Stochastic each produce more than one series.
Per the convention:

- `compute(...)` returns the canonical single-output series:
  - MACD → the MACD line
  - Bollinger → the middle band (SMA)
  - Stochastic → smoothed %K
- `compute_full(...)` returns a `dict[str, pd.Series]` of all
  components. The `IndicatorReader` decomposes this into multiple
  named `IndicatorSeries` entries in API responses (e.g.
  `bollinger_upper`, `bollinger_middle`, `bollinger_lower`).

## Contract

```python
class MyIndicator(Indicator):
    def __init__(self, period: int = 14):
        super().__init__()
        self.name = "my_indicator"
        self.period = period

    def compute(
        self,
        close: pd.Series,
        high: pd.Series | None = None,
        low: pd.Series | None = None,
    ) -> pd.Series:
        ...
```

Rules:
- Input index passes through unchanged. The output series is reindex-aligned
  to `close.index`.
- Warmup rows (where the indicator isn't defined yet) are `NaN`, not `0` —
  divergence detectors filter on `notna()`.
- No I/O. Indicators are pure: same inputs → same outputs. Caching and
  persistence live one layer up.

## Future library expansion

The registry already covers moving averages, momentum, volatility, and pivots.
Possible additions should land only when a consumer requires them and must
include registry exposure and colocated unit tests.

Candidate adds, grouped by family:

- **Momentum:** Stochastic RSI, Williams %R, CCI,
  ROC, Momentum, Ultimate Oscillator, Awesome Oscillator
- **Trend:** ADX/DI+/DI-, Aroon, SuperTrend, Parabolic SAR, Ichimoku
  (Tenkan/Kijun/Senkou/Chikou)
- **Moving averages:** HMA, DEMA, TEMA, KAMA, VWMA, ALMA
- **Volatility:** Keltner Channels, Donchian
  Channels, Chaikin Volatility, NATR
- **Volume:** OBV, A/D Line, Chaikin Money Flow, MFI, VWAP (intraday),
  Volume Profile, Force Index
- **Cycles / statistics:** Hurst exponent, Z-score, linear-regression
  slope, Hilbert Transform dominant cycle period

Each accepted addition gets its own file + class + tests in [`tests/`](tests/)
and an entry in the table above.

Pattern detectors that consume these indicators live in
[`app/signals/`](../signals/) — that's a separate layer with its own
backlog.
