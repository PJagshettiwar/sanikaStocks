import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timedelta, timezone
from brokers.base import Quote, Position
from risk_engine import validate_signal, ValidationResult


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
    timestamp = datetime.now(timezone.utc).isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False), \
         patch("risk_engine.get_today_trade_count", return_value=0):
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
    timestamp = datetime.now(timezone.utc).isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False):
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
    timestamp = datetime.now(timezone.utc).isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False), \
         patch("risk_engine.get_today_trade_count", return_value=0):
        result = await validate_signal(
            signal, channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is True
    assert result.stop_loss == 1490.0 * 0.85  # 15% below entry_max


@pytest.mark.asyncio
async def test_low_balance_buy_still_passes_validation():
    """R2-H4: Balance check removed from validate_signal for BUY. Approval flow checks balance."""
    broker = _make_broker(balance=500)
    db_conn = AsyncMock()
    timestamp = datetime.now(timezone.utc).isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False), \
         patch("risk_engine.get_today_trade_count", return_value=0):
        result = await validate_signal(
            _make_signal(), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is True


@pytest.mark.asyncio
async def test_duplicate_signal_rejected():
    broker = _make_broker()
    db_conn = AsyncMock()
    timestamp = datetime.now(timezone.utc).isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=True):
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
    old_timestamp = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False):
        result = await validate_signal(
            _make_signal(), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=old_timestamp,
        )
    assert result.valid is False
    assert "age" in result.reason.lower() or "old" in result.reason.lower()


@pytest.mark.asyncio
async def test_off_hours_signal_still_passes():
    """H12: Signals are accepted 24/7; market hours enforcement is in approval_bot."""
    broker = _make_broker()
    db_conn = AsyncMock()
    timestamp = datetime.now(timezone.utc).isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False), \
         patch("risk_engine.get_today_trade_count", return_value=0):
        result = await validate_signal(
            _make_signal(), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is True


@pytest.mark.asyncio
async def test_timezone_aware_timestamp_does_not_crash():
    """C2: Telegram sends timezone-aware timestamps. datetime.now(timezone.utc) - aware raises TypeError."""
    broker = _make_broker()
    db_conn = AsyncMock()
    aware_timestamp = datetime.now(timezone.utc).isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False), \
         patch("risk_engine.get_today_trade_count", return_value=0):
        result = await validate_signal(
            _make_signal(), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=aware_timestamp,
        )
    assert result.valid is True


@pytest.mark.asyncio
async def test_daily_trade_limit_rejected():
    broker = _make_broker()
    db_conn = AsyncMock()
    timestamp = datetime.now(timezone.utc).isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False), \
         patch("risk_engine.get_today_trade_count", return_value=5):
        result = await validate_signal(
            _make_signal(), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is False
    assert "daily" in result.reason.lower() or "limit" in result.reason.lower()


@pytest.mark.asyncio
async def test_sell_signal_uses_held_quantity():
    broker = _make_broker()
    broker.get_positions.return_value = [
        Position(security_id="2885", symbol="RELIANCE", exchange="NSE", net_qty=10, avg_price=1400.0)
    ]
    db_conn = AsyncMock()
    timestamp = datetime.now(timezone.utc).isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False), \
         patch("risk_engine.get_today_trade_count", return_value=0):
        result = await validate_signal(
            _make_signal(action="SELL"), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is True
    assert result.quantity == 10
    assert result.action == "SELL"
    broker.get_balance.assert_not_called()


@pytest.mark.asyncio
async def test_sell_signal_no_position_rejected():
    broker = _make_broker()
    broker.get_positions.return_value = []
    db_conn = AsyncMock()
    timestamp = datetime.now(timezone.utc).isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False), \
         patch("risk_engine.get_today_trade_count", return_value=0):
        result = await validate_signal(
            _make_signal(action="SELL"), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is False
    assert "no position" in result.reason.lower()


