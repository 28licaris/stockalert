"""ClickHouse integration tests. Enable with CLICKHOUSE_TEST=1 and a running server."""

import os
import uuid
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("CLICKHOUSE_TEST", "").lower() not in ("1", "true", "yes"),
    reason="Set CLICKHOUSE_TEST=1 and start ClickHouse (see docker-compose.yml).",
)


@pytest.fixture(scope="module")
def _ch_schema():
    from app.db import close_client, init_schema

    init_schema()
    yield
    close_client()


def test_ohlcv_roundtrip(_ch_schema):
    from app.db import queries

    sym = "TESTCH"
    ts = datetime.now(timezone.utc).replace(microsecond=0)
    queries.insert_bars_batch(
        [
            {
                "symbol": sym,
                "timestamp": ts,
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 100.0,
                "source": "test",
            }
        ]
    )
    df = queries.fetch_bars(sym, ts, ts, 10)
    assert not df.empty
    assert df.iloc[-1]["close"] == 1.5


@pytest.mark.asyncio
async def test_signals_insert_async(_ch_schema):
    from app.db import queries

    now = datetime.now(timezone.utc).replace(microsecond=0)
    rid = uuid.uuid4()
    await queries.insert_signals_batch_async(
        [
            {
                "id": rid,
                "symbol": "TESTCH",
                "signal_type": "test_signal",
                "indicator": "rsi",
                "ts_signal": now,
                "price_at_signal": 100.0,
                "indicator_value": 50.0,
                "p1_ts": now,
                "p2_ts": now,
            }
        ]
    )
    rows = queries.list_signals("TESTCH", 5)
    assert any(r["id"] == str(rid) for r in rows)
