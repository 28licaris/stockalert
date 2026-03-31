import asyncio
from fastapi import APIRouter

from app.db import queries

router = APIRouter()


def _ts(v):
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


@router.get("/signals")
async def list_signals(symbol: str | None = None, limit: int = 50):
    rows = await asyncio.to_thread(queries.list_signals, symbol, limit)
    return [
        {
            "id": x["id"],
            "symbol": x["symbol"],
            "type": x["type"],
            "indicator": x["indicator"],
            "ts": _ts(x["ts"]),
            "price": x["price"],
            "indicator_value": x["indicator_value"],
        }
        for x in rows
    ]


@router.get("/bars")
async def list_bars(symbol: str, limit: int = 200):
    raw = await asyncio.to_thread(queries.list_bars_desc, symbol, limit)
    return [
        {
            "ts": _ts(x["ts"]),
            "open": x["open"],
            "high": x["high"],
            "low": x["low"],
            "close": x["close"],
            "volume": x["volume"],
        }
        for x in raw
    ]
