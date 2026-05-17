# Risk Management Plan

The implementation contract for the risk-management layer. This
sits **between** the strategy framework (which produces trading
intents) and any executor (backtest, paper, live), and **refuses**
any intent that violates the configured risk limits.

**Status:** plan only. No code written yet.

**Why this exists:** the [System Review §3 #1](SYSTEM_REVIEW_2026-05-17.md)
identified the absence of risk-management code as the single
highest-urgency profitability-killer. Today the `Portfolio` class
tracks cash, positions, fees, and equity — but it will accept any
trade a strategy asks for, regardless of size, drawdown state, or
exposure concentration. Multi-symbol live deployment without this
layer is a blow-up waiting for the wrong day.

**Companion docs:**
- [trading_subsystem_design.md](trading_subsystem_design.md) — strategy
  framework + Portfolio class that risk wraps.
- [silver_layer_plan.md](silver_layer_plan.md) — data layer feeding
  the volatility / correlation inputs risk needs.
- [SYSTEM_REVIEW_2026-05-17.md](SYSTEM_REVIEW_2026-05-17.md) — the
  review that triggered this plan.
- (future) `docs/execution_plan.md` — paper / live executor that
  consumes risk-checked intents.

---

## 1. The contract — what risk-management does

Risk-management is a **pure decision layer**. It takes:

- A proposed `Action` from a strategy (e.g. BUY 100 shares of AAPL).
- A `PortfolioSnapshot` (current cash, positions, equity curve).
- The current `Bar` for context (price, time, volatility).
- A `RiskPolicy` (the configured rules).

And returns one of three outcomes:

- `Approved(action)` — the action passes; the executor proceeds.
- `Modified(action)` — the action was scaled down (e.g. requested
  $50K position, allowed $20K); the executor proceeds with the
  modified version.
- `Rejected(reason)` — the action was blocked entirely; the
  executor records a `risk_rejected` event but does not execute.

```python
# app/services/risk/risk_manager.py — NEW
class RiskDecision(BaseModel):
    outcome: Literal["approved", "modified", "rejected"]
    action: Optional[Action] = None   # populated on approved/modified
    rejected_reason: Optional[str] = None
    triggered_rules: list[str] = []
    notes: dict[str, Any] = {}

class RiskManager(Protocol):
    def evaluate(
        self,
        action: Action,
        portfolio: PortfolioSnapshot,
        bar: Bar,
    ) -> RiskDecision:
        ...
```

The risk manager is **stateless across calls**. Per-call state
comes in via `portfolio`. Anything that needs persistence (a
kill-switch flag, a daily-DD high-water mark) is held in a
separate stateful object the manager reads.

---

## 2. The rules — what we enforce on day one

Eight rules, ordered by priority. Each is a separate class
implementing a `RiskRule` Protocol; the `RiskManager` composes them
into a `RiskPolicy`.

### 2.1 `KillSwitch` (highest priority)

Single global flag. When tripped, ALL new entries are rejected.
Exits are still allowed (you want to be able to flatten in an
emergency). Set via:

- Config flag (`settings.kill_switch_enabled`)
- MCP tool (`trigger_kill_switch(reason)`) — so an LLM agent
  watching anomalies can self-halt
- HTTP route (`POST /api/risk/kill-switch`) — operator manual
- Automatic on certain rule trips (e.g. `MaxDailyLossHalt`)

Persists across process restarts (stored in CH `risk_state` table).
Manual `reset_kill_switch()` to clear.

### 2.2 `MaxDailyLossHalt`

If portfolio's intra-day loss exceeds `max_daily_loss_pct` of
start-of-day equity, trip the kill switch and reject all entries.

```python
@dataclass
class MaxDailyLossHalt(RiskRule):
    max_daily_loss_pct: float = 0.05  # 5% intra-day loss

    def evaluate(self, action, portfolio, bar):
        sod_equity = portfolio.start_of_day_equity
        current_equity = portfolio.equity
        intraday_loss_pct = (sod_equity - current_equity) / sod_equity
        if intraday_loss_pct > self.max_daily_loss_pct:
            return Rejected("daily loss halt; kill switch tripped")
```

Side effect: trips the global kill switch (so the next call sees
KillSwitch fire first).

### 2.3 `MaxDrawdownHalt`

Same idea but on the peak-to-trough equity curve, not just
intraday. If portfolio drawdown exceeds `max_drawdown_pct`, trip
the kill switch. Requires manual reset.

### 2.4 `MaxPositionSize`

Position cannot exceed `pct_equity_max` × current equity.

```python
@dataclass
class MaxPositionSize(RiskRule):
    pct_equity_max: float = 0.10  # 10% of equity per position

    def evaluate(self, action, portfolio, bar):
        if action.kind != "BUY":
            return Approved(action)  # closes always allowed
        proposed_value = action.qty * bar.close
        max_value = portfolio.equity * self.pct_equity_max
        if proposed_value > max_value:
            # Modify down rather than reject — let the trade happen
            # at the safe size.
            scaled_qty = max_value / bar.close
            return Modified(action.with_qty(scaled_qty))
        return Approved(action)
```

### 2.5 `MaxSymbolConcentration`

A single symbol's total position cannot exceed `pct_equity_max` of
equity (counts existing position + this proposed add).

### 2.6 `MaxLeverage`

Total position notional (sum across all symbols) cannot exceed
`max_leverage_ratio` × cash + equity. Default: 1.0 (no leverage).
For now we don't support leverage; the rule exists so when we add
it later, the seam is in place.

### 2.7 `VolatilityScaledSizing`

Optional sizer that **modifies** position size based on the symbol's
recent volatility (via ATR). Doesn't reject; just modifies down for
volatile names.

```python
@dataclass
class VolatilityScaledSizing(RiskRule):
    target_risk_per_trade_pct: float = 0.01  # 1% of equity at risk per trade
    atr_period: int = 14
    atr_stop_multiplier: float = 2.0

    def evaluate(self, action, portfolio, bar):
        if action.kind != "BUY":
            return Approved(action)
        atr = bar.atr  # computed by Context.indicator("atr", period=14)
        if atr is None or atr <= 0:
            return Approved(action)  # can't compute; pass through
        risk_dollars = portfolio.equity * self.target_risk_per_trade_pct
        stop_distance_dollars = atr * self.atr_stop_multiplier
        safe_qty = risk_dollars / stop_distance_dollars
        if safe_qty < action.qty:
            return Modified(action.with_qty(safe_qty))
        return Approved(action)
```

This is the Kelly-criterion-flavored sizer that real systems use.
The formula says: "for an N% risk budget per trade, with a stop set
N×ATR below entry, the max position size is `risk_budget /
stop_distance`."

### 2.8 `CooldownPeriod`

After a position is closed (whether profitable or losing), refuse
to re-enter the same symbol for `cooldown_minutes`. Prevents
strategy thrashing and the classic "ping-pong" failure mode.

---

## 3. The policy — composition

A `RiskPolicy` is just an ordered list of rules. The manager calls
each in order; first `Rejected` short-circuits, `Modified` updates
the action carried forward, `Approved` is the pass-through.

```python
@dataclass
class RiskPolicy:
    rules: list[RiskRule]

class StandardRiskManager:
    def __init__(self, policy: RiskPolicy, state: RiskState):
        self.policy = policy
        self.state = state  # holds kill-switch flag, sod_equity, etc.

    def evaluate(self, action, portfolio, bar) -> RiskDecision:
        current = action
        triggered = []
        for rule in self.policy.rules:
            result = rule.evaluate(current, portfolio, bar)
            triggered.append(rule.__class__.__name__)
            if result.outcome == "rejected":
                return RiskDecision(
                    outcome="rejected",
                    rejected_reason=result.reason,
                    triggered_rules=triggered,
                )
            if result.outcome == "modified":
                current = result.action
        return RiskDecision(
            outcome="modified" if current != action else "approved",
            action=current,
            triggered_rules=triggered,
        )
```

The default policy (the `StandardRiskPolicy`) wires all 8 rules in
priority order. Backtest configs can override; live deployment must
use the standard policy or stricter.

```python
# Default settings
StandardRiskPolicy = RiskPolicy(rules=[
    KillSwitch(),                                         # halt-all check
    MaxDailyLossHalt(max_daily_loss_pct=0.05),            # 5% daily DD halt
    MaxDrawdownHalt(max_drawdown_pct=0.20),               # 20% peak-DD halt
    MaxLeverage(max_leverage_ratio=1.0),                  # no leverage
    MaxSymbolConcentration(pct_equity_max=0.20),          # 20% per symbol
    MaxPositionSize(pct_equity_max=0.10),                 # 10% per trade
    VolatilityScaledSizing(target_risk_per_trade_pct=0.01),  # 1% per trade risk
    CooldownPeriod(cooldown_minutes=30),                  # 30m cooldown
])
```

---

## 4. State persistence

`RiskState` holds the data that must survive a process restart:

```python
# app/db/risk_state.py — NEW (ClickHouse table)
class RiskState(BaseModel):
    kill_switch_tripped: bool = False
    kill_switch_reason: Optional[str] = None
    kill_switch_tripped_at: Optional[datetime] = None
    start_of_day_equity: float = 0.0
    start_of_day_date: Optional[date] = None
    peak_equity_observed: float = 0.0
    last_trade_close_per_symbol: dict[str, datetime] = {}
```

Stored in CH `risk_state` (one row per `(account_id, date)` for
audit history). The manager reads it on init, writes on every
state change.

---

## 5. Integration with existing code

Five integration points; each is additive.

### 5.1 `Backtester` — wrap Portfolio with RiskManager

```python
# Today
self.portfolio.apply(action, current_bar, next_bar, fees, slippage)

# Future
decision = self.risk_manager.evaluate(action, self.portfolio.snapshot(), current_bar)
if decision.outcome == "rejected":
    self._record_risk_rejection(decision)
    continue  # skip; don't apply the action
self.portfolio.apply(decision.action, current_bar, next_bar, fees, slippage)
```

`BacktestConfig` gets a `risk_policy: RiskPolicyName = "standard"` field.

### 5.2 Strategy `Context` — expose risk state read-only

Strategies should be able to see whether they're near a limit
(e.g. "I'm at 9.5% concentration on AAPL; this BUY would hit 11%").
Currently the `Portfolio` snapshot is on `Context`. We add:

```python
ctx.risk.would_approve(action) -> RiskDecision  # dry-run check
ctx.risk.headroom("position_size", symbol="AAPL") -> float  # how much room
```

This lets strategies be polite (size down voluntarily) rather than
fight the risk layer. Doesn't replace the layer — it complements
it.

### 5.3 New MCP tools — for LLM agent self-control

- `get_risk_state()` — current kill-switch state + recent rejections.
- `trigger_kill_switch(reason)` — operator/agent halt.
- `reset_kill_switch()` — operator-only (audit log entry required).
- `simulate_action_under_risk(action, account)` — dry-run check
  before commiting.

### 5.4 HTTP routes

- `GET /api/risk/state` — current state (read-only).
- `POST /api/risk/kill-switch` — trip; requires `reason` body.
- `DELETE /api/risk/kill-switch` — reset; operator-only.
- `GET /api/risk/policy` — show the active policy + rule params.
- `POST /api/risk/dry-run` — `(action, snapshot)` → decision; useful
  for the cockpit "would this be approved?" preview.

### 5.5 Cockpit Status page

- 🛑 Kill-switch indicator (red when tripped).
- Daily P&L bar with halt-threshold line.
- Per-symbol concentration mini-bars (which positions are near
  the 20% limit).
- Risk-rejection counter (today / week / month).

---

## 6. Audit & observability

Every risk decision writes one row to CH `risk_decisions`:

| Column | Type |
|---|---|
| ts | DateTime64 |
| symbol | String |
| account_id | String |
| outcome | Enum8('approved', 'modified', 'rejected') |
| triggered_rules | Array(String) |
| proposed_qty | Float64 |
| approved_qty | Float64 (nullable) |
| portfolio_equity | Float64 |
| portfolio_cash | Float64 |
| rejected_reason | String (nullable) |
| notes_json | String |

This is the **ground-truth audit log** for "why didn't the system
take this trade?" Searchable from the cockpit (FE-1 Status page
exposes a "recent risk decisions" table).

In SaaS mode later, this becomes per-tenant evidence for the SOC2
audit (frontend_plan.md §7.7).

---

## 7. Testing strategy

Per the System Review §3 #5, the absence of dedicated harness
tests is a profitability risk. Risk-management tests **must**
land before any execution code that depends on them.

### 7.1 Unit tests per rule

`tests/test_risk_rules.py` — one test class per rule. For each:
- Approves under normal conditions.
- Rejects/modifies at threshold boundary (exactly at, just over).
- Handles edge cases (zero equity, negative cash, no ATR, etc.).
- Idempotent for repeated calls with identical inputs.

### 7.2 Composition tests

`tests/test_risk_policy.py` — verify the priority order. E.g.
KillSwitch trips before MaxPositionSize runs.

### 7.3 State persistence tests

`tests/test_risk_state.py` — kill switch survives a `RiskManager`
re-instantiation; start-of-day equity rolls correctly at midnight
ET; per-symbol cooldowns clear after the window.

### 7.4 Adversarial integration tests

`tests/test_risk_integration.py` — backtest configs designed to
trip each rule:
- "BUY 10000 shares of AAPL on a $40k account" → `MaxPositionSize`
  modifies to small qty.
- "BUY 1 share every minute" → `CooldownPeriod` rejects after first
  exit.
- "Strategy that loses 10% in a day" → `MaxDailyLossHalt` trips;
  subsequent BUYs rejected; SELLs allowed.

### 7.5 Reproducibility

Same `(BacktestConfig, RiskPolicy)` triple produces the same
`risk_decisions` log byte-identical. Pinned by
`tests/test_risk_reproducibility.py`.

---

## 8. Phasing

### Phase TA-R.1 — Risk Manager scaffold (2 days)

- `app/services/risk/` package + README.
- `schemas.py` — `Action`, `RiskDecision`, `RiskPolicy`, `RiskState`
  Pydantic models.
- `contract.py` — `RiskRule` Protocol, `RiskManager` Protocol.
- `manager.py` — `StandardRiskManager` (composition).
- `state.py` — `RiskState` persistence (CH `risk_state` table).
- Empty rule slots — `rules/__init__.py` re-exports.

**Gate:** scaffold imports cleanly; `RiskState` round-trips through
CH; `RiskManager` with zero rules returns `Approved` for any input.

### Phase TA-R.2 — The 8 rules + composition (3-4 days)

- One file per rule under `app/services/risk/rules/`.
- Unit tests per rule.
- Standard policy in `app/services/risk/policy.py`.
- Composition tests.

**Gate:** all 8 rules implement the `RiskRule` Protocol; standard
policy unit-tested; coverage report shows >95% line coverage on
the risk package.

### Phase TA-R.3 — Backtester integration (2 days)

- `BacktestConfig` gets `risk_policy` field.
- `Backtester.run` wraps each action through `RiskManager.evaluate`.
- `RunResult` gets `risk_rejections` count + `risk_decisions` log.
- Existing backtest configs work unchanged (default to standard
  policy; SMA-canary etc. continue to pass).

**Gate:** the existing bake-off (sma_crossover, ema_crossover,
rsi_reversion, bollinger_mean_revert, mtf_ema) re-runs with risk
enabled; same Sharpe (within rounding) because none hit the limits;
`risk_rejections == 0` on all runs.

### Phase TA-R.4 — Adversarial integration tests (2 days)

- Synthetic backtest configs designed to trip each rule.
- Verify the system holds up under intentional misbehavior.

**Gate:** each rule has at least one adversarial test that
demonstrates the rule firing as expected on synthetic input.

### Phase TA-R.5 — HTTP + MCP surfaces (1 day)

- Routes per §5.4.
- MCP tools per §5.3.
- Cockpit Status page integration (per [frontend_plan.md §5.1](frontend_plan.md))
  comes when FE-1 lands; not blocked on risk.

**Gate:** can dry-run an action via `POST /api/risk/dry-run`; can
trip + reset the kill switch via HTTP; MCP `list_tools` includes
the new ones.

### Phase TA-R.6 — Decision log + observability (1 day)

- `risk_decisions` CH table + writer in `RiskManager.evaluate`.
- `audit_events` (from frontend_plan SaaS-readiness work) gets
  risk-decision entries.
- Morning-brief includes "yesterday's risk rejections" count.

**Gate:** for any backtest re-run, the `risk_decisions` log
contains one row per evaluated action; queries by `outcome`,
`symbol`, `triggered_rules` work cleanly.

**Total: ~10-12 days** for the full risk-management layer.

---

## 9. Reproducibility & determinism

- The `RiskManager.evaluate` method is **pure**: same `(action,
  portfolio_snapshot, bar, policy_params)` → same decision,
  byte-identical, every time.
- `RiskState` is the only stateful object; it's captured in
  `RunResult.risk_state_snapshot` for replay.
- Re-running a backtest produces an identical `risk_decisions`
  log. This is the test gate.
- The cooldown / kill-switch / daily-loss-halt state is
  bar-deterministic: the rules read the timestamp from the
  current bar (not from `datetime.now()`), so replays work.

---

## 10. Risks & open questions

### What if a rule is wrong?

Risk rules can have bugs. A bad `MaxPositionSize` calculation could
reject legitimate trades or approve oversized ones. Defense:
- Per-rule unit tests with adversarial inputs (§7.1).
- The composition is **fail-safe**: a rule that crashes counts as
  Rejected (the safer default). Logged as `risk_rule_crashed` for
  investigation.
- Operator can disable any rule via config without redeploying.

### Live execution before risk is built?

**No.** The hard rule: no action of any kind reaches a paper or
live `Executor` without going through `RiskManager.evaluate` first.
This is enforced architecturally — `Executor.submit_order` takes
a `RiskDecision`, not an `Action`. If the seam doesn't exist, the
type system refuses to compile.

### Conflict with strategy intent?

A strategy says BUY 1000 shares; risk says you can do 100. The
strategy might want to know — should it size up? Hold the rejected
intent in a queue? Just take the 100 and move on?

Decision: **Modified** decisions are the path. The strategy gets
the original intent; the executor takes the modified size; the
log shows both. Strategies that want to negotiate further can use
`ctx.risk.headroom()` to size their initial request appropriately.

### What about gaps / market-on-open?

Overnight gaps can blow through stops. The risk layer doesn't
solve this — it's an execution concern (use bracket orders,
GTC stop-loss orders). Risk layer assumes stops are honored;
when execution is built, we ensure the seam supports
broker-side stop orders.

### Latency on hot path?

`RiskManager.evaluate` runs once per action; actions are at most
once per bar per symbol. At 100 symbols × 1m bars, that's ~100
calls/min. Each call is microseconds. Not a hot-path concern.

---

## 11. Decisions deferred until we hit them

1. **Per-tenant policies** (SaaS) — different plans get different
   defaults (Pro tier allows leverage; Free tier doesn't).
   Decided during the SaaS-flip phase (frontend_plan §11).
2. **Margin / short selling** — out of scope for TA-R; tracked as
   future phase TA-R.7+. Today's `MaxLeverage` rule explicitly
   sets `max_leverage_ratio = 1.0` so the seam exists.
3. **Options risk** (theta, vega, gamma exposures) — not on the
   current roadmap. The architecture doesn't preclude it; the
   rule list would expand.
4. **Cross-account correlation** — for now we assume one account.
   When we add multi-account, the rules generalize naturally
   (`MaxPositionSize` becomes per-account).

---

## 12. Where this fits in the overall roadmap

Insertion in [trading_subsystem_design.md §10](trading_subsystem_design.md):

```
TA-4.3  Screener (LANDED)
TA-5.0  Silver corp-actions (next, in-flight)
TA-R.1  Risk Manager scaffold       ← THIS PLAN
TA-R.2  The 8 rules
TA-5.1  Silver build job
TA-R.3  Backtester integration
TA-5.2  SilverReader + reads-flip
TA-R.4  Adversarial tests
TA-5.3  silver_to_ch backfill
TA-R.5  HTTP + MCP surfaces
TA-5.4  Shadow validation
TA-R.6  Decision log + observability
TA-5.5  Retire provider-REST paths
TA-6    TA indicator gap-fill
TA-7    Gold features
TA-8    Universe history (promoted from later — needed for OOS)
TA-9    OOS bake-off harness
TA-10   Regime classifier
TA-11   Execution layer (Executor Protocol + Paper)
TA-12   Observability infra (Prometheus + Sentry + morning brief)
TA-RL   RL agent (depends on all of the above)
TA-Live Paper → live execution
```

TA-R interleaves with TA-5 because the two are independent and
each is small enough to ship in parallel. Both are pre-requisites
for any execution code (TA-11+).

---

## 13. The single highest-leverage thing this plan delivers

> **A backtest result you can trust to live-deploy.**

Today's bake-off (`ema_crossover` Sharpe 0.933) is single-symbol,
single-window, no risk management. Even if the Sharpe survives
out-of-sample testing (the OOS bake-off harness — TA-9), deploying
it live without risk management = catastrophic on the first
multi-symbol day.

With this layer in place, **any** strategy run produces a
backtest result that:
- Survives forced position sizing (won't take a 50% position
  even if the strategy asks).
- Survives intra-day loss limits (won't keep adding into a -10%
  day).
- Survives concentration limits (won't put 80% in one ticker).
- Survives cooldowns (won't ping-pong in/out of a losing trade).

The Sharpe number from a risk-managed backtest is the number we
can actually deploy capital against.
