from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Signal, Bar

router = APIRouter()

async def get_sess():
    async with SessionLocal() as s: yield s

@router.get("/signals")
async def list_signals(symbol:str|None=None, limit:int=50, session:AsyncSession=Depends(get_sess)):
    stmt = select(Signal).order_by(Signal.ts_signal.desc()).limit(limit)
    if symbol: stmt = stmt.where(Signal.symbol==symbol)
    res = (await session.execute(stmt)).scalars().all()
    return [{
        "id":x.id,"symbol":x.symbol,"type":x.signal_type,"indicator":x.indicator,
        "ts":x.ts_signal.isoformat(), "price":x.price_at_signal, "indicator_value":x.indicator_value
    } for x in res]

@router.get("/bars")
async def list_bars(symbol:str, limit:int=200, session:AsyncSession=Depends(get_sess)):
    stmt = select(Bar).where(Bar.symbol==symbol).order_by(Bar.ts.desc()).limit(limit)
    res = (await session.execute(stmt)).scalars().all()
    out=[{"ts":x.ts.isoformat(),"open":x.open,"high":x.high,"low":x.low,"close":x.close,"volume":x.volume} for x in res]
    return list(reversed(out))
