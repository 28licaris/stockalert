# screener/

Universe scanner. Takes a `ScreenerSpec` (declarative filters
+ rank) and returns ranked candidates. The "fast filter" stage of
the canonical swing-trade pipeline:

```
universe (1000s)  →  Screener.scan  →  10-30 candidates  →  Strategy
                     (fast filter)                          (slow analysis)
```

Single source of truth for indicator math reused: every rule that
needs an indicator (SMA, EMA, RSI, ATR, Bollinger) calls into
`app.indicators.registry.get_indicator(name, **params)`. The
screener's "SMA(20)" is byte-identical to the dashboard's and the
backtester's.

## Files

| File | Owns |
|---|---|
| [schemas.py](schemas.py) | `ScreenerSpec`, `ScreenerRule`, `Candidate`, `CandidateMetric`, `ScreenerResult` |
| [rules.py](rules.py) | One evaluator function per `RuleKind`. New rule = add an entry to `_RULE_EVALUATORS` + a `RuleKind` literal + a test |
| [screener.py](screener.py) | `Screener.scan(spec)` — pure function: spec → result |

## Supported rule kinds (TA-4.3)

| Kind | Params | Meaning |
|---|---|---|
| `close_above_sma` / `close_below_sma` | `{period}` | Trend filter via SMA |
| `close_above_ema` / `close_below_ema` | `{period}` | Trend filter via EMA |
| `rsi_above` / `rsi_below` | `{period, threshold}` | Momentum threshold |
| `close_at_lower_band` / `close_at_upper_band` | `{period, std_multiplier}` | Bollinger envelope touch |
| `atr_pct_above` / `atr_pct_below` | `{period, threshold}` | Volatility filter (ATR/close fraction) |
| `price_above` / `price_below` | `{value}` | Absolute price threshold |
| `volume_above` | `{value}` | Volume threshold |

Rules combine via **logical AND** — a symbol must pass ALL rules
to qualify.

## Rank options

- `volume` — most-traded first (default)
- `atr_pct` — most volatile first
- `rsi` — lowest RSI first (most oversold)
- `rsi_desc` — highest RSI first (most overbought)
- `none` — preserve universe order

## Surfaces

- **HTTP** — `POST /api/screener/scan` (route adds at TA-4.3 commit).
- **MCP** — `scan_universe` tool (agents call this).
- **Direct Python** — `Screener.from_settings().scan(spec)` from CLI scripts.

All three return the same `ScreenerResult` shape.

## Examples

**Trend-following daily setups** (close above 200-SMA, oversold-ish RSI):

```yaml
universe: [AAPL, MSFT, NVDA, GOOGL, META, AMZN, TSLA]
interval: "1d"
lookback_bars: 250
rules:
  - kind: close_above_sma
    params: {period: 200}
  - kind: rsi_below
    params: {period: 14, threshold: 50}
rank_by: rsi   # most oversold first within the passers
limit: 5
```

**Volatility breakout candidates** (ATR > 2% of price, near upper Bollinger):

```yaml
universe: [...]
interval: "1d"
lookback_bars: 100
rules:
  - kind: atr_pct_above
    params: {period: 14, threshold: 0.02}
  - kind: close_at_upper_band
    params: {period: 20, std_multiplier: 2.0}
rank_by: atr_pct
limit: 10
```

## How to add a new rule

1. Add the `kind` string to `RuleKind` in [schemas.py](schemas.py).
2. Add an evaluator function to [rules.py](rules.py) following the
   existing pattern (param validation → indicator compute →
   `RuleEval(passed, metric_name, metric_value)`).
3. Register it in `_RULE_EVALUATORS`.
4. Add a test in [`tests/test_screener.py`](tests/test_screener.py) exercising the pass and
   fail paths.

Bad rule kinds raise `ValueError` at scan time, not at spec validation —
this is intentional so an agent debugging a typo gets the supported
list in the error message rather than a cryptic Pydantic error.

## What's NOT in TA-4.3

- Cross-rule logic beyond AND (no OR, no NOT, no nested expressions).
  If you need them, ship them in TA-4.4 or build separate specs.
- Composite scoring (combining multiple rank metrics).
- "All of bronze" universe expansion — too big without partitioning.
  Use explicit lists or watchlist names.
- Caching across consecutive scans — every call re-fetches bars.
  Cheap enough at TA-4.3 scale (~hundreds of symbols × ms-per-symbol).
