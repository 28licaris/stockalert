"""Elliott Wave recompute — the nightly job body.

Per symbol x interval: pull adjusted bars (lake), detect multi-degree pivots,
run the WaveEngine, and append the resulting WaveLabeling to
`elliott_wave_labels`. Append-only, no-look-ahead (the engine only uses
confirmed pivots), reproducible (`engine_ver` + `git_sha`).

`compute_labeling()` is the single labeling code path shared by the CLI
(`scripts/ewt_label.py`) and this job — no drift between what an operator sees
and what gets stored.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import pandas as pd

from app.indicators.pivots import detect_multidegree
from app.services.elliott_store.schema import asset_class_for
from app.services.elliott_store.sink import ElliottLabelSink
from app.services.readers.bars_gateway import BarSource, get_chart_bars
from app.signals.elliott import WaveEngine
from app.signals.elliott.schemas import WaveLabeling

logger = logging.getLogger(__name__)

# Per-interval bar lookback. Intraday bars are dense — pulling 400 days of 5m
# bars produces ~30k rows, most of which are irrelevant noise for EWT and slow
# pivot detection. These windows give roughly the same *swing count* across
# timeframes as 400 daily bars.
_INTERVAL_LOOKBACK: dict[str, int] = {
    "1d":  400,
    "4h":  120,
    "1h":   90,
    "30m":  30,
    "15m":  20,
    "5m":   10,
    "1m":    3,
}
_DEFAULT_LOOKBACK = 400


def current_git_sha() -> str:
    """Best-effort git SHA for reproducibility; empty on failure."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _load_bars(symbol: str, interval: str, lookback_days: int) -> Optional[pd.DataFrame]:
    bars = get_chart_bars(symbol, interval=interval, lookback_days=lookback_days,
                          source=BarSource.LAKE)
    if not bars:
        return None
    df = pd.DataFrame([{"timestamp": b.timestamp, "high": b.high, "low": b.low,
                        "close": b.close} for b in bars])
    return df.sort_values("timestamp").set_index("timestamp")


def compute_labeling(symbol: str, interval: str = "1d", *, lookback_days: Optional[int] = None,
                     base_k: int = 4) -> Optional[WaveLabeling]:
    """Label the latest bar of `symbol`@`interval`. None if no usable bars.

    `lookback_days` defaults to `_INTERVAL_LOOKBACK[interval]` so callers that
    only pass a symbol+interval get the right window automatically.  Pass an
    explicit value to override (e.g. CLI deep-history runs).
    """
    days = lookback_days if lookback_days is not None else _INTERVAL_LOOKBACK.get(interval, _DEFAULT_LOOKBACK)
    df = _load_bars(symbol, interval, days)
    if df is None or len(df) < 2 * base_k + 5:
        return None
    close, high, low = df["close"], df["high"], df["low"]
    ks = tuple(x for x in (base_k, base_k * 2, base_k * 4, base_k * 8) if 2 * x + 5 <= len(close))
    pivots = detect_multidegree(close, high, low, ks=ks)
    as_of = len(close) - 1
    return WaveEngine().label(
        pivots, last_price=float(close.iloc[as_of]), symbol=symbol, interval=interval,
        as_of_index=as_of, as_of=close.index[as_of].to_pydatetime(),
    )


def recompute_symbol(symbol: str, intervals: tuple[str, ...] = ("1d",),
                     **kw) -> list[WaveLabeling]:
    """All labelings for one symbol across the requested intervals."""
    out: list[WaveLabeling] = []
    for interval in intervals:
        lab = compute_labeling(symbol, interval, **kw)
        if lab is not None:
            out.append(lab)
    return out


def recompute_universe(symbols: list[str], intervals: tuple[str, ...] = ("1d",), *,
                       git_sha: Optional[str] = None, **kw) -> dict:
    """Recompute + append labels for every symbol. Logs every outcome
    (including no-count symbols — no silent failures) and cross-checks that the
    rows appended match the labelings produced."""
    git = git_sha if git_sha is not None else current_git_sha()
    by_class: dict[str, list[WaveLabeling]] = defaultdict(list)
    labeled = no_count = errors = 0

    for sym in symbols:
        try:
            labs = recompute_symbol(sym, intervals, **kw)
        except Exception as exc:  # boundary: one bad symbol must not kill the run
            errors += 1
            logger.warning("ewt_recompute: %s FAILED: %s", sym, exc)
            continue
        if not labs:
            logger.info("ewt_recompute: %s -> no usable bars (skipped)", sym)
            continue
        for lab in labs:
            by_class[asset_class_for(sym)].append(lab)
            if lab.primary:
                labeled += 1
            else:
                no_count += 1
        logger.info("ewt_recompute: %s -> %s", sym,
                    {lab.interval: (lab.current_wave or "no-count") for lab in labs})

    total_labelings = sum(len(v) for v in by_class.values())
    written = 0
    for asset_class, labs in by_class.items():
        written += ElliottLabelSink.for_asset_class(asset_class).write(labs, git_sha=git)

    if written != total_labelings:  # cross-side verify (coding standards)
        logger.error("ewt_recompute: row mismatch — produced %d, wrote %d",
                     total_labelings, written)
    logger.info("ewt_recompute: DONE — %d symbols, %d rows written "
                "(%d with primary, %d no-count, %d errors)",
                len(symbols), written, labeled, no_count, errors)
    return {"symbols": len(symbols), "rows": written, "labeled": labeled,
            "no_count": no_count, "errors": errors}


async def run_now_recompute(symbols_provider: Callable[[], list[str]],
                            intervals: tuple[str, ...] = ("1d",)) -> None:
    """One audited recompute cycle (the registry `run_now` callable)."""
    from app.services.jobs import audit_run  # lazy: avoid import cycle at module load
    async with audit_run("nightly_elliott_recompute"):
        symbols = symbols_provider()
        if not symbols:
            logger.warning("ewt_recompute: empty universe — nothing to do")
            return
        await asyncio.to_thread(recompute_universe, symbols, intervals)


async def run_elliott_recompute_loop(symbols_provider: Callable[[], list[str]], *,
                                     run_hour_utc: int,
                                     intervals: tuple[str, ...] = ("1d",)) -> None:
    """Background loop: fire once per day at `run_hour_utc`."""
    while True:
        now = datetime.now(timezone.utc)
        nxt = now.replace(hour=run_hour_utc, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            await run_now_recompute(symbols_provider, intervals)
        except Exception as exc:  # never let the loop die
            logger.exception("ewt_recompute loop: cycle failed: %s", exc)
