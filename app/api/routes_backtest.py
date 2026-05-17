from fastapi import APIRouter
from app.services.backtest_service import run_backtest

router = APIRouter()

@router.post("/backtest")
async def backtest(payload:dict):
    tickers = payload.get("tickers", ["QQQ"])
    indicator = payload.get("indicator","rsi")
    signal_type = payload.get("signal_type","hidden_bullish_divergence")
    horizons = payload.get("horizons",[5,15,60])
    return await run_backtest(tickers, indicator, signal_type, horizons)
