# Simulation strategies

Strategies turn a per-bar simulation `Context` into an `Action`. Rule-based,
LLM, and future agent strategies share the contract in
[`../strategy.py`](../strategy.py).

Strategies must preserve deterministic replay, declare their name/version and
required intervals, avoid direct database/provider access, and never use future
bars. New strategy unit tests live in [`tests/`](tests/) with synthetic inputs.

Read [`../../../../docs/standards/trading_subsystem.md`](../../../../docs/standards/trading_subsystem.md)
before editing this package.
