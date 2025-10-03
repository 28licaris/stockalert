import pytest
import asyncio
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.config import settings
from app.models import Base, Alert, DivergenceSignal

@pytest.fixture
async def db_session():
    """Create a test database session"""
    # Use test database
    test_db_url = settings.database_url.replace("/divergence", "/divergence_test")
    engine = create_async_engine(test_db_url, echo=True)
    
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Create session
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
    
    # Drop tables after test
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    
    await engine.dispose()

@pytest.mark.asyncio
async def test_create_alert(db_session):
    """Test creating an alert in database"""
    alert = Alert(
        symbol="SPY",
        signal_type="hidden_bullish_divergence",
        indicator="rsi",
        price=670.50,
        indicator_value=45.2,
        timestamp=datetime.now(timezone.utc)
    )
    
    db_session.add(alert)
    await db_session.commit()
    await db_session.refresh(alert)
    
    assert alert.id is not None
    assert alert.symbol == "SPY"
    print(f"✅ Created alert with ID: {alert.id}")

@pytest.mark.asyncio
async def test_query_alerts(db_session):
    """Test querying alerts"""
    # Create test data
    for i in range(3):
        alert = Alert(
            symbol="SPY",
            signal_type="test_signal",
            indicator="rsi",
            price=670.00 + i,
            indicator_value=45.0 + i,
            timestamp=datetime.now(timezone.utc)
        )
        db_session.add(alert)
    
    await db_session.commit()
    
    # Query
    from sqlalchemy import select
    result = await db_session.execute(select(Alert).where(Alert.symbol == "SPY"))
    alerts = result.scalars().all()
    
    assert len(alerts) == 3
    print(f"✅ Found {len(alerts)} alerts")

if __name__ == "__main__":
    pytest.main([__file__, "-v"])