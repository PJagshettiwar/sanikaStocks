import sys
import types

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from risk_engine import ValidationResult
from brokers.base import Quote, OrderResult

# yfinance isn't installable in this sandboxed/corporate-network environment
# (PyPI access is blocked). Stub it so approval_bot's transitive
# `import yfinance` (via market_data) succeeds.
if "yfinance" not in sys.modules:
    _yf_stub = types.ModuleType("yfinance")

    class _FakeFastInfo:
        last_price = 1490.0
        last_volume = 1000000
        day_high = 1495.0
        day_low = 1480.0

    class _FakeTicker:
        def __init__(self, ticker_symbol):
            self.ticker_symbol = ticker_symbol

        @property
        def fast_info(self):
            return _FakeFastInfo()

    _yf_stub.Ticker = _FakeTicker
    sys.modules["yfinance"] = _yf_stub

from approval_bot import (
    format_trade_card, parse_approval_reply, handle_approval_reply,
    _msg_to_candidate, _remove_pending,
)


def _make_candidate():
    return {
        "id": 1,
        "signal_id": 1,
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "action": "BUY",
        "quantity": 3,
        "amount": 4458.0,
        "stop_loss": 1455.0,
        "entry_min": 1482.0,
        "entry_max": 1490.0,
        "targets": "[1525.0, 1550.0]",
        "reasoning": "test",
        "confidence": 0.87,
        "allocation_pct": None,
        "original_message": "Buy RELIANCE",
        "channel_id": 123,
        "status": "pending",
        "current_price_at_send": 1486.0,
        "created_at": "2026-08-28T10:00:00",
    }


def _make_broker(balance=100000, price=1486.0):
    broker = AsyncMock()
    broker.get_balance.return_value = balance
    broker.get_quote.return_value = Quote(
        symbol="RELIANCE", exchange="NSE", price=price,
        volume=1000000, day_high=1495.0, day_low=1480.0,
    )
    broker.get_instruments.return_value = {"RELIANCE": "2885"}
    broker.place_order.return_value = OrderResult(order_id="ORD123", status="placed")
    return broker


def _make_bot_client():
    bot = AsyncMock()
    sent_msg = MagicMock()
    sent_msg.id = 999
    bot.send_message.return_value = sent_msg
    return bot


@pytest.fixture(autouse=True)
def _clear_pending():
    _msg_to_candidate.clear()
    yield
    _msg_to_candidate.clear()


def test_format_trade_card_contains_all_fields():
    validation = ValidationResult(
        valid=True, reason="ok", symbol="RELIANCE", exchange="NSE",
        action="BUY", security_id="2885", quantity=13, amount=19318.0,
        stop_loss=1455.0, entry_min=1482.0, entry_max=1490.0,
        targets=[1525.0, 1550.0], current_price=1486.0,
    )
    card = format_trade_card(
        candidate={"id": 1, "created_at": "2026-08-28T10:00:00", "allocation_pct": 10, "confidence": 0.87},
        validation=validation,
        original_message="Buy RELIANCE above 1480-1490, SL 1455, Targets 1525/1550",
    )
    assert "RELIANCE" in card
    assert "1,482" in card or "1482" in card
    assert "1,455" in card or "1455" in card
    assert "A to approve" in card
    assert "Buy RELIANCE above" in card


def test_parse_approval_reply_accepts_variations():
    assert parse_approval_reply("A") == "approve"
    assert parse_approval_reply("a") == "approve"
    assert parse_approval_reply("approve") == "approve"
    assert parse_approval_reply("Approve") == "approve"
    assert parse_approval_reply("yes") == "approve"
    assert parse_approval_reply("y") == "approve"
    assert parse_approval_reply("  A  ") == "approve"


def test_parse_approval_reply_rejects_variations():
    assert parse_approval_reply("R") == "reject"
    assert parse_approval_reply("r") == "reject"
    assert parse_approval_reply("reject") == "reject"
    assert parse_approval_reply("no") == "reject"
    assert parse_approval_reply("n") == "reject"


def test_parse_approval_reply_unrecognized():
    assert parse_approval_reply("maybe") is None
    assert parse_approval_reply("hello") is None
    assert parse_approval_reply("") is None


# --- handle_approval_reply tests (C6) ---


@pytest.mark.asyncio
async def test_handle_approval_unrecognized_text():
    broker = _make_broker()
    db_conn = AsyncMock()
    bot = _make_bot_client()

    result = await handle_approval_reply("maybe", 1, broker, db_conn, bot, 123)

    assert result == "unrecognized"
    bot.send_message.assert_called_once()
    assert "Reply A to approve" in bot.send_message.call_args[0][1]


@pytest.mark.asyncio
async def test_handle_approval_reject():
    broker = _make_broker()
    db_conn = AsyncMock()
    bot = _make_bot_client()

    with patch("approval_bot.get_pending_candidate", return_value=_make_candidate()), \
         patch("approval_bot.update_candidate_status") as mock_status, \
         patch("approval_bot.save_decision") as mock_decision:
        result = await handle_approval_reply("R", 1, broker, db_conn, bot, 123)

    assert result == "finalized"
    mock_status.assert_called_once_with(db_conn, 1, "rejected")
    mock_decision.assert_called_once_with(db_conn, 1, "reject", None)
    assert "Rejected" in bot.send_message.call_args[0][1]


@pytest.mark.asyncio
async def test_handle_approval_candidate_not_found():
    broker = _make_broker()
    db_conn = AsyncMock()
    bot = _make_bot_client()

    with patch("approval_bot.get_pending_candidate", return_value=None), \
         patch("db.get_candidate_status", return_value=None):
        result = await handle_approval_reply("A", 1, broker, db_conn, bot, 123)

    assert result == "finalized"
    assert "not found" in bot.send_message.call_args[0][1]


@pytest.mark.asyncio
async def test_handle_approval_already_decided():
    broker = _make_broker()
    db_conn = AsyncMock()
    bot = _make_bot_client()

    with patch("approval_bot.get_pending_candidate", return_value=None), \
         patch("db.get_candidate_status", return_value="executed"):
        result = await handle_approval_reply("A", 1, broker, db_conn, bot, 123)

    assert result == "finalized"
    assert "Already executed" in bot.send_message.call_args[0][1]


@pytest.mark.asyncio
async def test_handle_approval_market_closed():
    broker = _make_broker()
    db_conn = AsyncMock()
    bot = _make_bot_client()

    with patch("approval_bot.get_pending_candidate", return_value=_make_candidate()), \
         patch("approval_bot._is_market_open", return_value=(False, "Market closed: weekend")):
        result = await handle_approval_reply("A", 1, broker, db_conn, bot, 123)

    assert result == "market_closed"
    assert "Market closed" in bot.send_message.call_args[0][1]


@pytest.mark.asyncio
async def test_handle_approval_broker_unavailable():
    broker = _make_broker()
    broker.get_balance.side_effect = Exception("connection timeout")
    db_conn = AsyncMock()
    bot = _make_bot_client()

    with patch("approval_bot.get_pending_candidate", return_value=_make_candidate()), \
         patch("approval_bot._is_market_open", return_value=(True, "")):
        result = await handle_approval_reply("A", 1, broker, db_conn, bot, 123)

    assert result == "error"
    assert "Broker unavailable" in bot.send_message.call_args[0][1]


@pytest.mark.asyncio
async def test_handle_approval_insufficient_funds():
    broker = _make_broker(balance=500)
    db_conn = AsyncMock()
    bot = _make_bot_client()

    with patch("approval_bot.get_pending_candidate", return_value=_make_candidate()), \
         patch("approval_bot._is_market_open", return_value=(True, "")):
        result = await handle_approval_reply("A", 1, broker, db_conn, bot, 123)

    assert result == "insufficient_funds"
    assert "Insufficient" in bot.send_message.call_args[0][1]


@pytest.mark.asyncio
async def test_handle_approval_quote_unavailable():
    broker = _make_broker()
    broker.get_quote.side_effect = Exception("API down")
    db_conn = AsyncMock()
    bot = _make_bot_client()

    with patch("approval_bot.get_pending_candidate", return_value=_make_candidate()), \
         patch("approval_bot._is_market_open", return_value=(True, "")):
        result = await handle_approval_reply("A", 1, broker, db_conn, bot, 123)

    assert result == "error"
    assert "Quote unavailable" in bot.send_message.call_args[0][1]


@pytest.mark.asyncio
async def test_handle_approval_price_too_high():
    broker = _make_broker(price=6000.0)
    db_conn = AsyncMock()
    bot = _make_bot_client()

    with patch("approval_bot.get_pending_candidate", return_value=_make_candidate()), \
         patch("approval_bot._is_market_open", return_value=(True, "")):
        result = await handle_approval_reply("A", 1, broker, db_conn, bot, 123)

    assert result == "error"
    assert "exceeds allocation" in bot.send_message.call_args[0][1]


@pytest.mark.asyncio
async def test_handle_approval_price_outside_range_sends_reapproval():
    broker = _make_broker(price=1550.0)
    db_conn = AsyncMock()
    bot = _make_bot_client()

    with patch("approval_bot.get_pending_candidate", return_value=_make_candidate()), \
         patch("approval_bot._is_market_open", return_value=(True, "")), \
         patch("approval_bot.set_telegram_msg_id") as mock_set_msg:
        result = await handle_approval_reply("A", 1, broker, db_conn, bot, 123)

    assert result == "reapproval_sent"
    sent_text = bot.send_message.call_args[0][1]
    assert "PRICE CHANGED" in sent_text
    assert "1,550" in sent_text
    mock_set_msg.assert_called_once()


@pytest.mark.asyncio
async def test_handle_approval_success_places_order():
    broker = _make_broker(price=1486.0)
    db_conn = AsyncMock()
    bot = _make_bot_client()

    with patch("approval_bot.get_pending_candidate", return_value=_make_candidate()), \
         patch("approval_bot._is_market_open", return_value=(True, "")), \
         patch("approval_bot.save_decision") as mock_decision, \
         patch("approval_bot.save_audit_log") as mock_audit, \
         patch("approval_bot.save_trade") as mock_trade, \
         patch("approval_bot.update_candidate_status") as mock_status, \
         patch("db.get_candidate_status", return_value="pending"):
        result = await handle_approval_reply("A", 1, broker, db_conn, bot, 123)

    assert result == "finalized"
    broker.place_order.assert_called_once()
    order = broker.place_order.call_args[0][0]
    assert order.symbol == "RELIANCE"
    assert order.txn_type == "BUY"
    assert order.qty == 3
    mock_decision.assert_called_once_with(db_conn, 1, "approve", 1486.0)
    mock_audit.assert_called_once()
    mock_trade.assert_called_once()
    mock_status.assert_called_once_with(db_conn, 1, "executed")
    sent_text = bot.send_message.call_args[0][1]
    assert "Order placed" in sent_text
    assert "ORD123" in sent_text


@pytest.mark.asyncio
async def test_handle_approval_order_failure():
    broker = _make_broker(price=1486.0)
    broker.place_order.side_effect = Exception("order rejected by exchange")
    db_conn = AsyncMock()
    bot = _make_bot_client()

    with patch("approval_bot.get_pending_candidate", return_value=_make_candidate()), \
         patch("approval_bot._is_market_open", return_value=(True, "")), \
         patch("approval_bot.save_decision") as mock_decision, \
         patch("approval_bot.save_audit_log") as mock_audit, \
         patch("approval_bot.save_trade") as mock_trade, \
         patch("approval_bot.update_candidate_status") as mock_status, \
         patch("db.get_candidate_status", return_value="pending"):
        result = await handle_approval_reply("A", 1, broker, db_conn, bot, 123)

    assert result == "error"
    assert "Order failed" in bot.send_message.call_args[0][1]
    mock_decision.assert_not_called()
    mock_audit.assert_not_called()
    mock_trade.assert_not_called()
    mock_status.assert_not_called()
