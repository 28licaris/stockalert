# System Review — 2026-05-17

**Reviewer position:** I'm reviewing as if I were a senior quant
engineer being asked "can this system make money in the market?"
Not "can this system run a backtest?" Those are different
questions. I'm going to be direct about gaps because the cost of
deploying a beautiful-but-leaky system with real money is much
higher than the cost of hearing hard things now.

**Scope:** all code, plan docs, baselines, and architectural
commitments as of commit `eb474a1`.

**Bottom line up front:** the **engineering quality is genuinely
strong** — far above where most retail trading systems live. The
data architecture (Iceberg + provider precedence + silver/gold
medallion) is institution-grade. The strategy framework is
modular and reproducible. The doc discipline is rare.

**But there are seven gaps that, if not closed, will reliably
turn a winning backtest into a losing live deployment.** They are
the difference between "can produce signals" and "can be
profitable." I list them in §3 ordered by which will hurt you
fastest.

---

## 1. What's strong (genuinely state-of-the-art)

These are the parts where the system is already in the top
quartile of trading platforms I've seen at this scale:

### 1.1 Data architecture is institution-grade

- **Medallion lake** (bronze → silver → gold) with explicit
  separation of concerns. Most retail systems collapse this into
  "one big CH table" and pay for it later.
- **Iceberg snapshot pinning** for reproducibility. This is what
  hedge funds do. Most retail systems can't tell you "this
  backtest used exactly these bars" — you can.
- **Per-provider bronze tables + config-driven precedence.**
  Means you can plug providers in and out without code changes;
  means you can audit provider disagreements.
- **The ground-truth rule** (silver canonical, CH derived). This
  is the right discipline; it's exactly how Two Sigma /
  Renaissance / Citadel think about data lineage.

### 1.2 Backtest framework is honest

- **Look-ahead invariant enforced at the harness level**: fills
  happen on the next bar's open by default; the multi-TF
  back-pressure ensures coarser-interval bars are only visible
  when their session ends. Pinned by tests.
- **Pluggable fees + slippage models** (`FeeModel` / `SlippageModel`
  Protocols + 5 default implementations). The retail mistake is
  hardcoding zero-fees-zero-slippage and being shocked when live
  trading bleeds 3% to the broker.
- **Reproducibility pinning** — every `RunResult` carries
  `snapshot_id` + `git_sha` + `strategy_version` + `strategy_params`
  + `config`. Re-running the same triple produces an identical
  metrics row. This is a competitive moat in itself; almost
  nobody does this.
- **Multi-timeframe Context** with the no-look-ahead guarantee
  (`coarser_bar + interval_duration <= execution_bar`). Pinned
  by `test_backtester_releases_coarser_bars_only_when_ready`.

### 1.3 Engineering discipline

- **117 Python files** in `app/`, **42 test files**, **13 plan
  docs**. Test-to-source ratio is healthy.
- **Strict typing** via Pydantic everywhere — at the schema
  boundary, at the contracts, at the MCP tools. The class of
  bugs where "JSON shape changed and a downstream consumer
  silently crashes" is largely eliminated.
- **One contract for HTTP + MCP**. Adding the agent surface was
  a wiring change, not a contract change. This is the right
  architecture for the LLM-strategy era.
- **Decision log discipline.** Every architectural choice is
  recorded in [BUILD_JOURNAL.md](BUILD_JOURNAL.md) with a date
  and rationale. Future-you (or a future agent) will thank you.
- **Modularity contracts enforced by tests** — strategy purity,
  no-CH-imports, no-look-ahead. AST-walk gates catch
  contract violations at PR time.

### 1.4 The agent surface

- **MCP server with 29 tools** covering data, indicators,
  screener, backtests, lake, live. Most quant teams retrofit
  this; you have it as a first-class architectural surface from
  day one.
- **LLM strategy with response caching** for cost-bounded
  reproducible runs.
- **Two more agent surfaces in the plan** (RL via the existing
  Strategy Protocol; EW state as additional Context input).

### 1.5 Plan-quality

Six plan docs (data, trading, indicator-exposure, EW, frontend,
silver) — each with phasing, gates, reproducibility, risks, and
explicit decisions deferred. This is the kind of paperwork that
separates "side project" from "thing that will actually ship."

---

## 2. What's weak (real gaps in what's been built)

These are real weaknesses in the **existing code**, not just
"future work." Each one will compound into a bigger problem if
not addressed.

### 2.1 Test coverage is thin where it matters most for safety

The test counts hide a problem when you look at *where* the
coverage is:

| Area | Test files | Risk if buggy |
|---|---|---|
| `app/services/sim/` (backtest harness) | 2 | Wrong backtest = burning capital |
| `app/services/screener/` | 1 | False candidates = wasted attention |
| `app/services/readers/` | 1 | Wrong bars = wrong signals + wrong fills |
| `app/services/live/` | 2 | Stream drops invisible = missed signals |
| `app/indicators/` | 1 | Bad indicator math = poisoned everything downstream |

The two `sim/` test files cover the canary path (SMA crossover,
multi-TF). The harness itself, the Portfolio class, the
Backtester orchestration, the slippage/fees models — most of
their edge cases are unverified.

**The Portfolio class** ([app/services/sim/portfolio.py](app/services/sim/portfolio.py))
is the single most safety-critical piece of code in the
backtest path. It implements:
- Cash accounting
- Position tracking with cost basis
- Fee/slippage application
- Mark-to-market on every bar
- Trade-log accumulation

I see no test file dedicated to it. There's no
`test_portfolio.py`. The math is "tested" only transitively
through end-to-end backtests against AAPL — which means the
edge cases (insufficient cash, partial fills, fee-driven
zero-quantity orders, float-precision creep on the equity
curve) might or might not be exercised.

**Recommended:** before any live execution, every method on
`Portfolio` gets a dedicated test class with synthetic
adversarial inputs. Same for `Backtester.run`. Same for each
slippage/fees model. This is the lowest-cost highest-leverage
work in this entire review.

### 2.2 Strategy results are weak (and honestly so)

From the bake-off summary in [BUILD_JOURNAL.md](BUILD_JOURNAL.md):

| Strategy | Window | Trades | Return | Sharpe | Max DD |
|---|---|---:|---:|---:|---:|
| `sma_crossover` (canary) | AAPL 2023-24 daily | 5 | +2.65% | +0.305 | -5.90% |
| `ema_crossover` ⭐ | AAPL 2023-24 daily | 7 | +9.02% | **+0.933** | -6.67% |
| `rsi_reversion` | AAPL 2023-24 daily | 12 | -0.13% | +0.015 | -6.17% |
| `bollinger_mean_revert` | AAPL 2023-24 daily | 12 | -1.89% | -0.188 | -7.82% |
| `mtf_ema_trend_filtered` | AAPL Jun-Dec '24 hourly | 44 | **-9.75%** | -1.168 | n/a |

Hard truths from this table:

1. **Five strategies; only ONE has a Sharpe above 0.5.** Three are
   below 0.1. One is decisively negative.
2. **Three of five have negative or near-zero returns** over a
   period when AAPL itself gained ~50%. Pure buy-and-hold on
   AAPL would have crushed every strategy.
3. **Single-symbol, single-window backtests.** No statistical
   evidence that the winning EMA-crossover signal isn't an AAPL
   2023-24 artifact. Run it on MSFT, GOOG, TSLA over the same
   window; run it on AAPL 2019-22; if the +0.933 Sharpe survives,
   it's a real signal. If it doesn't, it's noise.
4. **The MTF strategy** — the most architecturally ambitious one
   — lost money. Per the journal: "MTF infra ✓, strategy noisy."
   The infrastructure works; the strategy hypothesis didn't pan
   out. Treating "infra works" as success is a category error —
   profit requires both infra AND a real edge.

**Recommended:** before any live execution, every strategy
ships with a **multi-symbol, multi-window bake-off** as its
own gate. Acceptance threshold: Sharpe > 1.0 OOS across at
least 5 symbols + 3 disjoint years. Don't deploy capital on a
single-symbol Sharpe.

### 2.3 No survivorship bias controls

There's no `gold.universes` table yet (designed in
`data_platform_plan.md` §7 but not built). This means:

- Today's watchlist symbols are used as the universe for
  backtests over 2023-24
- But 2023's actual S&P 500 had different members than today's
  (companies got added, dropped, delisted)
- A backtest that uses today's universe on 2023's prices is
  **counterfactually advantaged** — you didn't have a way to
  buy NVDA-the-2024-trillion-dollar-stock back when NVDA was
  $13 in 2018

The bias direction is always positive: dropped names disappear,
winners stay. Backtests look ~1-3% Sharpe better than reality.
On a strategy showing Sharpe 0.93, that could be the entire
edge.

**Recommended:** the planned `gold.universes` table is on the
roadmap (TA-8). It belongs in the **same milestone** as silver,
not three phases later. Without it, every backtest you run on
silver is still cheating.

### 2.4 No risk management code anywhere

Not a single line. I grep'd for `risk`, `position_size`,
`max_position`, `drawdown`, `kill_switch`, `leverage` — zero
hits in `app/`.

The `Portfolio` class can:
- ✅ Track cash and positions
- ✅ Apply fees and slippage
- ❌ Refuse to take a position larger than X% of equity
- ❌ Refuse to enter a trade if portfolio drawdown >Y%
- ❌ Refuse to enter a trade if symbol's recent volatility >Z
- ❌ Sector concentration limits
- ❌ Correlation limits across positions
- ❌ Kill-switch (halt all trading on signal-of-broken-system)

**Today this doesn't burn you** because backtests are
single-symbol and you're not live. The instant you go
multi-symbol or paper-trade, this becomes the #1 source of
catastrophic losses.

**Recommended:** before TA-RL or paper trading, add an
`app/services/risk/` module that wraps the Portfolio with:
- `MaxPositionSizeRule(pct_equity: float)`
- `MaxDrawdownHaltRule(pct: float)` (when triggered, all new
  entries blocked until reset)
- `MaxSymbolConcentrationRule(pct: float)`
- `MaxCorrelatedExposureRule(correlation_threshold, pct_equity)`
- `VolatilityScaledPositionSizer` (ATR-based — already half the
  work; we have ATR)
- `KillSwitch` (one config flag halts everything; surfaced as
  an MCP tool so the LLM agent can self-kill on detected
  anomalies)

This is not a future-roadmap item. This is "what stands between
you and ruin." Build this BEFORE any execution code.

### 2.5 Execution layer doesn't exist

There's no `app/services/execution/`, no `Executor` Protocol,
no paper-trading shim, no order-management. There's no path
from "strategy says BUY" to "broker receives an order." The
Schwab provider is read-only (quotes, history); the trader-API
write side isn't wired up.

This is honestly stated in the trading_subsystem_design.md
phasing (TA-6+: "Paper trading → live"). But for the system to
be "profitable in the market," this is the **other** half of
the work — and it's not designed yet.

**Recommended:** an `app/services/execution/` module with:
- `Executor` Protocol — `submit_order(order)`, `cancel_order(id)`,
  `get_position(symbol)`, `get_equity()`.
- `BacktestExecutor` — uses the `Portfolio` (existing).
- `PaperExecutor` — Schwab paper account; same API as live;
  records every action to `audit_events`.
- `LiveExecutor` — Schwab trader API; behind a hard config flag.
- `OrderManagementSystem` — child orders, partial fills, parent
  order tracking, time-in-force handling.
- `ReconciliationJob` — every 5 min, asks broker for current
  positions and verifies against our internal Portfolio. Alerts
  on drift.

The Executor Protocol is the seam. Strategies don't care which
Executor backs them (already true in the backtest harness).
Adding paper trading = a new `Executor` impl, not a refactor.

### 2.6 No market regime detection

Every strategy in `app/services/sim/strategies/` is
**regime-agnostic** — it runs the same logic in trending, mean-
reverting, high-vol, and crisis regimes. The bake-off table
makes this brutally clear:

- `ema_crossover` works in a trending year (AAPL 2023-24, +50%).
  Run it on AAPL 2018 (sideways) or 2008 (crisis) and it would
  almost certainly lose.
- `bollinger_mean_revert` loses in a trending year. It wants
  range-bound markets.
- The MTF strategy added a trend filter but still lost.

Without a **regime classifier**, strategy selection is a coin
flip. The platform either:
- Picks one strategy and lives with regime-mismatch losses, OR
- Runs an ensemble where weights are static, ignoring the
  regime context, OR
- Has a regime classifier that picks the right strategy for the
  current regime

The third option is the only profitable one. Today the system
has zero machinery for it.

**Recommended:** an `app/services/regime/` module with:
- `RegimeClassifier` Protocol returning labels like
  `{trend_up, trend_down, range, high_vol_breakout, crisis}`.
- A simple HMM or moving-window ADX/volatility-based classifier
  as the baseline.
- Strategies declare which regimes they work in (`@only_in('trend_up')`).
- The orchestrator (TBD) routes to the right strategy for the
  current regime.

This pairs naturally with the EW plan (wave-3 = trend regime;
wave-A correction = range/transition). And with the RL track
(regime is a natural state feature).

### 2.7 No live observability / alerting

We have `monitor_service` and `monitor_manager` — they monitor
*per-symbol divergence signals*, not the *system itself*. There's
no:

- Latency SLO monitoring (live stream tick → CH write)
- Error budget tracking
- Memory / CPU / connection-pool health
- Per-strategy P&L tracker that pages when intra-day DD exceeds X
- Detection of "we haven't received a Schwab tick in N minutes
  during market hours" (silent stream death)

The cockpit Status page (FE-1) will fix some of this. But the
**backend telemetry** isn't there yet. There's no Prometheus,
no Sentry, no structured log aggregation, no on-call rotation.

For a single-user dev system this is fine. For a system you
trade real capital with, this is a single-point-of-failure
class problem.

**Recommended:** before live trading:
- Structured logging (already mostly there) + Loki/Grafana or
  equivalent for query.
- Sentry for exception capture (no-op stub today; wires up in
  SaaS phase per frontend_plan).
- Prometheus metrics: counter per CH insert, gauge per active
  monitor, histogram per tick→write latency.
- Pager integration (PagerDuty or just SMS via Twilio) on
  critical alerts.
- A daily "morning brief" email/Slack: yesterday's P&L, current
  positions, any halt conditions tripped, data-freshness
  status.

---

## 3. The seven things that will kill profitability if not fixed (ranked by urgency)

This is the punch list. Numbers are the order of severity.

### 🔥 1. Lack of risk management code (§2.4)

**Why this kills profit:** the first multi-symbol live strategy
will, on some particular day, decide all 100 watchlist symbols
are a buy. Without position sizing, you blow through your
account.

**Effort to fix:** 3-5 days.
**Add a new plan doc:** `docs/risk_management_plan.md`. Phase
this BEFORE TA-RL and BEFORE any execution.

### 🔥 2. Survivorship-bias-cheating backtests (§2.3)

**Why this kills profit:** every Sharpe number you currently
have is overestimated by 1-3% absolute. The `ema_crossover`
+0.933 might be a real +0.6 (mediocre); the others probably
have negative true Sharpe.

**Effort to fix:** 3 days. Build `gold.universes` from a free
data source (Wikipedia historical S&P 500 deltas, or DataHub,
or Sharadar if budget allows).
**Add to phasing:** elevate from TA-8 to TA-5.0.5 (parallel
with corp-actions ingestion). It's a one-symbol-table job; not
a full gold-build.

### 🔥 3. Strategy results are weak; no out-of-sample evidence (§2.2)

**Why this kills profit:** deploying a single-symbol-single-
window Sharpe to production is gambling. None of your current
strategies have evidence of real edge. The cockpit Status
page (FE-1) lulls you into "the system is healthy" while
the strategies bleed.

**Effort to fix:** 2 days of bake-off harness work + indefinite
strategy R&D.
**Add to phasing:** a `scripts/run_oos_bakeoff.py` that runs
each strategy on ≥5 symbols × ≥3 disjoint years. Acceptance
gate for any strategy entering paper trading: OOS Sharpe > 1.0.

### ⚠️ 4. No regime classification (§2.6)

**Why this kills profit:** running the wrong strategy in the
wrong regime is one of the biggest sources of retail strategy
death. Buy-the-dip works in a bull market and goes to zero in a
2008. Your bake-off shows this dynamic exactly.

**Effort to fix:** 2 weeks for a usable v1.
**Add a new plan doc:** `docs/regime_classifier_plan.md`. Pair
naturally with EW track.

### ⚠️ 5. Backtest-harness tests are thin (§2.1)

**Why this kills profit:** a Portfolio off-by-one in cash
accounting, a fee/slippage corner case mishandled, an equity-
curve float-precision drift — any of these silently inflate
backtests. You'd never know until live trading proved them wrong
the expensive way.

**Effort to fix:** 1 week of focused test-writing.
**Add to phasing:** `tests/test_portfolio.py` + dedicated
`tests/test_fees_models.py` + `tests/test_slippage_models.py`
+ `tests/test_backtester_edge_cases.py` as a gate before
TA-RL.

### ⚠️ 6. Execution layer doesn't exist (§2.5)

**Why this kills profit:** can't deploy capital without it.
This is "future work" today, but it's the longest pole — paper
trading + reconciliation + live execution + broker integration
is ~6-8 weeks of work. Starting it parallel with the agent
training is the right move.

**Effort to fix:** 6-8 weeks.
**Add a new plan doc:** `docs/execution_plan.md`. Recommend
starting paper-execution scaffold in parallel with TA-5
(silver).

### ⚠️ 7. No live system observability (§2.7)

**Why this kills profit:** something will break silently. A
Schwab stream will die at 14:32 ET on a Tuesday. You won't
notice until you check the dashboard at 9pm. By then you've
missed a day of fills, the cockpit shows "healthy" because
nobody told it the stream was dead, and your paper portfolio
has phantom positions from before the disconnect.

**Effort to fix:** 1 week (Prometheus + Loki + Sentry + a
morning brief).
**Add to phasing:** between TA-5 silver and TA-RL agent. The
cockpit Status page (FE-1) is **part of the answer but not the
whole answer** — the backend needs to know it's broken before
the UI can show it.

---

## 4. The seven things I'd add to the roadmap

Cleaned up version of the punch list, sequenced. These are the
**minimum-viable additions to the existing plan** to take it
from "interesting prototype" to "credible profit-grade system."

| # | New phase | When | Effort | Plan doc to write |
|---|---|---|---:|---|
| 1 | **Risk Management v1** (position sizing, max DD, kill switch) | BEFORE TA-RL or paper exec | 3-5d | `docs/risk_management_plan.md` |
| 2 | **Universe history** (`gold.universes`; promote from TA-8) | Parallel with TA-5.0 corp-actions | 3d | extend `data_platform_plan.md` |
| 3 | **OOS bake-off harness** (`scripts/run_oos_bakeoff.py`) | After silver lands | 2d | extend `trading_subsystem_design.md` |
| 4 | **Portfolio + harness adversarial tests** | Before TA-RL | 1wk | no new plan; tracked in journal |
| 5 | **Execution layer** (Executor Protocol + PaperExecutor) | Parallel with TA-5 silver | 6-8wk | `docs/execution_plan.md` |
| 6 | **Regime classifier v1** | After silver + TA-6 indicators | 2wk | `docs/regime_classifier_plan.md` |
| 7 | **Live observability** (Prometheus + Sentry + morning brief) | Before paper exec goes live | 1wk | extend `docs/STARTUP_FLOW.md` |

Total new effort: ~14-16 weeks of work. Doesn't replace the existing
roadmap — *supplements* it. The existing roadmap is necessary
infrastructure; this list is what turns infrastructure into a
P&L.

---

## 5. The single highest-leverage thing to do this week

Three candidates; I'll commit to one:

**A. Start TA-5.0 (silver corp-actions ingestion)** — the
in-flight plan. Unblocks ~4 downstream tracks. 3 days.

**B. Write `docs/risk_management_plan.md`** — the single biggest
gap in the current architecture. 4-6 hours of plan-writing,
then phased work after.

**C. Build the OOS bake-off harness** — turns every current
strategy result into evidence-grade rather than anecdotal.
2 days.

**My pick: B → A → C, in that order.**

Reasoning: the risk-management plan is a **prerequisite** for
A being safe to live-deploy. It's the cheapest of the three
(plan-only initially) and unblocks everything downstream. After
the plan exists, then TA-5.0 corp-actions makes silver real, and
the OOS bake-off harness gives the existing strategies an honest
verdict.

---

## 6. What I'd ship to a paying customer today

Not yet.

What I'd ship to a paying customer **after the seven additions
above land**: the **cockpit + paper-trading** combo. With
risk management, universe history, OOS evidence, observability,
and the Executor abstraction, the system would be a credible
"managed strategy backtest + paper trading" product. The data
layer + reproducibility story would actually be a moat — most
competitors can't show byte-identical backtest replays.

What I'd ship to a paying customer **after that + 6 months of
paper trading evidence**: the **same product with live execution
behind a gated subscription tier**. The seam is already in the
plan (frontend_plan.md §7).

---

## 7. What I'm NOT worried about

For the record, things that are sometimes called out as "you
need this!" but I think you've already handled or don't need:

- **Data quality:** the silver plan handles this comprehensively.
- **Reproducibility:** snapshot pinning + git_sha is best-in-class.
- **Provider lock-in:** pluggable provider architecture is solid.
- **Schema evolution:** Iceberg handles this natively.
- **MCP exposure for agents:** ahead of most quant teams.
- **Documentation discipline:** ahead of most engineering teams.
- **Single-user-now / SaaS-later modularity:** the frontend plan
  already addresses this with the seam-discipline approach.

---

## 8. The bottom line, again

You have a strong **foundation**. The architecture is right; the
discipline is right; the doc-quality is rare. The data layer,
reproducibility, and agent surface are genuinely state-of-the-art.

You do NOT yet have a **profit-grade system**. The seven gaps
in §3 — risk management, survivorship bias, weak strategy
evidence, no regime context, thin harness tests, no execution
layer, no live observability — are the difference between
"running interesting backtests" and "making money in the market."

The good news: **none of the seven require a redesign**. They're
all additive. The existing plans are correct; they're just
incomplete on the path to profit.

The single weakest current strategy result (the MTF EMA at
-9.75% / Sharpe -1.168) is honestly the most informative thing
in your entire codebase. It says: "the infrastructure works,
the strategy alpha doesn't." Closing the seven gaps above is
the next layer of work that gives an actual strategy a fair
chance to show alpha.

Recommend starting with the risk_management_plan doc this week
before resuming TA-5.0.
