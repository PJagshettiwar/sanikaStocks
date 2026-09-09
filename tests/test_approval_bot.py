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
    _msg_to_candidate, _remove_pending, verify_order_fill,
    VERIFY_INTERVAL_SECONDS, VERIFY_MAX_ATTEMPTS,
    SUCCESS_STATUSES, FAILURE_STATUSES,
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
    broker.get_tick_size = lambda symbol: 0.05
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


# --- verify_order_fill tests ---


@pytest.mark.asyncio
async def test_verify_order_fill_success():
    from brokers.base import OrderStatus
    broker = _make_broker()
    broker.get_order_status = AsyncMock(return_value=OrderStatus(
        order_id="ORD123", status="SUCCESS", traded_qty=3, traded_price=1486.0,
        requested_qty=3, requested_price=1487.97, extra_info="",
    ))
    bot = _make_bot_client()
    db_conn = AsyncMock()

    with patch("asyncio.sleep", new_callable=AsyncMock), \
         patch("approval_bot.save_audit_log") as mock_audit, \
         patch("approval_bot.update_trade_fill") as mock_fill:
        await verify_order_fill("ORD123", "RELIANCE", "BUY", 3, broker, bot, 123, db_conn, 1)

    sent_text = bot.send_message.call_args[0][1]
    assert "FILLED" in sent_text or "filled" in sent_text.lower()
    assert "RELIANCE" in sent_text
    assert "ORD123" in sent_text
    mock_audit.assert_called_once()
    audit_action = mock_audit.call_args[0][2]
    assert audit_action == "order_filled"
    mock_fill.assert_called_once_with(db_conn, 1, "ORD123", 1486.0, 3)


@pytest.mark.asyncio
async def test_verify_order_fill_failed_with_reason():
    from brokers.base import OrderStatus
    broker = _make_broker()
    broker.get_order_status = AsyncMock(return_value=OrderStatus(
        order_id="ORD123", status="FAILED", traded_qty=0, traded_price=0,
        requested_qty=12, requested_price=412.22,
        extra_info="RMS:Blocked for nse_cm ACMESOLAR-EQ Insufficient Margin",
    ))
    bot = _make_bot_client()
    db_conn = AsyncMock()

    with patch("asyncio.sleep", new_callable=AsyncMock), \
         patch("approval_bot.save_audit_log") as mock_audit, \
         patch("approval_bot.update_trade_fill") as mock_fill:
        await verify_order_fill("ORD123", "ACMESOLAR", "BUY", 12, broker, bot, 123, db_conn, 6)

    sent_text = bot.send_message.call_args[0][1]
    assert "FAILED" in sent_text
    assert "Insufficient Margin" in sent_text
    mock_audit.assert_called_once()
    assert mock_audit.call_args[0][2] == "order_failed"
    mock_fill.assert_not_called()


@pytest.mark.asyncio
async def test_verify_order_fill_partial_fill():
    from brokers.base import OrderStatus
    broker = _make_broker()
    broker.get_order_status = AsyncMock(return_value=OrderStatus(
        order_id="ORD123", status="PARTIALLY FILLED - CANCELLED",
        traded_qty=5, traded_price=411.20,
        requested_qty=12, requested_price=412.22, extra_info="User cancelled",
    ))
    bot = _make_bot_client()
    db_conn = AsyncMock()

    with patch("asyncio.sleep", new_callable=AsyncMock), \
         patch("approval_bot.save_audit_log") as mock_audit, \
         patch("approval_bot.update_trade_fill") as mock_fill:
        await verify_order_fill("ORD123", "ACMESOLAR", "BUY", 12, broker, bot, 123, db_conn, 6)

    sent_text = bot.send_message.call_args[0][1]
    assert "5" in sent_text
    assert "12" in sent_text
    mock_fill.assert_called_once_with(db_conn, 6, "ORD123", 411.20, 5)
    assert mock_audit.call_args[0][2] == "order_failed"


@pytest.mark.asyncio
async def test_verify_order_fill_timeout():
    from brokers.base import OrderStatus
    broker = _make_broker()
    broker.get_order_status = AsyncMock(return_value=OrderStatus(
        order_id="ORD123", status="INITIATED", traded_qty=0, traded_price=0,
        requested_qty=12, requested_price=412.22, extra_info="",
    ))
    bot = _make_bot_client()
    db_conn = AsyncMock()

    with patch("asyncio.sleep", new_callable=AsyncMock), \
         patch("approval_bot.save_audit_log") as mock_audit:
        await verify_order_fill("ORD123", "RELIANCE", "BUY", 12, broker, bot, 123, db_conn, 1)

    sent_text = bot.send_message.call_args[0][1]
    assert "NOT confirmed" in sent_text or "not confirmed" in sent_text.lower()
    assert broker.get_order_status.call_count == VERIFY_MAX_ATTEMPTS
    assert mock_audit.call_args[0][2] == "order_unconfirmed"


@pytest.mark.asyncio
async def test_verify_order_fill_recovers_from_single_failure():
    from brokers.base import OrderStatus
    broker = _make_broker()
    success_status = OrderStatus(
        order_id="ORD123", status="SUCCESS", traded_qty=3, traded_price=1486.0,
        requested_qty=3, requested_price=1487.97, extra_info="",
    )
    broker.get_order_status = AsyncMock(
        side_effect=[Exception("connection timeout"), success_status],
    )
    bot = _make_bot_client()
    db_conn = AsyncMock()

    with patch("asyncio.sleep", new_callable=AsyncMock), \
         patch("approval_bot.save_audit_log"), \
         patch("approval_bot.update_trade_fill"):
        await verify_order_fill("ORD123", "RELIANCE", "BUY", 3, broker, bot, 123, db_conn, 1)

    sent_text = bot.send_message.call_args[0][1]
    assert "FILLED" in sent_text or "filled" in sent_text.lower()
    assert broker.get_order_status.call_count == 2


@pytest.mark.asyncio
async def test_verify_polls_until_terminal():
    from brokers.base import OrderStatus
    broker = _make_broker()
    pending = OrderStatus(
        order_id="ORD123", status="PENDING", traded_qty=0, traded_price=0,
        requested_qty=3, requested_price=1487.97, extra_info="",
    )
    success = OrderStatus(
        order_id="ORD123", status="SUCCESS", traded_qty=3, traded_price=1486.0,
        requested_qty=3, requested_price=1487.97, extra_info="",
    )
    broker.get_order_status = AsyncMock(side_effect=[pending, pending, success])
    bot = _make_bot_client()
    db_conn = AsyncMock()

    with patch("asyncio.sleep", new_callable=AsyncMock), \
         patch("approval_bot.save_audit_log"), \
         patch("approval_bot.update_trade_fill"):
        await verify_order_fill("ORD123", "RELIANCE", "BUY", 3, broker, bot, 123, db_conn, 1)

    assert broker.get_order_status.call_count == 3
    sent_text = bot.send_message.call_args[0][1]
    assert "FILLED" in sent_text or "filled" in sent_text.lower()


# --- Sell/exit card and approval tests ---


def _make_sell_candidate(**overrides):
    base = {
        "id": 10,
        "signal_id": 5,
        "symbol": "CAPLINPOINT",
        "exchange": "NSE",
        "action": "SELL",
        "quantity": 10,
        "amount": 14500.0,
        "stop_loss": 0,
        "entry_min": 0,
        "entry_max": 0,
        "sell_pct": 100,
        "avg_buy_price": 1200.0,
        "held_qty": 10,
        "targets": "[]",
        "reasoning": "exit call",
        "confidence": 0.85,
        "allocation_pct": None,
        "original_message": "Exit Caplin Point",
        "channel_id": 123,
        "status": "pending",
        "current_price_at_send": 1450.0,
        "created_at": "2026-09-09T10:00:00",
    }
    base.update(overrides)
    return base


def test_format_sell_card_shows_pnl():
    from approval_bot import _format_sell_card
    validation = ValidationResult(
        valid=True, reason="ok", symbol="CAPLINPOINT", exchange="NSE",
        action="SELL", security_id="1234", quantity=10, amount=14500.0,
        stop_loss=0, entry_min=0, entry_max=0, targets=[],
        current_price=1450.0, sell_pct=100, avg_buy_price=1200.0, held_qty=10,
    )
    card = format_trade_card(
        candidate={"id": 10, "created_at": "2026-09-09T10:00:00"},
        validation=validation,
        original_message="Exit Caplin Point",
    )
    assert "SELL CANDIDATE" in card
    assert "CAPLINPOINT" in card
    assert "1,200.00" in card
    assert "1,450.00" in card
    assert "+20.8%" in card
    assert "10 of 10 held" in card
    assert "14,500" in card
    assert "A to approve" in card


def test_format_sell_card_partial_shows_pct():
    validation = ValidationResult(
        valid=True, reason="ok", symbol="CAPLINPOINT", exchange="NSE",
        action="SELL", security_id="1234", quantity=5, amount=7250.0,
        stop_loss=0, entry_min=0, entry_max=0, targets=[],
        current_price=1450.0, sell_pct=50, avg_buy_price=1200.0, held_qty=10,
    )
    card = format_trade_card(
        candidate={"id": 10, "created_at": "2026-09-09T10:00:00"},
        validation=validation,
        original_message="Exit 50% Caplin Point",
    )
    assert "SELL 50%" in card
    assert "5 of 10 held" in card


def test_format_sell_card_loss_shows_negative():
    validation = ValidationResult(
        valid=True, reason="ok", symbol="RELIANCE", exchange="NSE",
        action="SELL", security_id="2885", quantity=3, amount=4200.0,
        stop_loss=0, entry_min=0, entry_max=0, targets=[],
        current_price=1400.0, sell_pct=100, avg_buy_price=1500.0, held_qty=3,
    )
    card = format_trade_card(
        candidate={"id": 1, "created_at": "2026-09-09T10:00:00"},
        validation=validation,
        original_message="Exit Reliance",
    )
    assert "-6.7%" in card
    assert "-100.00/share" in card


@pytest.mark.asyncio
async def test_handle_sell_approval_skips_balance_check():
    from brokers.base import Position
    broker = _make_broker(balance=200, price=1450.0)
    broker.get_positions.return_value = [
        Position(security_id="1234", symbol="CAPLINPOINT", exchange="NSE", net_qty=10, avg_price=1200.0),
    ]
    db_conn = AsyncMock()
    bot = _make_bot_client()

    with patch("approval_bot.get_pending_candidate", return_value=_make_sell_candidate()), \
         patch("approval_bot._is_market_open", return_value=(True, "")), \
         patch("approval_bot.save_decision"), \
         patch("approval_bot.save_audit_log"), \
         patch("approval_bot.save_trade"), \
         patch("approval_bot.update_candidate_status"), \
         patch("db.get_candidate_status", return_value="pending"):
        result = await handle_approval_reply("A", 10, broker, db_conn, bot, 123)

    assert result == "finalized"
    broker.place_order.assert_called_once()
    broker.get_balance.assert_not_called()


@pytest.mark.asyncio
async def test_handle_sell_approval_no_position_errors():
    from brokers.base import Position
    broker = _make_broker(price=1450.0)
    broker.get_positions.return_value = []
    db_conn = AsyncMock()
    bot = _make_bot_client()

    with patch("approval_bot.get_pending_candidate", return_value=_make_sell_candidate()), \
         patch("approval_bot._is_market_open", return_value=(True, "")):
        result = await handle_approval_reply("A", 10, broker, db_conn, bot, 123)

    assert result == "error"
    assert "No position" in bot.send_message.call_args[0][1]


@pytest.mark.asyncio
async def test_handle_sell_rejection_message():
    broker = _make_broker()
    db_conn = AsyncMock()
    bot = _make_bot_client()

    with patch("approval_bot.get_pending_candidate", return_value=_make_sell_candidate()), \
         patch("approval_bot.update_candidate_status"), \
         patch("approval_bot.save_decision"):
        result = await handle_approval_reply("R", 10, broker, db_conn, bot, 123)

    assert result == "finalized"
    sent = bot.send_message.call_args[0][1]
    assert "Rejected" in sent
    assert "SELL" in sent
    assert "CAPLINPOINT" in sent


@pytest.mark.asyncio
async def test_handle_sell_partial_does_not_close_buy_trade():
    from brokers.base import Position
    broker = _make_broker(price=1450.0)
    broker.get_positions.return_value = [
        Position(security_id="1234", symbol="CAPLINPOINT", exchange="NSE", net_qty=10, avg_price=1200.0),
    ]
    db_conn = AsyncMock()
    bot = _make_bot_client()

    with patch("approval_bot.get_pending_candidate", return_value=_make_sell_candidate(sell_pct=50)), \
         patch("approval_bot._is_market_open", return_value=(True, "")), \
         patch("approval_bot.save_decision"), \
         patch("approval_bot.save_audit_log"), \
         patch("approval_bot.save_trade"), \
         patch("approval_bot.update_candidate_status"), \
         patch("db.get_candidate_status", return_value="pending"), \
         patch("db.get_open_buy_trade") as mock_get_buy, \
         patch("db.close_trade") as mock_close:
        result = await handle_approval_reply("A", 10, broker, db_conn, bot, 123)

    assert result == "finalized"
    mock_get_buy.assert_not_called()
    mock_close.assert_not_called()


@pytest.mark.asyncio
async def test_handle_sell_full_closes_buy_trade():
    from brokers.base import Position
    broker = _make_broker(price=1450.0)
    broker.get_positions.return_value = [
        Position(security_id="1234", symbol="CAPLINPOINT", exchange="NSE", net_qty=10, avg_price=1200.0),
    ]
    db_conn = AsyncMock()
    bot = _make_bot_client()

    with patch("approval_bot.get_pending_candidate", return_value=_make_sell_candidate(sell_pct=100)), \
         patch("approval_bot._is_market_open", return_value=(True, "")), \
         patch("approval_bot.save_decision"), \
         patch("approval_bot.save_audit_log"), \
         patch("approval_bot.save_trade"), \
         patch("approval_bot.update_candidate_status"), \
         patch("db.get_candidate_status", return_value="pending"), \
         patch("db.get_open_buy_trade", return_value=42) as mock_get_buy, \
         patch("db.close_trade") as mock_close:
        result = await handle_approval_reply("A", 10, broker, db_conn, bot, 123)

    assert result == "finalized"
    mock_get_buy.assert_called_once_with(db_conn, "CAPLINPOINT")
    mock_close.assert_called_once()
