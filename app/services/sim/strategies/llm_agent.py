"""
LLM-driven trading strategy — Phase TA-2 deliverable.

Wraps an Anthropic Claude model behind the `Strategy` Protocol. On
every bar past warmup, builds a context prompt from recent bars +
configured indicators + the portfolio snapshot, asks Claude for a
JSON-shaped decision, and translates the response into an `Action`.

Why this design:

  - **Same Strategy Protocol as rule-based.** The backtester doesn't
    know `on_bar` calls an LLM; the strategy is just another
    interchangeable component. This is the modularity contract
    (`feedback_trading_subsystem_design`) made concrete.

  - **Response caching keyed on the prompt hash.** Same (model +
    system prompt + user prompt) → identical response, fetched from
    a local SQLite file. Replaying a backtest costs zero API dollars
    after the first run. Critical for cost control and
    reproducibility (without it, "is this strategy improving" would
    cost real money to answer).

  - **Cost-bounded by construction.** Strategy holds during warmup
    (no LLM call), so N bars → at most N - warmup API calls. With
    cache hits, replays of an already-run backtest produce zero new
    calls.

  - **Errors degrade to HOLD.** API failure, parse failure, missing
    key, malformed JSON — all log a warning and emit `hold()`. A
    bad LLM response is a data condition, not a server bug; the
    backtest continues so we still get a measurable run.

  - **Deterministic by default.** `temperature=0.0` removes
    randomness; combined with response caching, the same strategy +
    same config produces the same agent_runs row twice in a row.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from pydantic import BaseModel, Field, field_validator

from app.services.sim.context import Context
from app.services.sim.schemas import Action, hold

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Params
# ─────────────────────────────────────────────────────────────────────


# Latest Claude family per the harness's knowledge cutoff (2026-01).
# Override in YAML configs as new models ship.
_DEFAULT_MODEL = "claude-sonnet-4-6"


class IndicatorSpec(BaseModel):
    """One indicator the strategy includes in the LLM context."""

    name: str = Field(..., description="Registry name: 'sma', 'ema', 'rsi', 'macd', 'tsi'.")
    params: dict[str, Any] = Field(default_factory=dict, description="Keyword args for the indicator constructor.")
    label: str | None = Field(
        None,
        description=(
            "Display label in the prompt. Defaults to a stringified "
            "name+params (e.g. 'sma(period=20)')."
        ),
    )


class LLMAgentParams(BaseModel):
    """Configuration for `LLMAgentStrategy`. Serialized into agent_runs."""

    model: str = Field(_DEFAULT_MODEL, description="Anthropic model identifier.")
    context_bars: int = Field(
        30, ge=2, le=500,
        description="How many recent bars (including current) to include in the prompt.",
    )
    indicators: list[IndicatorSpec] = Field(
        default_factory=lambda: [
            IndicatorSpec(name="sma", params={"period": 20}, label="SMA(20)"),
            IndicatorSpec(name="sma", params={"period": 50}, label="SMA(50)"),
            IndicatorSpec(name="rsi", params={"period": 14}, label="RSI(14)"),
        ],
        description="Indicators computed + included in the prompt.",
    )
    system_prompt: str = Field(
        default=(
            "You are a disciplined trading assistant evaluating ONE symbol at ONE point in time.\n"
            "You will be given recent price history, indicator values, and the current portfolio state.\n"
            "Respond with EXACTLY ONE JSON object on a single line, no prose around it, of the form:\n"
            '  {"action": "buy" | "sell" | "hold", "size_pct": 0.0 to 1.0, "rationale": "<one short sentence>"}\n\n'
            "Rules:\n"
            "- Only suggest BUY when no position is currently held.\n"
            "- Only suggest SELL when a position IS currently held.\n"
            "- size_pct on BUY: fraction of available cash to deploy (0.95 leaves headroom for fees).\n"
            "- size_pct on SELL: fraction of the position to close (1.0 = exit fully).\n"
            "- size_pct on HOLD: ignored (use 0.0).\n"
            "- HOLD when in doubt or when the signal is ambiguous.\n"
            "- Do NOT use outside knowledge of what happens after this bar.\n"
        ),
        description="System prompt sent on every call. Part of the cache key.",
    )
    temperature: float = Field(0.0, ge=0.0, le=1.0)
    max_tokens: int = Field(256, ge=16, le=4096)
    position_size_pct: float = Field(
        0.95, gt=0.0, le=1.0,
        description="Hard cap on `size_pct` for BUY decisions. Belt-and-suspenders.",
    )
    cache_path: str = Field(
        "data/llm_strategy_cache.sqlite",
        description=(
            "Local SQLite file for response caching. Same (model + "
            "system_prompt + user_prompt) -> cache hit, no API call."
        ),
    )
    api_key_env: str = Field(
        "ANTHROPIC_API_KEY",
        description="Env var name to read for the API key.",
    )

    @field_validator("indicators")
    @classmethod
    def _label_each(cls, vs: list[IndicatorSpec]) -> list[IndicatorSpec]:
        out = []
        for spec in vs:
            if spec.label is None:
                params_str = ",".join(f"{k}={v}" for k, v in spec.params.items())
                spec = spec.model_copy(update={"label": f"{spec.name}({params_str})"})
            out.append(spec)
        return out


# ─────────────────────────────────────────────────────────────────────
# Response cache (SQLite-backed)
# ─────────────────────────────────────────────────────────────────────


class _ResponseCache:
    """
    Thread-safe SQLite-backed cache of LLM responses keyed on prompt
    hash. Same prompt → cache hit, no API call, no money spent.

    Layout:
      llm_response_cache(
        key TEXT PRIMARY KEY,        -- sha256(model || system || user)
        response_json TEXT NOT NULL, -- the LLM's response (full content)
        created_at TEXT NOT NULL     -- ISO-8601 UTC
      )

    The cache is **append-only**; we never overwrite. To invalidate
    a stale cache, either delete the file or change the model /
    system_prompt (the key changes, miss → fresh call).
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS llm_response_cache ("
            " key TEXT PRIMARY KEY,"
            " response_json TEXT NOT NULL,"
            " created_at TEXT NOT NULL"
            ")"
        )
        self._conn.commit()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT response_json FROM llm_response_cache WHERE key = ?",
                (key,),
            ).fetchone()
        return row[0] if row else None

    def put(self, key: str, response_json: str) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO llm_response_cache (key, response_json, created_at) VALUES (?, ?, ?)",
                (key, response_json, now),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _cache_key(model: str, system_prompt: str, user_prompt: str) -> str:
    """sha256(model || system || user). Stable across processes."""
    h = hashlib.sha256()
    h.update(b"M:"); h.update(model.encode("utf-8"))
    h.update(b"|S:"); h.update(system_prompt.encode("utf-8"))
    h.update(b"|U:"); h.update(user_prompt.encode("utf-8"))
    return h.hexdigest()


# ─────────────────────────────────────────────────────────────────────
# Strategy
# ─────────────────────────────────────────────────────────────────────


@dataclass
class _CallStats:
    """Per-run accounting: how many LLM calls vs cache hits."""

    api_calls: int = 0
    cache_hits: int = 0
    parse_failures: int = 0
    api_failures: int = 0


class LLMAgentStrategy:
    """
    LLM-driven trading strategy. Implements the `Strategy` Protocol
    directly (not via `BaseStrategy`) because we need explicit
    setup/teardown for the API client + cache.

    Cost shape: at most (n_bars - warmup) API calls per backtest run.
    Cache hits cost nothing. With temperature=0 + a stable
    system_prompt, replays cost zero after the first run.

    Per the modularity contract, this strategy is **side-effecting**
    (it talks to the Anthropic API). Per `feedback_trading_subsystem_design`
    that's allowed as long as the file is clearly marked and lives
    next to its pure peers — the import-purity gate in
    `test_strategy_is_pure` explicitly allows `anthropic`, just not
    `app.db.*` or `app.providers.*`.
    """

    name: str = "llm_agent"
    version: str = "0.1"

    def __init__(
        self,
        params: Optional[LLMAgentParams] = None,
        *,
        interval: str = "1d",
        client: Any = None,  # injectable for tests; type avoided to keep import light
    ) -> None:
        self.params = params or LLMAgentParams()
        self.interval = interval
        # Injected client lets unit tests pass a stub without hitting the API.
        self._client = client
        self._cache: Optional[_ResponseCache] = None
        self._stats = _CallStats()

    # ─── Strategy Protocol ────────────────────────────────────────────

    def setup(self, ctx: Context) -> None:
        self._stats = _CallStats()
        self._cache = _ResponseCache(self.params.cache_path)
        if self._client is None:
            # Lazy import keeps the strategy module light at top-level
            # AND keeps test_strategy_is_pure structural-gate friendly
            # — anthropic isn't an app.db/app.providers import.
            import os
            import anthropic
            key = os.environ.get(self.params.api_key_env)
            if not key:
                raise RuntimeError(
                    f"LLMAgentStrategy requires {self.params.api_key_env} to be set. "
                    "Either set the env var or inject a stub client via constructor."
                )
            self._client = anthropic.Anthropic(api_key=key)

    def teardown(self, ctx: Context) -> None:
        if self._cache is not None:
            self._cache.close()
        logger.info(
            "LLMAgentStrategy run: api_calls=%d cache_hits=%d parse_failures=%d api_failures=%d",
            self._stats.api_calls, self._stats.cache_hits,
            self._stats.parse_failures, self._stats.api_failures,
        )

    def on_bar(self, ctx: Context) -> Action:
        p = self.params

        # Warmup: need enough history for the prompt + indicators to
        # be meaningful. The slowest indicator's period sets the floor.
        slowest = max(
            (int(spec.params.get("period", 14)) for spec in p.indicators),
            default=0,
        )
        warmup_needed = max(p.context_bars, slowest + 1)
        if len(ctx.history) < warmup_needed:
            return hold()

        user_prompt = self._build_user_prompt(ctx)
        key = _cache_key(p.model, p.system_prompt, user_prompt)

        # Cache hit -> no API call.
        cached = self._cache.get(key) if self._cache else None
        if cached is not None:
            self._stats.cache_hits += 1
            response_text = cached
        else:
            response_text = self._call_llm(user_prompt)
            if response_text is None:
                self._stats.api_failures += 1
                return hold()
            if self._cache is not None:
                self._cache.put(key, response_text)
            self._stats.api_calls += 1

        return self._parse_response(response_text, ctx)

    def params_dict(self) -> dict[str, Any]:
        """For the agent_runs registry."""
        return self.params.model_dump(mode="json")

    # ─── Prompt building ──────────────────────────────────────────────

    def _build_user_prompt(self, ctx: Context) -> str:
        """
        Build the per-bar user prompt. Deterministic given the same
        history + portfolio + indicator params, so the cache key is
        stable across runs.
        """
        p = self.params
        df = ctx.history.to_dataframe().tail(p.context_bars)
        bar = ctx.bar
        portfolio = ctx.portfolio

        # Indicator values at the CURRENT bar only (saves tokens).
        ind_lines: list[str] = []
        for spec in p.indicators:
            try:
                series = ctx.indicator(spec.name, **spec.params)
                latest = series.iloc[-1] if len(series) else float("nan")
                value = "n/a" if pd.isna(latest) else f"{float(latest):.4f}"
            except Exception as exc:  # noqa: BLE001 — degrade to 'n/a'
                logger.warning(
                    "LLMAgentStrategy: indicator %s%s failed: %s",
                    spec.name, spec.params, exc,
                )
                value = "n/a"
            ind_lines.append(f"  {spec.label or spec.name}: {value}")

        position = portfolio.positions.get(bar.symbol)
        if position and position.quantity > 0:
            position_line = (
                f"Currently HOLDING {position.quantity:.4f} shares @ avg entry "
                f"${position.avg_entry_price:.2f} (unrealized PnL ${position.unrealized_pnl:.2f})"
            )
        else:
            position_line = "No current position."

        # Last-N close trail. Truncate to keep the prompt token-bounded.
        closes_trail = ", ".join(f"{c:.2f}" for c in df["close"].tolist())

        return (
            f"Symbol: {bar.symbol}\n"
            f"Bar timestamp: {bar.timestamp.isoformat()}\n"
            f"Interval: {self.interval}\n"
            f"Current bar OHLCV: O={bar.open:.2f} H={bar.high:.2f} L={bar.low:.2f} "
            f"C={bar.close:.2f} V={int(bar.volume)}\n\n"
            f"Last {len(df)} closes (oldest first): {closes_trail}\n\n"
            f"Indicators at current bar:\n" + "\n".join(ind_lines) + "\n\n"
            f"Portfolio:\n"
            f"  Cash: ${portfolio.cash:.2f}\n"
            f"  Equity: ${portfolio.equity:.2f}\n"
            f"  {position_line}\n\n"
            f"What do you do for the next bar?"
        )

    # ─── LLM call ─────────────────────────────────────────────────────

    def _call_llm(self, user_prompt: str) -> Optional[str]:
        """Call Anthropic and return the text response, or None on failure."""
        p = self.params
        try:
            resp = self._client.messages.create(
                model=p.model,
                max_tokens=p.max_tokens,
                temperature=p.temperature,
                system=p.system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            # Concatenate all text-content blocks.
            parts = []
            for block in resp.content:
                text = getattr(block, "text", None)
                if text:
                    parts.append(text)
            return "".join(parts) if parts else None
        except Exception as exc:  # noqa: BLE001 — degrade to None
            logger.warning("LLMAgentStrategy: API call failed: %s", exc)
            return None

    # ─── Response parsing ─────────────────────────────────────────────

    def _parse_response(self, text: str, ctx: Context) -> Action:
        """
        Extract the JSON decision from the model's response and
        translate into an `Action`. Tolerant: looks for the first
        `{...}` substring in case the model wrapped JSON in prose.
        """
        decision = _extract_json_object(text)
        if decision is None:
            self._stats.parse_failures += 1
            ctx.log(event="llm_parse_failure", raw=text[:200])
            return hold()

        kind = str(decision.get("action", "hold")).lower()
        size_pct = float(decision.get("size_pct", 0.0) or 0.0)
        rationale = str(decision.get("rationale", ""))[:200]

        symbol = ctx.bar.symbol
        ctx.log(
            event="llm_decision", action=kind, size_pct=size_pct,
            rationale=rationale,
        )

        if kind == "buy":
            position = ctx.portfolio.positions.get(symbol)
            if position is not None and position.quantity > 0:
                return hold()  # already in position; ignore conflicting buy
            cap = min(size_pct, self.params.position_size_pct)
            cash_to_spend = ctx.portfolio.cash * cap
            price = ctx.bar.close
            if price <= 0:
                return hold()
            qty = int(cash_to_spend // price)  # integer shares
            if qty <= 0:
                return hold()
            return Action(kind="buy", symbol=symbol, size=float(qty), note=f"llm: {rationale}")

        if kind == "sell":
            position = ctx.portfolio.positions.get(symbol)
            if position is None or position.quantity <= 0:
                return hold()
            qty = position.quantity * max(0.0, min(size_pct, 1.0))
            if qty <= 0:
                return hold()
            return Action(kind="sell", symbol=symbol, size=qty, note=f"llm: {rationale}")

        return hold()


def _extract_json_object(text: str) -> Optional[dict[str, Any]]:
    """
    Find and parse the first top-level JSON object in `text`. Returns
    None if no object is found or it fails to parse.

    Why not just `json.loads(text)`? Models sometimes wrap their
    answer in prose despite the system-prompt instruction. Being
    lenient on the parse keeps the strategy alive in those cases —
    we'd rather emit hold() than crash the backtest.
    """
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None
    # Walk braces to find the matching close.
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    obj = json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                if isinstance(obj, dict):
                    return obj
                return None
    return None
