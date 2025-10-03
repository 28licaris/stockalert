from sqlalchemy import Column, String, Integer, Float, DateTime, UniqueConstraint, Index
from app.db import Base

class Bar(Base):
    __tablename__ = "bars"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(16), index=True)
    ts = Column(DateTime(timezone=True), index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer)
    __table_args__ = (
        UniqueConstraint("symbol", "ts", name="uq_bars_symbol_ts"),
        Index("ix_bars_symbol_ts", "symbol", "ts"),
    )

class Signal(Base):
    __tablename__ = "signals"
    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(16), index=True)
    signal_type = Column(String(64))
    indicator = Column(String(32))
    ts_signal = Column(DateTime(timezone=True), index=True)
    price_at_signal = Column(Float)
    indicator_value = Column(Float)
    p1_ts = Column(DateTime(timezone=True))
    p2_ts = Column(DateTime(timezone=True))
    __table_args__ = (
        Index("ix_signals_symbol_ts", "symbol", "ts_signal"),
    )
