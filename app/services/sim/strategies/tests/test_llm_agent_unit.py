"""
Unit tests for LLMAgentStrategy.

We stub the Anthropic client so tests never make real API calls.
The strategy's interesting behavior — prompt construction, response
caching, parse-failure → hold, cost-bounded calls during warmup,
deterministic re-runs from cache — all gets covered without burning
a single token.

Live integration (against the real Anthropic API) lives in
`tests/integration/test_llm_agent_real.py` and is skipped without
an `ANTHROPIC_API_KEY` env var.
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.services.sim.context import Context
from app.services.sim.schemas import (
    BacktestConfig,
    PortfolioSnapshot,
    Position,
    hold,
)
from app.services.sim.strategies.llm_agent import (
    IndicatorSpec,
    LLMAgentParams,
    LLMAgentStrategy,
    _cache_key,
    _extract_json_object,
    _ResponseCache,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


class _SyntheticBar:
    def __init__(self, symbol, ts, open_, high, low, close, volume=1000.0):
        self.symbol = symbol
        self.timestamp = ts
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume


def _bars(symbol: str, closes: list[float]):
    base = datetime(2024, 8, 1, tzinfo=timezone.utc)
    return [
        _SyntheticBar(
            symbol=symbol, ts=base + timedelta(days=i),
            open_=c, high=c * 1.005, low=c * 0.995, close=c, volume=10_000,
        )
        for i, c in enumerate(closes)
    ]


def _config() -> BacktestConfig:
    return BacktestConfig(
        symbols=["TEST"],
        start=datetime(2024, 8, 1, tzinfo=timezone.utc),
        end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        interval="1d",
        starting_cash=10_000.0,
        history_window=100,
    )


def _empty_snapshot(cash: float = 10_000.0) -> PortfolioSnapshot:
    return PortfolioSnapshot(cash=cash, equity=cash)


def _snapshot_with_position(qty: float, avg_price: float = 100.0, cash: float = 5_000.0) -> PortfolioSnapshot:
    pos = Position(
        symbol="TEST", quantity=qty, avg_entry_price=avg_price,
        entry_time=datetime(2024, 8, 1, tzinfo=timezone.utc),
    )
    return PortfolioSnapshot(cash=cash, equity=cash + qty * avg_price, positions={"TEST": pos})


def _stub_client(response_text: str) -> MagicMock:
    """Build an Anthropic-shaped stub that returns the given text."""
    block = MagicMock()
    block.text = response_text
    resp = MagicMock()
    resp.content = [block]
    client = MagicMock()
    client.messages.create.return_value = resp
    return client


def _make_strategy(
    cache_path: str,
    *,
    client: Any = None,
    params: LLMAgentParams | None = None,
) -> LLMAgentStrategy:
    p = params or LLMAgentParams(
        context_bars=5,
        indicators=[IndicatorSpec(name="sma", params={"period": 3})],
        cache_path=cache_path,
    )
    return LLMAgentStrategy(params=p, client=client)


def _warmup(ctx: Context, n: int, start_at: int = 100):
    """Advance ctx through `n` bars so warmup completes."""
    bars = _bars("TEST", [float(start_at + i) for i in range(n)])
    for bar in bars:
        ctx.advance(bar, _empty_snapshot())


# ─────────────────────────────────────────────────────────────────────
# Helpers — JSON extraction + cache-key determinism
# ─────────────────────────────────────────────────────────────────────


def test_extract_json_object_strict() -> None:
    obj = _extract_json_object('{"action": "buy", "size_pct": 0.95}')
    assert obj == {"action": "buy", "size_pct": 0.95}


def test_extract_json_object_with_prose_around() -> None:
    text = 'Sure, here is my decision: {"action": "hold", "size_pct": 0.0, "rationale": "ambiguous"}\n— done.'
    obj = _extract_json_object(text)
    assert obj == {"action": "hold", "size_pct": 0.0, "rationale": "ambiguous"}


def test_extract_json_object_returns_none_on_garbage() -> None:
    assert _extract_json_object("no json here") is None
    assert _extract_json_object("") is None
    assert _extract_json_object("{ malformed") is None


def test_cache_key_is_deterministic() -> None:
    k1 = _cache_key("claude-x", "sys-prompt", "user-prompt")
    k2 = _cache_key("claude-x", "sys-prompt", "user-prompt")
    assert k1 == k2
    # Same content, different model -> different key.
    assert _cache_key("claude-y", "sys-prompt", "user-prompt") != k1


# ─────────────────────────────────────────────────────────────────────
# Response cache (SQLite-backed)
# ─────────────────────────────────────────────────────────────────────


def test_response_cache_persists_to_sqlite(tmp_path: Path) -> None:
    cache_path = str(tmp_path / "cache.sqlite")
    c = _ResponseCache(cache_path)
    c.put("k1", '{"action":"buy"}')
    assert c.get("k1") == '{"action":"buy"}'
    c.close()

    # Reopen — value still there.
    c2 = _ResponseCache(cache_path)
    assert c2.get("k1") == '{"action":"buy"}'
    c2.close()


def test_response_cache_get_miss_returns_none(tmp_path: Path) -> None:
    c = _ResponseCache(str(tmp_path / "cache.sqlite"))
    assert c.get("nope") is None
    c.close()


# ─────────────────────────────────────────────────────────────────────
# Warmup + cost budget
# ─────────────────────────────────────────────────────────────────────


def test_llm_agent_holds_during_warmup_without_api_call(tmp_path: Path) -> None:
    """First N bars (< context_bars) -> hold, never call the LLM."""
    client = _stub_client('{"action":"buy","size_pct":0.95}')
    strat = _make_strategy(str(tmp_path / "c.sqlite"), client=client)

    ctx = Context(config=_config())
    strat.setup(ctx)

    # Only feed 3 bars (context_bars=5) — every call must hold.
    for bar in _bars("TEST", [100.0, 101.0, 102.0]):
        ctx.advance(bar, _empty_snapshot())
        action = strat.on_bar(ctx)
        assert action.kind == "hold"

    client.messages.create.assert_not_called()
    strat.teardown(ctx)


def test_llm_agent_calls_api_once_per_bar_after_warmup(tmp_path: Path) -> None:
    client = _stub_client('{"action":"hold","size_pct":0.0,"rationale":"sit tight"}')
    strat = _make_strategy(str(tmp_path / "c.sqlite"), client=client)

    ctx = Context(config=_config())
    strat.setup(ctx)

    # 8 bars: first 5 are warmup → hold, no call. Bars 6-8 → 3 calls.
    closes = list(range(100, 110))  # 10 bars (well past warmup of 5)
    for bar in _bars("TEST", [float(c) for c in closes]):
        ctx.advance(bar, _empty_snapshot())
        strat.on_bar(ctx)

    # Warmup is max(context_bars=5, slowest_indicator+1=4) = 5.
    # Bars 0..3 (4 bars) hold; bars 4..9 (6 bars) -> 6 API calls.
    assert client.messages.create.call_count == 6
    assert strat._stats.api_calls == 6
    assert strat._stats.cache_hits == 0
    strat.teardown(ctx)


# ─────────────────────────────────────────────────────────────────────
# Caching across runs
# ─────────────────────────────────────────────────────────────────────


def test_llm_agent_hits_cache_on_replay(tmp_path: Path) -> None:
    """Second run with same config + same data -> all cache hits, no API calls."""
    cache_path = str(tmp_path / "shared_cache.sqlite")
    closes = [float(c) for c in range(100, 110)]

    # Run 1: populates cache.
    client_1 = _stub_client('{"action":"hold","size_pct":0.0}')
    strat_1 = _make_strategy(cache_path, client=client_1)
    ctx_1 = Context(config=_config())
    strat_1.setup(ctx_1)
    for bar in _bars("TEST", closes):
        ctx_1.advance(bar, _empty_snapshot())
        strat_1.on_bar(ctx_1)
    strat_1.teardown(ctx_1)
    run1_calls = client_1.messages.create.call_count
    assert run1_calls > 0  # at least one actionable bar

    # Run 2: fresh client, fresh strategy, same cache. Same prompts -> all hits.
    client_2 = _stub_client('{"action":"buy","size_pct":1.0}')  # different stub, must not be used
    strat_2 = _make_strategy(cache_path, client=client_2)
    ctx_2 = Context(config=_config())
    strat_2.setup(ctx_2)
    for bar in _bars("TEST", closes):
        ctx_2.advance(bar, _empty_snapshot())
        strat_2.on_bar(ctx_2)
    strat_2.teardown(ctx_2)

    assert client_2.messages.create.call_count == 0  # ALL cache hits
    assert strat_2._stats.cache_hits == run1_calls
    assert strat_2._stats.api_calls == 0


# ─────────────────────────────────────────────────────────────────────
# Action emission
# ─────────────────────────────────────────────────────────────────────


def test_llm_agent_buy_when_flat(tmp_path: Path) -> None:
    """LLM says BUY + we hold no position -> Action(buy, size>0)."""
    client = _stub_client('{"action":"buy","size_pct":0.95,"rationale":"trend up"}')
    strat = _make_strategy(str(tmp_path / "c.sqlite"), client=client)

    ctx = Context(config=_config())
    strat.setup(ctx)
    for bar in _bars("TEST", [float(c) for c in range(100, 108)]):
        ctx.advance(bar, _empty_snapshot())
    action = strat.on_bar(ctx)

    assert action.kind == "buy"
    assert action.symbol == "TEST"
    assert action.size > 0
    assert "trend up" in action.note
    strat.teardown(ctx)


def test_llm_agent_buy_ignored_when_already_long(tmp_path: Path) -> None:
    """LLM says BUY + we already hold -> hold (no doubling up)."""
    client = _stub_client('{"action":"buy","size_pct":0.95}')
    strat = _make_strategy(str(tmp_path / "c.sqlite"), client=client)

    ctx = Context(config=_config())
    strat.setup(ctx)
    snap = _snapshot_with_position(qty=50.0, avg_price=100.0, cash=5000.0)
    for bar in _bars("TEST", [float(c) for c in range(100, 108)]):
        ctx.advance(bar, snap)
    action = strat.on_bar(ctx)

    assert action.kind == "hold"
    strat.teardown(ctx)


def test_llm_agent_sell_with_position(tmp_path: Path) -> None:
    """LLM says SELL + we hold -> Action(sell, full position)."""
    client = _stub_client('{"action":"sell","size_pct":1.0,"rationale":"exit"}')
    strat = _make_strategy(str(tmp_path / "c.sqlite"), client=client)

    ctx = Context(config=_config())
    strat.setup(ctx)
    snap = _snapshot_with_position(qty=50.0, avg_price=100.0)
    for bar in _bars("TEST", [float(c) for c in range(100, 108)]):
        ctx.advance(bar, snap)
    action = strat.on_bar(ctx)

    assert action.kind == "sell"
    assert action.size == 50.0
    strat.teardown(ctx)


def test_llm_agent_sell_ignored_when_flat(tmp_path: Path) -> None:
    """LLM says SELL + no position -> hold."""
    client = _stub_client('{"action":"sell","size_pct":1.0}')
    strat = _make_strategy(str(tmp_path / "c.sqlite"), client=client)

    ctx = Context(config=_config())
    strat.setup(ctx)
    for bar in _bars("TEST", [float(c) for c in range(100, 108)]):
        ctx.advance(bar, _empty_snapshot())
    action = strat.on_bar(ctx)
    assert action.kind == "hold"
    strat.teardown(ctx)


# ─────────────────────────────────────────────────────────────────────
# Error paths — degrade to hold(), never crash
# ─────────────────────────────────────────────────────────────────────


def test_llm_agent_parse_failure_degrades_to_hold(tmp_path: Path) -> None:
    """LLM returns garbage -> hold + parse_failures stat increments."""
    client = _stub_client("This is not JSON at all.")
    strat = _make_strategy(str(tmp_path / "c.sqlite"), client=client)

    ctx = Context(config=_config())
    strat.setup(ctx)
    for bar in _bars("TEST", [float(c) for c in range(100, 108)]):
        ctx.advance(bar, _empty_snapshot())
    action = strat.on_bar(ctx)

    assert action.kind == "hold"
    assert strat._stats.parse_failures >= 1
    strat.teardown(ctx)


def test_llm_agent_api_failure_degrades_to_hold(tmp_path: Path) -> None:
    """API raises -> hold + api_failures stat increments + nothing cached."""
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("rate limited")
    strat = _make_strategy(str(tmp_path / "c.sqlite"), client=client)

    ctx = Context(config=_config())
    strat.setup(ctx)
    for bar in _bars("TEST", [float(c) for c in range(100, 108)]):
        ctx.advance(bar, _empty_snapshot())
    action = strat.on_bar(ctx)
    assert action.kind == "hold"
    assert strat._stats.api_failures >= 1
    # The bad call shouldn't be cached — next run will retry.
    assert strat._cache.get(_cache_key(
        strat.params.model, strat.params.system_prompt,
        strat._build_user_prompt(ctx),
    )) is None
    strat.teardown(ctx)


def test_llm_agent_clamps_buy_size_pct_to_max(tmp_path: Path) -> None:
    """LLM says size_pct=10.0 but position_size_pct=0.95 caps it."""
    client = _stub_client('{"action":"buy","size_pct":10.0}')
    strat = _make_strategy(str(tmp_path / "c.sqlite"), client=client)
    strat.params = LLMAgentParams(
        context_bars=5,
        indicators=[IndicatorSpec(name="sma", params={"period": 3})],
        position_size_pct=0.50,  # tight cap
        cache_path=str(tmp_path / "c.sqlite"),
    )

    ctx = Context(config=_config())
    strat.setup(ctx)
    snap = PortfolioSnapshot(cash=10_000.0, equity=10_000.0)
    for bar in _bars("TEST", [float(c) for c in range(100, 108)]):
        ctx.advance(bar, snap)
    action = strat.on_bar(ctx)

    assert action.kind == "buy"
    # 10000 * 0.50 / 107 = ~46.7 → floor to 46.
    assert 40 <= action.size <= 50
    strat.teardown(ctx)


# ─────────────────────────────────────────────────────────────────────
# Reproducibility
# ─────────────────────────────────────────────────────────────────────


def test_llm_agent_replay_produces_identical_decisions(tmp_path: Path) -> None:
    """
    Two strategy instances sharing one cache file + the same bar
    stream produce IDENTICAL action sequences. Reproducibility of
    LLM strategies is contingent on the cache; this test pins it.
    """
    cache_path = str(tmp_path / "shared.sqlite")
    closes = [float(c) for c in range(100, 115)]

    # Run 1 — alternating responses on each call (deterministic via patching).
    response_sequence = [
        '{"action":"hold","size_pct":0.0}',
        '{"action":"buy","size_pct":0.95}',
        '{"action":"hold","size_pct":0.0}',
        '{"action":"sell","size_pct":1.0}',
        '{"action":"hold","size_pct":0.0}',
        '{"action":"buy","size_pct":0.95}',
    ] * 5  # plenty of responses for either run
    client_1 = _stub_client_sequence(response_sequence)
    strat_1 = _make_strategy(cache_path, client=client_1)
    actions_1 = _run_collecting_actions(strat_1, closes)

    # Run 2 — fresh client; cache hits guarantee identical actions.
    client_2 = _stub_client_sequence(["NEVER_USED"] * 100)
    strat_2 = _make_strategy(cache_path, client=client_2)
    actions_2 = _run_collecting_actions(strat_2, closes)

    assert actions_1 == actions_2
    assert client_2.messages.create.call_count == 0  # confirms cache hits


def _stub_client_sequence(responses: list[str]) -> MagicMock:
    """Stub Anthropic client where successive calls return the next response in `responses`."""
    iterator = iter(responses)

    def _create(**kwargs):
        block = MagicMock()
        block.text = next(iterator)
        resp = MagicMock()
        resp.content = [block]
        return resp

    client = MagicMock()
    client.messages.create.side_effect = _create
    return client


def _run_collecting_actions(strat: LLMAgentStrategy, closes: list[float]) -> list[tuple]:
    ctx = Context(config=_config())
    strat.setup(ctx)
    out: list[tuple] = []
    for bar in _bars("TEST", closes):
        ctx.advance(bar, _empty_snapshot())
        a = strat.on_bar(ctx)
        out.append((a.kind, a.symbol, a.size))
    strat.teardown(ctx)
    return out
