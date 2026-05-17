# indicators/

Modular technical-analysis library. Each indicator is a small class that
subclasses [`Indicator`](base.py) and implements `compute(close, high?, low?)`
returning a `pd.Series` aligned to the input index.

## Current contents

| File | Class | Used by |
|---|---|---|
| [base.py](base.py) | `Indicator` (ABC) | All indicators |
| [rsi.py](rsi.py) | `RSI` | `services/live/monitor_service`, [signals/divergence.py](../signals/divergence.py) |
| [macd.py](macd.py) | `MACD` | `services/live/monitor_service`, [signals/divergence.py](../signals/divergence.py) |
| [tsi.py](tsi.py) | `TSI` | `services/live/monitor_service`, [signals/divergence.py](../signals/divergence.py) |

The live monitor wires these via `INDICATOR_MAP` (see
[monitor_service.py](../services/live/monitor_service.py)). Adding an
indicator means: implement the class here, then add a `{name: Class}`
entry to `INDICATOR_MAP`. No further changes needed.

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

## TODO — full TA library buildout (not started)

Today's three indicators (RSI, MACD, TSI) are what divergence detection
needs. The plan is to expand this folder into a full TA toolkit so
agents and strategies can pick from a rich set without external deps.
Tracked in [BUILD_JOURNAL.md backlog](../../docs/BUILD_JOURNAL.md).

Candidate adds, grouped by family:

- **Momentum:** Stochastic (`%K`/`%D`), Stochastic RSI, Williams %R, CCI,
  ROC, Momentum, Ultimate Oscillator, Awesome Oscillator
- **Trend:** ADX/DI+/DI-, Aroon, SuperTrend, Parabolic SAR, Ichimoku
  (Tenkan/Kijun/Senkou/Chikou)
- **Moving averages:** SMA, EMA, WMA, HMA, DEMA, TEMA, KAMA, VWMA, ALMA
- **Volatility:** ATR, Bollinger Bands, Keltner Channels, Donchian
  Channels, Chaikin Volatility, NATR
- **Volume:** OBV, A/D Line, Chaikin Money Flow, MFI, VWAP (intraday),
  Volume Profile, Force Index
- **Cycles / statistics:** Hurst exponent, Z-score, linear-regression
  slope, Hilbert Transform dominant cycle period

Each gets its own file + class + unit tests + an entry in the table above.

Pattern detectors that consume these indicators live in
[`app/signals/`](../signals/) — that's a separate layer with its own
backlog.
