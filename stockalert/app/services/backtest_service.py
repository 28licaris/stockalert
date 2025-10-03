import numpy as np, pandas as pd
from app.db import SessionLocal, init_db
from app.models import Bar
from app.indicators.rsi import RSI
from app.indicators.macd import MACD
from app.indicators.tsi import TSI
from app.divergence import (
    detect_hidden_bullish, detect_hidden_bearish, detect_regular_bullish, detect_regular_bearish
)
from app.config import settings

INDICATOR_MAP = {"rsi": RSI, "macd": MACD, "tsi": TSI}
DETECTOR_MAP = {
    "hidden_bullish_divergence": detect_hidden_bullish,
    "hidden_bearish_divergence": detect_hidden_bearish,
    "regular_bullish_divergence": detect_regular_bullish,
    "regular_bearish_divergence": detect_regular_bearish,
}

async def load_bars(symbol: str) -> pd.DataFrame:
    async with SessionLocal() as sess:
        rows = (await sess.execute(Bar.__table__.select().where(Bar.symbol==symbol).order_by(Bar.ts))).all()
    if not rows: return pd.DataFrame()
    return pd.DataFrame([{
        "ts": r.ts, "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume
    } for (r,) in rows]).set_index("ts")

def _forward_returns(df: pd.DataFrame, horizons=(5,15,60)):
    out={}
    for h in horizons:
        out[f"ret_{h}"]=df["close"].shift(-h)/df["close"]-1
    return pd.DataFrame(out, index=df.index)

async def run_backtest(tickers:list[str], indicator:str, signal_type:str, horizons=(5,15,60)):
    await init_db()
    metrics=[]
    for sym in tickers:
        bars = await load_bars(sym)
        if bars.empty:
            metrics.append({"symbol":sym,"signals":0}); continue
        ind = INDICATOR_MAP[indicator]().compute(bars["close"], bars.get("high"), bars.get("low"))
        det = DETECTOR_MAP[signal_type]
        sigs=[]
        for i in range(settings.lookback_bars, len(bars)):
            res = det(bars["close"].iloc[:i+1], ind.iloc[:i+1], lookback=settings.lookback_bars, k=settings.pivot_k)
            if res: sigs.append({"ts":res["p2_ts"],"price":res["price"]})
        if not sigs: metrics.append({"symbol":sym,"signals":0}); continue
        sig_df = pd.DataFrame(sigs).set_index("ts").sort_index()
        fwd = _forward_returns(bars)
        merged = sig_df.join(fwd, how="left")
        row={"symbol":sym,"signals":int(len(sig_df))}
        for h in horizons:
            col=f"ret_{h}"; vals=merged[col].dropna()
            if len(vals)==0: row.update({f"winrate_{h}":None,f"avg_{h}":None,f"sharpe_{h}":None})
            else:
                row[f"winrate_{h}"]=float((vals>0).mean())
                row[f"avg_{h}"]=float(vals.mean())
                row[f"sharpe_{h}"]=float(vals.mean()/(vals.std()+1e-9))
        metrics.append(row)
    return metrics
