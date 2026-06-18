"""Asset-class symbol helpers (futures vs equities).

Continuous futures roots are ``/``-prefixed (``/ES``, ``/MES``); equities
never are. This one predicate is the routing key for the read surface —
which ClickHouse table (``futures_ohlcv_1m`` vs ``ohlcv_1m``) and which
lake (``futures.schwab_futures`` vs ``equities.polygon_adjusted``) a bar
request targets.
"""
from __future__ import annotations

# ClickHouse hot-tier table per asset class.
EQUITIES_CH_TABLE = "ohlcv_1m"
FUTURES_CH_TABLE = "futures_ohlcv_1m"


def is_futures_symbol(symbol: str) -> bool:
    """True for a continuous futures root (``/ES``, ``/MES``, …)."""
    return bool(symbol) and symbol.strip().startswith("/")


def ch_table_for(symbol: str) -> str:
    """The hot-tier CH table a symbol's 1-min bars live in."""
    return FUTURES_CH_TABLE if is_futures_symbol(symbol) else EQUITIES_CH_TABLE
