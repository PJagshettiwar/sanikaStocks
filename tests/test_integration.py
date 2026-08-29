import sys
import types

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from brokers.base import Quote, OrderResult, Position

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

import json
import aiosqlite
import httpx

from db import (
    init_db, save_message, save_signal, save_trade_candidate,
    get_pending_candidate, get_all_pending_candidates,
    update_candidate_status, get_open_buy_trade, close_trade,
    update_candidate_entry_range, get_today_trade_count, save_trade,
    get_candidate_status,
)
from approval_bot import (
    format_trade_card, handle_approval_reply, send_approval,
    load_pending_from_db, _msg_to_candidate, _remove_pending,
    verify_order_fill, _sanitize_source,
)
from risk_engine import validate_signal, ValidationResult
from stock_agent import extract_trade, detect_signal, analyze_message, _cost_tracker


@pytest.fixture
async def db():
    conn = await aiosqlite.connect(":memory:")
    await init_db(conn)
    yield conn
    await conn.close()


@pytest.fixture(autouse=True)
def clear_approval_state():
    _msg_to_candidate.clear()
    yield
    _msg_to_candidate.clear()


@pytest.fixture(autouse=True)
def reset_cost_tracker():
    _cost_tracker["calls"] = 0
    _cost_tracker["total_tokens"] = 0
    _cost_tracker["cost_usd"] = 0.0
    _cost_tracker["db_conn"] = None
    yield


def _make_broker(balance=100000, price=1486.0):
    broker = AsyncMock()
    broker.get_balance.return_value = balance
    broker.get_quote.return_value = Quote(
        symbol="RELIANCE", exchange="NSE", price=price,
        volume=1000000, day_high=1495.0, day_low=1480.0,
    )
    broker.get_instruments.return_value = {"RELIANCE": "2885", "INFY": "5678"}
    broker.place_order.return_value = OrderResult(order_id="ORD123", status="placed")
    broker.get_positions.return_value = []
    return broker


def _make_bot():
    bot = AsyncMock()
    bot.send_message.return_value = MagicMock(id=999)
    return bot


def _mock_openrouter_response(content):
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": content}}],
            "usage": {"total_tokens": 10},
        },
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )


async def _seed_candidate(db, entry_min=1482.0, entry_max=1490.0, action="BUY",
                           symbol="RELIANCE", stop_loss=1455.0):
    msg_db_id = await save_message(db, 123, 1001, "Buy RELIANCE above 1480", "2026-08-28T10:00:00")
    sig_id = await save_signal(db, msg_db_id, {
        "symbol": symbol, "exchange": "NSE", "action": action,
        "entry_min": entry_min, "entry_max": entry_max,
        "stop_loss": stop_loss, "targets": [1525.0, 1550.0],
        "confidence": 0.87, "reasoning": "test",
    })
    cand_id = await save_trade_candidate(
        db, sig_id, symbol, 3, 4458.0, stop_loss, 1486.0, entry_min, entry_max,
    )
    return cand_id


# ---- R2-C2: reapproval updates entry range ----

@pytest.mark.asyncio
async def test_reapproval_updates_entry_range(db):
    cand_id = await _seed_candidate(db, entry_min=1482.0, entry_max=1490.0)
    broker = _make_broker(price=1550.0)
    bot = _make_bot()

    with patch("approval_bot._is_market_open", return_value=(True, "")):
        result = await handle_approval_reply("A", cand_id, broker, db, bot, 123)

    assert result == "reapproval_sent"

    cursor = await db.execute(
        "SELECT entry_min, entry_max FROM trade_candidates WHERE id = ?", (cand_id,),
    )
    row = await cursor.fetchone()
    margin = 1550.0 * 0.01
    assert abs(row[0] - (1550.0 - margin)) < 0.01
    assert abs(row[1] - (1550.0 + margin)) < 0.01

    broker2 = _make_broker(price=1550.0)
    bot2 = _make_bot()
    with patch("approval_bot._is_market_open", return_value=(True, "")):
        result2 = await handle_approval_reply("A", cand_id, broker2, db, bot2, 123)

    assert result2 != "reapproval_sent"


# ---- R2-H1: post-order DB failure sends critical alert ----

@pytest.mark.asyncio
async def test_post_order_db_failure_sends_critical_alert(db):
    cand_id = await _seed_candidate(db)
    broker = _make_broker(price=1486.0)
    bot = _make_bot()

    with patch("approval_bot._is_market_open", return_value=(True, "")), \
         patch("approval_bot.save_audit_log", side_effect=Exception("disk full")):
        result = await handle_approval_reply("A", cand_id, broker, db, bot, 123)

    assert result == "finalized"
    calls = [c[0][1] for c in bot.send_message.call_args_list]
    critical_msg = [m for m in calls if "CRITICAL" in m]
    assert len(critical_msg) == 1
    assert "ORD123" in critical_msg[0]

    status = await get_candidate_status(db, cand_id)
    assert status == "executed"


# ---- R2-H3: sell closes buy trade ----

@pytest.mark.asyncio
async def test_sell_closes_buy_trade(db):
    msg_db_id = await save_message(db, 123, 2001, "Buy RELIANCE", "2026-08-28T10:00:00")
    buy_sig_id = await save_signal(db, msg_db_id, {
        "symbol": "RELIANCE", "exchange": "NSE", "action": "BUY",
        "entry_min": 1482.0, "entry_max": 1490.0, "stop_loss": 1455.0,
        "targets": [1525.0, 1550.0], "confidence": 0.87, "reasoning": "test",
    })
    buy_cand_id = await save_trade_candidate(
        db, buy_sig_id, "RELIANCE", 3, 4458.0, 1455.0, 1486.0, 1482.0, 1490.0,
    )
    await update_candidate_status(db, buy_cand_id, "executed")
    trade_id = await save_trade(
        db, buy_cand_id, "RELIANCE", "NSE", "BUY", 3, 1486.0, "ORD_BUY", 10.0,
    )

    buy_trade_id = await get_open_buy_trade(db, "RELIANCE")
    assert buy_trade_id == trade_id

    sell_msg_id = await save_message(db, 123, 2002, "Sell RELIANCE", "2026-08-28T11:00:00")
    sell_sig_id = await save_signal(db, sell_msg_id, {
        "symbol": "RELIANCE", "exchange": "NSE", "action": "SELL",
        "entry_min": 1520.0, "entry_max": 1530.0, "stop_loss": None,
        "targets": [1500.0], "confidence": 0.8, "reasoning": "take profit",
    })
    sell_cand_id = await save_trade_candidate(
        db, sell_sig_id, "RELIANCE", 3, 4590.0, 0, 1530.0, 1520.0, 1530.0,
    )

    broker = _make_broker(price=1525.0)
    broker.get_positions.return_value = [
        Position(security_id="2885", symbol="RELIANCE", exchange="NSE", net_qty=3, avg_price=1486.0),
    ]
    bot = _make_bot()

    with patch("approval_bot._is_market_open", return_value=(True, "")):
        result = await handle_approval_reply("A", sell_cand_id, broker, db, bot, 123)

    assert result == "finalized"

    cursor = await db.execute("SELECT status FROM trades WHERE id = ?", (trade_id,))
    row = await cursor.fetchone()
    assert row[0] == "closed"


# ---- R2-H4: low balance still sends card (validation passes) ----

@pytest.mark.asyncio
async def test_low_balance_still_sends_card(db):
    from datetime import datetime, timezone
    broker = _make_broker(balance=500, price=1486.0)
    signal = {
        "symbol": "RELIANCE", "exchange": "NSE", "action": "BUY",
        "entry_min": 1482.0, "entry_max": 1490.0, "stop_loss": 1455.0,
        "targets": [1525.0, 1550.0], "confidence": 0.87,
    }
    timestamp = datetime.now(timezone.utc).isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False), \
         patch("risk_engine.get_today_trade_count", return_value=0):
        result = await validate_signal(signal, 123, broker, db, timestamp)

    assert result.valid is True


# ---- R2-H2: exchange validation rejects invalid ----

@pytest.mark.asyncio
async def test_exchange_validation_rejects_invalid():
    llm_response = json.dumps({
        "symbol": "GOLD", "exchange": "MCX", "action": "BUY",
        "entry_min": 50000.0, "entry_max": 50500.0, "stop_loss": 49000.0,
        "targets": [51000.0], "confidence": 0.85, "reasoning": "gold bullish",
    })
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_openrouter_response(llm_response)

    result = await extract_trade(
        "Buy GOLD above 50000", context_messages=[], api_key="k",
        model="test-model", http_client=client,
    )
    assert result is None


# ---- R2-M2: targets validation normalizes ----

@pytest.mark.asyncio
async def test_targets_validation_normalizes():
    client = AsyncMock(spec=httpx.AsyncClient)

    base = {
        "symbol": "RELIANCE", "exchange": "NSE", "action": "BUY",
        "entry_min": 1482.0, "entry_max": 1490.0, "stop_loss": 1455.0,
        "confidence": 0.87, "reasoning": "test",
    }

    null_targets = {**base, "targets": None}
    client.post.return_value = _mock_openrouter_response(json.dumps(null_targets))
    r = await extract_trade("Buy RELIANCE", [], "k", "m", client)
    assert r is not None
    assert r["targets"] == []

    none_str_targets = {**base, "targets": "none"}
    client.post.return_value = _mock_openrouter_response(json.dumps(none_str_targets))
    r = await extract_trade("Buy RELIANCE", [], "k", "m", client)
    assert r is not None
    assert r["targets"] == []

    mixed_targets = {**base, "targets": [-1, 1500, 99999999]}
    client.post.return_value = _mock_openrouter_response(json.dumps(mixed_targets))
    r = await extract_trade("Buy RELIANCE", [], "k", "m", client)
    assert r is not None
    assert -1 not in r["targets"]
    assert 99999999 not in r["targets"]
    assert 1500 in r["targets"]


# ---- R2-M4: verify_order_fill buy with existing position ----

@pytest.mark.asyncio
async def test_verify_order_fill_buy_with_existing_position():
    broker = _make_broker()
    bot = _make_bot()

    unchanged = Position(security_id="2885", symbol="RELIANCE", exchange="NSE", net_qty=10, avg_price=1486.0)
    filled = Position(security_id="2885", symbol="RELIANCE", exchange="NSE", net_qty=15, avg_price=1486.0)
    broker.get_positions.side_effect = [
        [unchanged],
        [filled],
    ]

    with patch("approval_bot.VERIFY_INTERVAL_SECONDS", 0), \
         patch("approval_bot.VERIFY_MAX_ATTEMPTS", 2):
        await verify_order_fill("RELIANCE", 5, "BUY", broker, bot, 123, pre_order_qty=10)

    calls = [c[0][1] for c in bot.send_message.call_args_list]
    filled_msgs = [m for m in calls if "FILLED" in m]
    assert len(filled_msgs) == 1
    assert "BUY" in filled_msgs[0]
    assert "x5" in filled_msgs[0]


# ---- R2-M4: verify_order_fill partial sell ----

@pytest.mark.asyncio
async def test_verify_order_fill_partial_sell():
    broker = _make_broker()
    bot = _make_bot()

    sold = Position(security_id="2885", symbol="RELIANCE", exchange="NSE", net_qty=10, avg_price=1486.0)
    broker.get_positions.return_value = [sold]

    with patch("approval_bot.VERIFY_INTERVAL_SECONDS", 0), \
         patch("approval_bot.VERIFY_MAX_ATTEMPTS", 1):
        await verify_order_fill("RELIANCE", 10, "SELL", broker, bot, 123, pre_order_qty=20)

    calls = [c[0][1] for c in bot.send_message.call_args_list]
    filled_msgs = [m for m in calls if "FILLED" in m]
    assert len(filled_msgs) == 1
    assert "SELL" in filled_msgs[0]


# ---- R2-M3: pending resend clears old mappings ----

@pytest.mark.asyncio
async def test_pending_resend_clears_old_mappings(db):
    cand_id = await _seed_candidate(db)
    _msg_to_candidate[100] = cand_id

    bot = _make_bot()
    await send_approval(bot, 123, cand_id, "card text", db)

    assert 100 not in _msg_to_candidate or _msg_to_candidate.get(100) == cand_id
    assert _msg_to_candidate[999] == cand_id


# ---- R2-M11: double approval race condition ----

@pytest.mark.asyncio
async def test_double_approval_race_condition(db):
    cand_id = await _seed_candidate(db)
    await update_candidate_status(db, cand_id, "executed")

    broker = _make_broker(price=1486.0)
    bot = _make_bot()

    with patch("approval_bot._is_market_open", return_value=(True, "")):
        result = await handle_approval_reply("A", cand_id, broker, db, bot, 123)

    assert result == "finalized"
    broker.place_order.assert_not_called()
    calls = [c[0][1] for c in bot.send_message.call_args_list]
    assert any("Already" in m for m in calls)


# ---- R2-L6: sanitize_source strips URLs ----

def test_sanitize_source_strips_urls():
    text = "Check https://example.com/foo and http://bar.org/baz for details"
    result = _sanitize_source(text)
    assert "https://" not in result
    assert "http://" not in result
    assert "[link removed]" in result

    long_text = "A" * 300
    result = _sanitize_source(long_text)
    assert len(result) <= 204  # 200 + "..."
    assert result.endswith("...")


# ---- R2-M6: limit price has buffer ----

@pytest.mark.asyncio
async def test_limit_price_has_buffer(db):
    buy_cand_id = await _seed_candidate(db, entry_min=1482.0, entry_max=1490.0, action="BUY")
    broker = _make_broker(price=1486.0)
    bot = _make_bot()

    with patch("approval_bot._is_market_open", return_value=(True, "")):
        await handle_approval_reply("A", buy_cand_id, broker, db, bot, 123)

    order = broker.place_order.call_args[0][0]
    assert order.limit_price > 1486.0

    sell_msg_id = await save_message(db, 123, 3001, "Sell RELIANCE", "2026-08-28T10:00:00")
    sell_sig_id = await save_signal(db, sell_msg_id, {
        "symbol": "RELIANCE", "exchange": "NSE", "action": "SELL",
        "entry_min": 1482.0, "entry_max": 1490.0, "stop_loss": None,
        "targets": [1500.0], "confidence": 0.8, "reasoning": "sell",
    })
    sell_cand_id = await save_trade_candidate(
        db, sell_sig_id, "RELIANCE", 3, 4458.0, 0, 1486.0, 1482.0, 1490.0,
    )
    broker2 = _make_broker(price=1486.0)
    broker2.get_positions.return_value = [
        Position(security_id="2885", symbol="RELIANCE", exchange="NSE", net_qty=3, avg_price=1486.0),
    ]
    bot2 = _make_bot()

    with patch("approval_bot._is_market_open", return_value=(True, "")):
        await handle_approval_reply("A", sell_cand_id, broker2, db, bot2, 123)

    sell_order = broker2.place_order.call_args[0][0]
    assert sell_order.limit_price < 1486.0


# ---- R2-M13: cost tracker reset between tests ----

@pytest.mark.asyncio
async def test_cost_tracker_reset_between_tests():
    assert _cost_tracker["calls"] == 0
    assert _cost_tracker["total_tokens"] == 0

    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_openrouter_response('{"is_tip": true, "confidence": 0.9}')

    await detect_signal("Buy RELIANCE", "k", "m", client)

    assert _cost_tracker["calls"] == 1
    assert _cost_tracker["total_tokens"] == 10


# ---- R2-M9: HTTP error from openrouter ----

@pytest.mark.asyncio
async def test_http_error_from_openrouter():
    error_response = httpx.Response(
        429,
        json={"error": "rate limited"},
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = error_response

    with pytest.raises(httpx.HTTPStatusError):
        await detect_signal("Buy RELIANCE", "k", "m", client)
