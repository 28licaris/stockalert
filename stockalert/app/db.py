from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from app.config import settings

engine = create_async_engine(
    settings.database_url, 
    echo=False, 
    future=True, 
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
)

SessionLocal = sessionmaker(
    engine, 
    expire_on_commit=False, 
    class_=AsyncSession
)

Base = declarative_base()

async def init_db():
    """Initialize database tables"""
    from app.models import Bar, Signal
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def close_db():
    """Close database connections gracefully"""
    await engine.dispose()
