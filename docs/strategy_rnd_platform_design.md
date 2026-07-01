# Strategy R&D Platform — Design

**Status:** DRAFT for sign-off · **Owner:** trading subsystem · **Builds on:**
[`trading_subsystem_design.md`](trading_subsystem_design.md), the existing
`app/services/sim/` engine, and `app/services/alerts/`.

## 1. Purpose

Turn our alert/signal output into a system that **demonstrably makes money**, and
make finding the next profitable strategy cheap. The product we sell is
*"A+ setups with a verifiable, profitable track record"* — surfaced as alerts the
customer can act on, and (later) traded by a gated agent.

This is **not a single strategy**. It is an R&D loop: propose a signal/strategy →
backtest it reproducibly → score it → (if it survives out-of-sample) forward
paper-trade it → only then does it count toward the public track record. Humans
*and* AI agents drive that loop.

## 2. Principles

1. **Modularity over commitment.** Signals, strategies, instruments, fees, exits,
   and scoring are all swappable. No strategy is privileged in the engine.
2. **Honesty / no-look-ahead.** The track record is credible only if it is
   out-of-sample. Backtests pin data + code version; the live record is forward
   paper-trading. This is the same doctrine that differentiates our Elliott Wave
   work — extended platform-wide.
3. **Reproducibility.** Every run pins Iceberg snapshot + git SHA + strategy
   version + params (already enforced by the engine). Same input → same metrics.
4. **Equities first, options next.** Validate the loop and a real edge on equity
   swing trades (no option-pricing complexity), then add options as a distinct
   instrument track.
5. **No real money yet.** Paper trading only until a strategy earns trust. (See
   [[project_product_scope]] — subscription SaaS, not a broker.)

## 3. Architecture — extension axes

Everything funnels into the existing reproducible backtest → metrics → registry
loop. The platform is defined by what plugs into it:

| Axis | Exists today | Net-new in this initiative |
|---|---|---|
| **Signal source** | EW wave-3/5 (`alerts/service.py`), MA-cross (`alerts/crossover.py`) | `SignalSource` protocol + a **breakout/momentum** detector |
| **Strategy** | rules + LLM (`sim/strategies/llm_agent.py`); Protocol-based | **ML (predictive), RL (reward-learning)** as strategy types |
| **Instrument** | equities (`sim/portfolio.py`) | **options** track (contracts, theta, chain fills) — phase 2 |
| **Holding period** | any — exits are rules/params | (no change; multi-day/week already supported) |
| **Scoring / gate** | probability + R:R gates in `build_alert` | composable **A+ filter/scoring** layer + agent decision gate |
| **Validation** | `RunMetrics` + `agent_runs` registry | **out-of-sample bake-off** + forward **paper-trade** track record |
| **Cost model** | `sim/fees.py` (fees + slippage), `NextBarOpenFill` | reused unchanged across backtest and paper trading |

We are **not** rebuilding the backtester, evaluator, Context, reproducibility, or
the MCP `run_backtest` surface — they already exist and already support this shape.

## 4. Core new abstractions

### 4.1 `SignalSource` (the bridge — the first unblock)
A pluggable provider that yields, per symbol/time, a normalized trade intent:

```
Signal: { symbol, ts, direction, entry, stop, target_1, target_2?,
          confidence, kind, source_agg, rationale, meta }
```

- Adapters wrap existing emitters: `EWSignalSource` (wave-3/5), `MACrossSignalSource`,
  and the new `BreakoutSignalSource`.
- An `AlertStrategy` (a `Strategy` Protocol impl) consumes a `SignalSource` and
  deterministically executes each signal's own plan (enter at entry zone, exit at
  stop / target). This is what makes alerts **backtestable** — today's critical gap.
- Same `SignalSource` later feeds the live paper-trade loop, so backtest and live
  read identical signals.

### 4.2 Breakout / momentum detector
New `SignalSource`: "stock going on a run" — N-day-high break, volume/ADR expansion,
relative strength vs SPY, optional MA-trend confirmation. Pure functions over bars
(mirrors `signals/` detectors). Pairs naturally with the options track later
(buy calls on the breakout).

### 4.3 A+ scoring / gate layer
"A+" is **not hardcoded** — it is a stack of composable filters over a `Signal`
(EW present + above 200d SMA + R:R ≥ X + breakout confirmation + …) producing a
score. Filters are individually testable; the agent (or a human) composes them.
This is the surface where iteration/AI search happens.

### 4.4 Execution: deterministic now, gated-agent next
- **Now:** deterministic executor turns a `Signal` into entry/stop/target fills —
  reproducible, the basis of the track record.
- **Next:** the existing LLM strategy may *propose* trades, but a deterministic
  **gate** (risk rules, R:R floor, confidence floor, position/exposure limits)
  must pass before any fill. AI decides within guardrails; the gate is auditable.

### 4.5 Forward paper-trading loop + track record
- Persistent sim portfolio + a per-bar/real-time execution loop driving the same
  executor off live bars.
- New `sim_trades` + equity-curve persistence (ClickHouse), exposed via API/MCP and
  the product UI. This is the **out-of-sample, customer-facing** record.
- Reuses the same fee/slippage models as backtest → results are directly comparable.

## 5. Instruments

- **Equities (now):** existing `Position`/`Trade` accounting. Swing holds (days→weeks)
  already supported.
- **Options (next track):** needs contract modeling (strike/expiry), option P&L
  including **theta over multi-day holds**, and fills from our options-chain data
  (`options.schwab_chain_contracts`, `gamma_exposure_snapshots`). Scoped as its own
  milestone — a new instrument adapter behind the same `Strategy`/executor, not a
  rewrite. Breakout signals + options is the headline use case here.

## 6. AI / ML / RL hooks

The platform is the research substrate, not a bolt-on:
- **LLM strategies** already run (cached, reproducible).
- **ML (predictive):** a feature/label store derived from bars + signals; models
  emit a `SignalSource` (probability of a profitable move) → backtested like any other.
- **RL (reward-learning):** the backtester is the *environment*; `RunMetrics`/P&L is
  the *reward*; `agent_runs` is the experiment log. RL agents are just another
  `Strategy` that learns its policy.
- An agent can drive the whole loop via MCP: read signals → compose filters →
  `run_backtest` → read metrics → iterate.

## 7. Track record & validation (the trust contract)

A strategy earns the public track record only by passing, in order:
1. **In-sample backtest** — positive expectancy, sane drawdown.
2. **Out-of-sample bake-off** — held-out symbols/years it never tuned on.
3. **Forward paper-trade** — live, no hindsight, real fills, for a minimum window.

Only stage-3 numbers are shown to customers. Backtests are R&D, never the pitch.

## 8. Phased roadmap (equities-first)

| # | Milestone | Deliverable |
|---|---|---|
| **1** | `SignalSource` + `AlertStrategy` bridge | Backtest EW + MA-cross signals → win-rate / avg-R / P&L / drawdown vs buy-and-hold. First evidence of edge. |
| **2** | Breakout/momentum signal + composable A+ filters | A second signal proves modularity; A+ = a tunable filter stack. |
| **3** | OOS bake-off + forward paper-trade loop + `sim_trades` | The credible, out-of-sample track record, exposed via API/MCP. |
| **4** | Gated AI-agent decisions | LLM proposes, deterministic gate approves; auditable. |
| **5** | Options instrument track | Buy calls/puts on breakouts; theta-aware multi-day holds. |
| **6** | ML/RL research tracks | Feature/label store + models/agents as `SignalSource`/`Strategy`. |
| **7** | Product surface + Stripe | A+ setups + live track record in the UI; monetize on proven edge. |

## 9. Non-goals (for now)

- No real-money order routing (paper only).
- No personalized investment advice (we publish system performance, not advice).
- Options modeling is **not** in Milestone 1–4 (equities-first).

## 10. Open decisions (resolve as we build)

- Backtest universe + window for Milestone 1 (proposed: ~10–20 liquid names ×
  3 years daily, plus a held-out OOS set).
- Position sizing default (fixed-fractional vs risk-based off the stop).
- Minimum paper-trade window before a strategy is "track-record eligible."
- Where the A+ filter definitions live (config vs registry vs agent-authored).
