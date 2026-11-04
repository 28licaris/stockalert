# components/charts/

Candlestick charting and the indicator overlay system.

## Layout

| File | Owns |
|---|---|
| [OhlcvChart.tsx](OhlcvChart.tsx) | The price chart — candles, overlays, oscillator panes |
| [ChartToolbar.tsx](ChartToolbar.tsx) | Interval, source and indicator selection |
| [indicatorCatalog.ts](indicatorCatalog.ts) | **The catalog both the toolbar and the renderer read** |
| [EquityTradeChart.tsx](EquityTradeChart.tsx) | Equity curve + trade markers for backtest/paper results |
| [movingAverage.ts](movingAverage.ts) | Client-side MA helper |

## Registry parity — the load-bearing rule

`indicatorCatalog.ts` `id` values **match the backend registry**
(`app/indicators/registry.py`) so a UI selection maps 1:1 onto an
`/api/v1/indicators/chart-data` spec. Add an indicator to the backend registry
first, then mirror its id here — a mismatch is a silent empty series.

Two render targets:

- **overlay** — drawn on the price pane (moving averages, bands)
- **oscillator** — drawn in its **own** pane below price (RSI, MACD, …),
  because 0-100 and ± ranges cannot share the price axis

Multi-output indicators decompose **server-side** into component series
(`bollinger_upper`, `macd_signal`, `stochastic_k`); the component name's prefix
is its parent id (`parentIndicatorId`). The client never reassembles them.

## Cross-timeframe indicators

An indicator can be sourced from a different aggregation than the chart — a
200-day SMA on a 5-minute chart — via `source_agg` / `window_days` through the
backend `IndicatorReader`. The source picker is part of the toolbar. Design:
[`../../../../docs/trading/indicators.md`](../../../../docs/trading/indicators.md).

## Conventions

- **Bars arrive as UTC instants**; display timezone is a per-user setting
  resolved in [`../../lib/timezone.ts`](../../lib/timezone.ts). One global
  setting drives both the chart axis and the Recent Bars table so they cannot
  drift apart.
- **Bar data comes from the gateway**, so the chart never picks a tier. The
  `source` selector (`auto` / `clickhouse` / `lake`) is a debugging affordance,
  not the normal path — see
  [`../../../../docs/architecture/README.md`](../../../../docs/architecture/README.md).
- **Overlay changes are perf-sensitive.** Adding an overlay must not refetch
  the price series.
