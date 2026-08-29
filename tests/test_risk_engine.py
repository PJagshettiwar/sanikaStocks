import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timedelta, timezone
from brokers.base import Quote
from risk_engine import validate_signal, ValidationResult

IST = timezone(timedelta(hours=5, minutes=30))
_IN_MARKET_HOURS = datetime(2026, 8, 24, 11, 0, tzinfo=IST)  # Monday, 11:00 IST


def _make_signal(**overrides):
    base = {
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "action": "BUY",
        "entry_min": 1482.0,
        "entry_max": 1490.0,
        "stop_loss": 1455.0,
        "targets": [1525.0, 1550.0],
        "allocation_pct": None,
        "confidence": 0.87,
        "reasoning": "test",
    }
    base.update(overrides)
    return base


def _make_broker(balance=100000, price=1486.0, instruments=None):
    broker = AsyncMock()
    broker.get_balance.return_value = balance
    broker.get_quote.return_value = Quote(
        symbol="RELIANCE", exchange="NSE", price=price,
        volume=1000000, day_high=1495.0, day_low=1480.0,
    )
    broker.get_instruments.return_value = instruments or {"RELIANCE": "2885"}
    return broker


@pytest.mark.asyncio
async def test_valid_signal_passes():
    broker = _make_broker()
    db_conn = AsyncMock()
    timestamp = datetime.utcnow().isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False), \
         patch("risk_engine._now_ist", return_value=_IN_MARKET_HOURS):
        result = await validate_signal(
            _make_signal(), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is True
    assert result.quantity > 0
    assert result.security_id == "2885"


@pytest.mark.asyncio
async def test_unknown_symbol_rejected():
    broker = _make_broker(instruments={})
    db_conn = AsyncMock()
    timestamp = datetime.utcnow().isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False), \
         patch("risk_engine._now_ist", return_value=_IN_MARKET_HOURS):
        result = await validate_signal(
            _make_signal(symbol="FAKESTOCK"), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is False
    assert "symbol" in result.reason.lower()


@pytest.mark.asyncio
async def test_default_stop_loss_applied():
    signal = _make_signal(stop_loss=None)
    broker = _make_broker()
    db_conn = AsyncMock()
    timestamp = datetime.utcnow().isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False), \
         patch("risk_engine._now_ist", return_value=_IN_MARKET_HOURS):
        result = await validate_signal(
            signal, channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is True
    assert result.stop_loss == 1490.0 * 0.85  # 15% below entry_max


@pytest.mark.asyncio
async def test_insufficient_balance_rejected():
    broker = _make_broker(balance=500)
    db_conn = AsyncMock()
    timestamp = datetime.utcnow().isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False), \
         patch("risk_engine._now_ist", return_value=_IN_MARKET_HOURS):
        result = await validate_signal(
            _make_signal(), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is False
    assert "balance" in result.reason.lower()


@pytest.mark.asyncio
async def test_duplicate_signal_rejected():
    broker = _make_broker()
    db_conn = AsyncMock()
    timestamp = datetime.utcnow().isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=True), \
         patch("risk_engine._now_ist", return_value=_IN_MARKET_HOURS):
        result = await validate_signal(
            _make_signal(), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is False
    assert "duplicate" in result.reason.lower()


@pytest.mark.asyncio
async def test_old_signal_rejected():
    broker = _make_broker()
    db_conn = AsyncMock()
    old_timestamp = (datetime.utcnow() - timedelta(hours=2)).isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False), \
         patch("risk_engine._now_ist", return_value=_IN_MARKET_HOURS):
        result = await validate_signal(
            _make_signal(), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=old_timestamp,
        )
    assert result.valid is False
    assert "age" in result.reason.lower() or "old" in result.reason.lower()


@pytest.mark.asyncio
async def test_weekend_signal_rejected():
    broker = _make_broker()
    db_conn = AsyncMock()
    timestamp = datetime.utcnow().isoformat()
    saturday = datetime(2026, 8, 22, 11, 0, tzinfo=IST)

    with patch("risk_engine.has_duplicate_signal", return_value=False), \
         patch("risk_engine._now_ist", return_value=saturday):
        result = await validate_signal(
            _make_signal(), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is False
    assert "market" in result.reason.lower()


@pytest.mark.asyncio
async def test_after_hours_signal_rejected():
    broker = _make_broker()
    db_conn = AsyncMock()
    timestamp = datetime.utcnow().isoformat()
    after_hours = datetime(2026, 8, 24, 18, 0, tzinfo=IST)  # Monday, 6 PM IST

    with patch("risk_engine.has_duplicate_signal", return_value=False), \
         patch("risk_engine._now_ist", return_value=after_hours):
        result = await validate_signal(
            _make_signal(), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is False
    assert "market" in result.reason.lower()
