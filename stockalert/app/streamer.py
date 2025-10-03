import asyncio, pandas as pd
from datetime import timezone
from app.config import settings
from app.db import SessionLocal
from app.models import Bar, Signal
from app.indicators.rsi import RSI
from app.indicators.macd import MACD
from app.indicators.tsi import TSI
from app.divergence import (
    detect_hidden_bullish, detect_hidden_bearish, detect_regular_bullish, detect_regular_bearish
)
from app.providers.base import DataProvider

INDICATOR_MAP = {"rsi": RSI, "macd": MACD, "tsi": TSI}
SIGNALS = {
    "hidden_bullish_divergence": detect_hidden_bullish,
    "hidden_bearish_divergence": detect_hidden_bearish,
    "regular_bullish_divergence": detect_regular_bullish,
    "regular_bearish_divergence": detect_regular_bearish,
}

class DivergenceTracker:
    def __init__(self, symbol: str, indicator_name: str, signal_type: str, broadcast_cb=None):
        self.symbol = symbol
        self.indicator_name = indicator_name
        self.signal_type = signal_type
        self.indicator = INDICATOR_MAP[indicator_name]()
        self.df = pd.DataFrame(columns=["ts","open","high","low","close","volume"]).set_index("ts")
        self.broadcast_cb = broadcast_cb

    async def on_bar(self, bar):
        ts = getattr(bar, "timestamp", None) or getattr(bar, "ts", None)
        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
        row = {
            "open": float(getattr(bar, "open")),
            "high": float(getattr(bar, "high")),
            "low": float(getattr(bar, "low")),
            "close": float(getattr(bar, "close")),
            "volume": int(getattr(bar, "volume", 0) or 0),
        }
        self.df.loc[ts] = row
        if len(self.df) > 3000: self.df = self.df.iloc[-2200:]

        ind = self.indicator.compute(self.df["close"], self.df.get("high"), self.df.get("low"))
        detector = SIGNALS[self.signal_type]
        res = detector(self.df["close"], ind, lookback=settings.lookback_bars, k=settings.pivot_k)
        if res:
            await self.persist_signal(res)
            if self.broadcast_cb:
                await self.broadcast_cb({
                    "symbol": self.symbol,
                    "signal_type": self.signal_type,
                    "indicator": self.indicator_name,
                    "ts": str(res["p2_ts"]),
                    "price": float(res["price"]),
                    "indicator_value": float(res["indicator_value"]),
                })

        asyncio.create_task(self.persist_bar(ts, row))

    async def persist_bar(self, ts, row):
        async with SessionLocal() as sess:
            try:
                sess.add(Bar(symbol=self.symbol, ts=ts, **row))
                await sess.commit()
            except Exception:
                await sess.rollback()

    async def persist_signal(self, res: dict):
        async with SessionLocal() as sess:
            try:
                sess.add(Signal(
                    symbol=self.symbol, signal_type=self.signal_type, indicator=self.indicator_name,
                    ts_signal=res["p2_ts"], price_at_signal=float(res["price"]),
                    indicator_value=float(res["indicator_value"]), p1_ts=res["p1_ts"], p2_ts=res["p2_ts"]
                ))
                await sess.commit()
            except Exception:
                await sess.rollback()

class DivergenceMonitor:
    def __init__(self, provider: DataProvider, tickers: list[str], indicator_name: str, signal_type: str, broadcast_cb=None):
        self.provider = provider
        self.tickers = tickers
        self.indicator_name = indicator_name
        self.signal_type = signal_type
        self.trackers = {sym: DivergenceTracker(sym, indicator_name, signal_type, broadcast_cb) for sym in tickers}

    def start(self):
        async def callback(bar):
            sym = getattr(bar, "symbol", None) or getattr(bar, "ticker", None)
            tr = self.trackers.get(sym)
            if tr: await tr.on_bar(bar)
        self.provider.subscribe_bars(callback, self.tickers)

    def stop(self):
        self.provider.unsubscribe_bars(self.tickers)
        self.provider.stop_stream()
