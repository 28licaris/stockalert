# Indicator Exposure Design

How the platform computes and serves technical indicators to
consumers — the dashboard, MCP-driven LLM agents, the backtester,
and (future) the silver/gold ML pipeline.

Complements:
- [trading_subsystem_design.md](trading_subsystem_design.md) —
  how indicators are used by the strategy framework.
- [architecture_v2/](architecture_v2/README.md) — the v2 Iceberg lake
  (`equities.*` raw + adjusted). Pre-computed feature storage is
  post-v1 architecture; will land separately.
- [`app/indicators/README.md`](../app/indicators/README.md) —
  the per-indicator catalog and TA-library backlog.

## 1. The problem

We have OHLCV bars in two stores (Iceberg bronze + ClickHouse live
tier) and a library of indicator math. We need to put a single,
production-grade pattern in place for:

- The React dashboard rendering SMA / Bollinger overlays on a
  candlestick chart.
- An LLM agent (via MCP) asking "what's AAPL's RSI(14) right now?"
  before making a trade decision.
- The backtest harness computing indicators bar-by-bar inside the
  strategy `Context` (already solved by `Context.indicator(...)`).
- (Future) ML training pipelines scanning thousands of symbols × five
  years × twenty indicators in seconds.

"Compute it ad-hoc wherever we need it" works for one consumer.
Across four, it produces inconsistent values, slow dashboards, and
mysterious drift between agent and chart.

## 2. Three established patterns

### 2.1 Pattern A — Compute-on-read (lazy, in-service)

Request comes in → fetch bars from `BronzeReader` or `BarReader` →
compute via the `INDICATOR_REGISTRY` → return.

| Property | Value |
|---|---|
| Latency | ~10–100ms for ≤500-bar windows with ≤5 indicators |
| Cost | $0 (CPU only) |
| Storage | $0 |
| Freshness | Real-time (always current bar) |
| Reproducibility | Iceberg snapshot pinning gives bit-for-bit determinism on historical windows |
| Multi-consumer consistency | Perfect — one code path |

**Best for:** dashboards, MCP queries, ad-hoc agent questions,
backtests over modest universes.

### 2.2 Pattern B — Pre-computed feature store (gold layer)

A nightly job materializes `gold.features_{provider}_{interval}` Iceberg
tables: one row per `(symbol, timestamp)` with columns for every
configured indicator. Reads serve pre-computed values.

| Property | Value |
|---|---|
| Latency | ms (just a row read) |
| Cost | Storage + nightly compute |
| Storage | ~3–10 GB per indicator × interval × five years × 10k symbols |
| Freshness | T+1 day (or whenever the nightly job ran) |
| Reproducibility | Iceberg snapshot per nightly run → pinnable per training |
| Multi-consumer consistency | Perfect within a run; risk of staleness vs Pattern A in the same query |

**Best for:** ML training that scans massive universes × indicator sets,
production inference where latency is the floor, anything that pays
the storage cost in compute savings.

### 2.3 Pattern C — Compute-on-read with caching

Same path as A, but with a cache layer keyed on `(symbol, interval,
indicator, params, end_ts)`. Once a bar closes, its indicator value
is immutable — so the cache is trivially invalidatable (no TTL
needed, no "when did bronze update" check).

**Best for:** popular symbols hit by many concurrent dashboard users.
**Not yet relevant for us** — we don't have the concurrency or hit
patterns to justify the operational overhead. Drop in later via
Redis or an in-process LRU on the reader.

## 3. Our decision

**Pattern A now (TA-3 onward). Pattern B in Phase 6 (Gold).**

Rationale:

1. **Pattern A reuses everything we have.** `BronzeReader` /
   `BarReader` already deliver bars. `INDICATOR_REGISTRY` already
   computes math. We need one new reader + Pydantic shapes + thin
   adapters at the route and MCP layers — and the same code path
   `Context` uses inside the backtester. **Single source of truth
   for indicator math across all consumers.**

2. **Pattern A's latency budget is met.** A dashboard chart pulling
   200 bars × 5 indicators computes in under 50ms cold. The
   bottleneck is bar I/O (already optimized via Iceberg partition
   pruning + CH ReplacingMergeTree).

3. **Pattern B doesn't pay for itself yet.** We're not running large
   training loops that re-compute the same indicators repeatedly.
   When we are (Phase 6), the swap is contract-preserving — see §6.

4. **Pattern C is premature optimization.** We have no concurrent-user
   pressure today. Adding Redis or an LRU adds operational complexity
   for benefits we can't measure.

The full data-plan picture:

```
                   today (TA-3)              Phase 6 (Gold)
                   ────────────              ──────────────
  Dashboard  ──┐                            ┌── reads gold tables
  MCP agent  ──┼──> IndicatorReader  ──┐    │       (when
  Backtester ──┘    (Pattern A)        │    │      latency-bound)
                                       │    │
                          ┌────────────┴────┴────────┐
                          │  Same Pydantic contract  │
                          └────────────┬─────────────┘
                                       │
                       BronzeReader / BarReader (bars)
                                       │
                       INDICATOR_REGISTRY (math, shared)
```

The `IndicatorReader` is the swap-point. Today it computes on read;
in Phase 6 a subclass or a config flag flips it to read from
`gold.features_*` for the same `(symbol, interval, indicator, params,
window)` query. **Consumers see no API change.**

## 4. Concrete design (TA-3 implementation)

### 4.1 Folder layout

```
app/
├── indicators/                    pure math (existing + 4 new in TA-3)
│   ├── base.py / sma.py / ema.py / rsi.py / macd.py / tsi.py
│   ├── atr.py        (new)        Average True Range
│   ├── bollinger.py  (new)        Bollinger Bands (3 series via compute_full)
│   ├── stochastic.py (new)        Stochastic %K / %D
│   ├── wma.py        (new)        Weighted Moving Average
│   ├── registry.py                INDICATOR_REGISTRY
│   └── README.md
│
├── services/
│   └── readers/
│       └── indicator_reader.py    (new) The single source of truth
│
├── api/
│   └── routes_indicators.py       (new) HTTP surface for dashboard
│
└── mcp/tools/
    └── indicators.py              (new) MCP surface for agents
```

### 4.2 Pydantic contracts

```python
class IndicatorValue(BaseModel):
    """One (timestamp, value) pair. Value is None during warmup."""
    timestamp: datetime
    value: Optional[float] = None


class IndicatorSeries(BaseModel):
    """
    A named series of computed indicator values.

    For multi-output indicators (Bollinger, Stochastic, MACD), the
    reader produces ONE IndicatorSeries per output component, labeled
    e.g. 'bollinger_upper', 'bollinger_middle', 'bollinger_lower'.
    """
    name: str                          # 'sma', 'rsi', 'bollinger_upper', etc.
    params: dict[str, Any]
    label: str                          # display label: 'SMA(20)', 'BB Upper(20, 2.0)'
    values: list[IndicatorValue]
    count: int


class IndicatorChartData(BaseModel):
    """
    Multi-indicator bundle aligned to the same bar window. The
    canonical response shape for dashboard chart overlays and
    agent batch queries.
    """
    symbol: str
    interval: SupportedInterval
    start: datetime
    end: datetime
    bars: list[BronzeBar]              # or LiveBar — Bar Protocol covers both
    series: list[IndicatorSeries]
    snapshot_id: Optional[str] = None  # Iceberg snapshot when bronze-backed
```

### 4.3 IndicatorReader service

```python
class IndicatorReader:
    """
    Single source of truth for "give me indicator X over this window
    for this symbol." Used by HTTP routes, MCP tools, and (via Context)
    the backtester.
    """

    @classmethod
    def from_settings(cls) -> "IndicatorReader": ...

    def get_series(
        self, symbol: str, indicator: str, params: dict,
        start: datetime, end: datetime,
        *, interval: str = "1d",
    ) -> IndicatorSeries: ...
    """Single indicator. Single series out (multi-output indicators
    pick their canonical component — SMA for bollinger, %K for stochastic)."""

    def get_chart_data(
        self, symbol: str, indicators: list[IndicatorSpec],
        start: datetime, end: datetime,
        *, interval: str = "1d",
    ) -> IndicatorChartData: ...
    """Multi-indicator. Bars + N series in one response. Multi-output
    indicators (Bollinger/Stochastic/MACD) explode into multiple
    IndicatorSeries entries in the response.

    This is what the dashboard chart endpoint will use."""
```

Bar source selection mirrors the Backtester:
- `interval='1m'` (or other intraday) → `BronzeReader.get_bars()`
  (CH-independent, snapshot-pinned)
- `interval='1d'` → `BarReader.get_bars_in_range(...)` (CH live tier;
  no snapshot)

### 4.4 HTTP routes

```
GET  /api/indicators/series
       ?symbol=AAPL
       &start=2024-01-01T00:00:00Z
       &end=2024-12-31T23:59:59Z
       &interval=1d
       &indicator=sma
       &period=20                   ← indicator params as flat query
     → IndicatorSeries
```

Single-indicator. Useful for curl / ad-hoc inspection.

```
POST /api/indicators/chart-data
     {
       "symbol": "AAPL",
       "start": "2024-01-01T00:00:00Z",
       "end": "2024-12-31T23:59:59Z",
       "interval": "1d",
       "indicators": [
         {"name": "sma", "params": {"period": 20}, "label": "SMA(20)"},
         {"name": "sma", "params": {"period": 50}, "label": "SMA(50)"},
         {"name": "rsi", "params": {"period": 14}, "label": "RSI(14)"},
         {"name": "bollinger", "params": {"period": 20, "std": 2.0}}
       ]
     }
     → IndicatorChartData
```

Multi-indicator. The dashboard / agent path.

### 4.5 MCP tools

```python
@mcp.tool()
def compute_indicator(
    symbol: str, indicator: str, params: dict,
    start: datetime, end: datetime, interval: str = "1d",
) -> IndicatorSeries:
    """Compute one indicator series. Use when an agent needs a
    single number or short series — 'what's AAPL's RSI(14) now?'"""

@mcp.tool()
def compute_indicators(
    symbol: str, indicators: list[IndicatorSpec],
    start: datetime, end: datetime, interval: str = "1d",
) -> IndicatorChartData:
    """Multi-indicator + bars in one response. Use for chart-style
    queries — 'show me AAPL daily with SMA(20)/SMA(50)/RSI(14)'."""

@mcp.tool()
def get_chart_data(
    symbol: str, *,
    interval: str = "1d",
    lookback_days: Optional[int] = None,
    indicators: Optional[list[IndicatorSpec]] = None,
) -> IndicatorChartData:
    """Convenience: like get_bars_for_chart but with indicators overlaid.
    Same lookback_days + auto-limit semantics."""
```

## 5. Dashboard integration path

For TA-3 we **don't change the existing dashboard**. New endpoints
land additively; the chart code can adopt them incrementally:

- **Step 1 (TA-3):** Dashboard keeps hitting `/api/bars` for OHLCV
  (unchanged). Chart JS can issue a parallel `POST
  /api/indicators/chart-data` and overlay the returned series on
  the candlestick chart. Two requests, one chart, zero
  regression risk.

- **Step 2 (later, separate work item):** Migrate the chart to one
  request: `POST /api/indicators/chart-data` returns both bars +
  indicator series. Drop `/api/bars` from the chart's call path
  (but keep the endpoint — it's used by other consumers).

The HTTP routes are designed so step 1 and step 2 are both
trivial — same response shape.

## 6. Phase 6 (Gold) migration plan

When training-loop compute starts dominating and we need
pre-computed features:

1. **Schema:** Iceberg table `gold.features_{provider}_{interval}` with
   columns `(symbol, timestamp, <indicator>_<params>)`. One column
   per (indicator, params) combination configured.

2. **Writer:** Nightly job after bronze-refresh completes. For each
   `(symbol, interval)` pair, run the indicator set, append rows.
   Watermark via `ingestion_runs` (the same table that gates
   bronze's idempotent re-runs today).

3. **Reader swap:** `IndicatorReader` gains a `backend` config —
   `"compute"` (Pattern A, default) or `"gold"` (Pattern B). The
   public API doesn't change. Strategies / dashboards / MCP tools
   are agnostic.

4. **Hybrid mode:** `backend="auto"` — read from gold when
   `interval/indicator/params` are pre-computed, fall back to compute
   when not. Lets us pre-compute popular combinations and still serve
   ad-hoc queries.

5. **Reproducibility:** the gold-tier table is Iceberg, so it has its
   own snapshot history. Pin the gold `snapshot_id` alongside the
   bronze one in `agent_runs` / `model_training_runs` (Phase 6).

## 7. Multi-output indicators (Bollinger, Stochastic, MACD)

Per the existing `Indicator(ABC)` contract, `compute()` returns a
single `pd.Series`. Multi-output indicators expose **additional
methods**:

```python
class BollingerBands(Indicator):
    def compute(self, close, high=None, low=None) -> pd.Series:
        """Returns the middle band (SMA) — the canonical single-output."""
        ...

    def compute_full(self, close, high=None, low=None) -> dict[str, pd.Series]:
        """Returns {'upper': ..., 'middle': ..., 'lower': ...,
                    'bandwidth': ..., 'percent_b': ...}."""
        ...
```

The `IndicatorReader.get_chart_data` consumer calls `compute_full()`
when available and emits one `IndicatorSeries` per component
(labeled `bollinger_upper`, `bollinger_middle`, `bollinger_lower`).
Single-output consumers (`Context.indicator()` in the backtester) get
the canonical Series.

This convention matches the existing MACD implementation (which has
`compute` + `compute_signal` + `compute_histogram` + `compute_full`).

## 8. Testing strategy

Per [docs/trading_subsystem_design.md §9](trading_subsystem_design.md#9-testing-strategy):

- **Unit:** every new indicator against known math. Synthetic input
  series → expected output values within tolerance.
- **Integration:** `IndicatorReader.get_chart_data(AAPL, [SMA(20),
  RSI(14)], 2024-01-01, 2024-03-31)` against real bronze produces a
  plausible IndicatorChartData with snapshot_id pinned.
- **Cross-consumer consistency:** the SMA(20) value at timestamp T
  computed by the dashboard route = the SMA(20) value at T computed
  by the MCP tool = the SMA(20) value at T computed inside the
  backtester's Context. This is enforced by the single-code-path
  design but verified by a regression test that runs all three and
  diffs the outputs.

## 9. Phasing

| Phase | Lands | Why |
|---|---|---|
| **TA-3.1** (next) | 4 new indicators (ATR, Bollinger, Stochastic, WMA) + unit tests + registry update | Indicator math first; consumers come next |
| **TA-3.2** | `IndicatorReader` + Pydantic shapes + HTTP routes + MCP tools + tests | **The exposure layer this doc covers** |
| **TA-3.3-5** | RSI Extreme / Bollinger Mean-Revert / EMA Crossover strategies | Comparison baselines built on top of the indicators |
| **TA-3.6** | Strategy bake-off — all 4 baselines on same window, comparison table | Anchor results before adding LLM iteration |
| **Phase 6 (Gold)** | Pre-computed `gold.features_*` Iceberg tables + reader backend swap | Future — when training compute dominates |

## 10. Decision log

- **2026-05-17** — Pattern A (compute-on-read) for TA-3. Defer
  Pattern B (gold features) to data-plan Phase 6, when training-
  compute pressure justifies the storage cost. Reader is the
  swap-point; consumers see the same contract under either backend.
- **2026-05-17** — Multi-output indicators (Bollinger, Stochastic)
  follow the existing MACD convention: `compute()` returns one
  canonical Series, `compute_full()` returns a dict of components.
  The `IndicatorReader` decomposes into multiple `IndicatorSeries`
  entries in the response, one per component, with explicit labels.
- **2026-05-17** — `IndicatorChartData` is the canonical multi-output
  shape for the dashboard chart and the LLM agent's get_chart_data
  call. One shape, two surfaces.
