import aiosqlite
import pytest

from db import (
    init_db,
    save_message,
    get_last_message_id,
    get_recent_messages,
    save_signal,
    save_trade_candidate,
    get_pending_candidate,
    get_all_pending_candidates,
    update_candidate_status,
    save_decision,
    save_audit_log,
    save_trade,
    get_portfolio_summary,
    has_duplicate_signal,
    close_trade,
    get_symbol_pnl,
)


@pytest.fixture
async def db_conn():
    conn = await aiosqlite.connect(":memory:")
    await init_db(conn)
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_init_db_creates_tables(db_conn):
    cursor = await db_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in await cursor.fetchall()]
    assert "messages" in tables
    assert "signals" in tables
    assert "trade_candidates" in tables
    assert "decisions" in tables
    assert "audit_log" in tables


@pytest.mark.asyncio
async def test_save_and_get_message(db_conn):
    msg_id = await save_message(
        db_conn, channel_id=123, message_id=456, text="Buy RELIANCE", timestamp="2026-08-28T10:00:00"
    )
    assert msg_id is not None
    last_id = await get_last_message_id(db_conn, channel_id=123)
    assert last_id == 456


@pytest.mark.asyncio
async def test_save_message_duplicate_returns_none(db_conn):
    first_id = await save_message(
        db_conn, channel_id=1, message_id=100, text="First", timestamp="2026-08-28T10:00:00"
    )
    assert first_id is not None
    second_id = await save_message(
        db_conn, channel_id=1, message_id=100, text="First", timestamp="2026-08-28T10:00:00"
    )
    assert second_id is None


@pytest.mark.asyncio
async def test_get_last_message_id_no_messages(db_conn):
    last_id = await get_last_message_id(db_conn, channel_id=999)
    assert last_id is None


@pytest.mark.asyncio
async def test_get_recent_messages(db_conn):
    for i in range(1, 4):
        await save_message(db_conn, channel_id=7, message_id=i, text=f"msg{i}", timestamp="2026-08-28T10:00:00")
    recent = await get_recent_messages(db_conn, channel_id=7, limit=2)
    assert len(recent) == 2
    assert recent[0]["message_id"] == 3
    assert recent[1]["message_id"] == 2


@pytest.mark.asyncio
async def test_save_signal_and_trade_candidate_flow(db_conn):
    message_db_id = await save_message(
        db_conn, channel_id=1, message_id=1, text="Buy RELIANCE", timestamp="2026-08-28T10:00:00"
    )
    signal_id = await save_signal(
        db_conn,
        message_db_id,
        {
            "symbol": "RELIANCE",
            "exchange": "NSE",
            "action": "BUY",
            "entry_min": 100.0,
            "entry_max": 105.0,
            "stop_loss": 90.0,
            "targets": [110.0, 120.0],
            "allocation_pct": 10.0,
            "confidence": 0.8,
            "reasoning": "strong momentum",
        },
    )
    assert signal_id is not None

    candidate_id = await save_trade_candidate(
        db_conn,
        signal_id,
        symbol="RELIANCE",
        quantity=10,
        amount=1000.0,
        stop_loss=90.0,
        current_price=102.0,
        entry_min=100.0,
        entry_max=105.0,
    )
    assert candidate_id is not None

    candidate = await get_pending_candidate(db_conn, candidate_id)
    assert candidate is not None
    assert candidate["symbol"] == "RELIANCE"
    assert candidate["status"] == "pending"
    assert candidate["channel_id"] == 1

    await update_candidate_status(db_conn, candidate_id, "approved")
    candidate_after = await get_pending_candidate(db_conn, candidate_id)
    assert candidate_after is None

    await save_decision(db_conn, candidate_id, "approved", 102.0)
    await save_audit_log(db_conn, candidate_id, "notify", {"a": 1}, {"b": 2})

    is_dup = await has_duplicate_signal(db_conn, "RELIANCE", channel_id=1, hours=24)
    assert is_dup is True

    is_dup_other_channel = await has_duplicate_signal(db_conn, "RELIANCE", channel_id=999, hours=24)
    assert is_dup_other_channel is False


@pytest.mark.asyncio
async def test_get_pending_candidate_missing_returns_none(db_conn):
    candidate = await get_pending_candidate(db_conn, 9999)
    assert candidate is None


@pytest.mark.asyncio
async def test_portfolio_summary_with_open_and_closed_trades(db_conn):
    """M2: Verify get_portfolio_summary works (CASE WHEN, not FILTER)."""
    msg_id = await save_message(db_conn, 1, 1, "Buy", "2026-08-28T10:00:00")
    sig_id = await save_signal(db_conn, msg_id, {
        "symbol": "RELIANCE", "exchange": "NSE", "action": "BUY",
        "entry_min": 100, "entry_max": 105, "stop_loss": 90,
        "targets": [110], "confidence": 0.8,
    })
    cid = await save_trade_candidate(db_conn, sig_id, "RELIANCE", 10, 1050, 90, 102, 100, 105)
    await save_trade(db_conn, cid, "RELIANCE", "NSE", "BUY", 10, 105, "ORD1", 10)

    msg_id2 = await save_message(db_conn, 1, 2, "Buy2", "2026-08-28T11:00:00")
    sig_id2 = await save_signal(db_conn, msg_id2, {
        "symbol": "TCS", "exchange": "NSE", "action": "BUY",
        "entry_min": 200, "entry_max": 210, "stop_loss": 180,
        "targets": [220], "confidence": 0.9,
    })
    cid2 = await save_trade_candidate(db_conn, sig_id2, "TCS", 5, 1050, 180, 200, 200, 210)
    trade_id2 = await save_trade(db_conn, cid2, "TCS", "NSE", "BUY", 5, 210, "ORD2", 10)

    from db import close_trade
    await close_trade(db_conn, trade_id2, 230, "SELL1", 10)

    summary = await get_portfolio_summary(db_conn)
    assert summary["open_trades"] == 1
    assert summary["closed_trades"] == 1
    assert summary["total_pnl"] == 80.0
    assert summary["invested"] == 1050.0


@pytest.mark.asyncio
async def test_get_all_pending_candidates_returns_full_data(db_conn):
    """M17: Verify the 3-table JOIN returns all expected fields."""
    msg_id = await save_message(db_conn, 42, 100, "Buy INFY above 1500", "2026-08-28T10:00:00")
    sig_id = await save_signal(db_conn, msg_id, {
        "symbol": "INFY", "exchange": "NSE", "action": "BUY",
        "entry_min": 1490, "entry_max": 1510, "stop_loss": 1450,
        "targets": [1550, 1600], "confidence": 0.85,
    })
    cid = await save_trade_candidate(db_conn, sig_id, "INFY", 3, 4530, 1450, 1500, 1490, 1510)

    rows = await get_all_pending_candidates(db_conn)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == cid
    assert row["symbol"] == "INFY"
    assert row["exchange"] == "NSE"
    assert row["action"] == "BUY"
    assert row["entry_min"] == 1490
    assert row["entry_max"] == 1510
    assert row["stop_loss"] == 1450
    assert row["quantity"] == 3
    assert row["original_message"] == "Buy INFY above 1500"

    await update_candidate_status(db_conn, cid, "executed")
    rows_after = await get_all_pending_candidates(db_conn)
    assert len(rows_after) == 0


@pytest.mark.asyncio
async def test_close_trade_calculates_pnl(db_conn):
    msg_id = await save_message(db_conn, 1, 1, "Buy", "2026-08-28T10:00:00")
    sig_id = await save_signal(db_conn, msg_id, {
        "symbol": "TCS", "exchange": "NSE", "action": "BUY",
        "entry_min": 200, "entry_max": 210, "stop_loss": 180,
        "targets": [220], "confidence": 0.9,
    })
    cid = await save_trade_candidate(db_conn, sig_id, "TCS", 5, 1050, 180, 200, 200, 210)
    trade_id = await save_trade(db_conn, cid, "TCS", "NSE", "BUY", 5, 210, "ORD1", 10)

    result = await close_trade(db_conn, trade_id, 230, "SELL1", 10)
    assert result["pnl"] == 80.0
    assert result["pnl_pct"] == 7.62


@pytest.mark.asyncio
async def test_close_trade_nonexistent_returns_none(db_conn):
    result = await close_trade(db_conn, 9999, 100)
    assert result is None


@pytest.mark.asyncio
async def test_get_symbol_pnl_aggregates_from_last_buy(db_conn):
    msg_id = await save_message(db_conn, 1, 1, "Buy", "2026-08-28T10:00:00")
    sig_id = await save_signal(db_conn, msg_id, {
        "symbol": "RELIANCE", "exchange": "NSE", "action": "BUY",
        "entry_min": 100, "entry_max": 105, "stop_loss": 90,
        "targets": [110], "confidence": 0.8,
    })
    cid = await save_trade_candidate(db_conn, sig_id, "RELIANCE", 10, 1050, 90, 102, 100, 105)
    t1 = await save_trade(db_conn, cid, "RELIANCE", "NSE", "BUY", 10, 105, "ORD1", 10)
    await close_trade(db_conn, t1, 115, "SELL1", 10)

    msg_id2 = await save_message(db_conn, 1, 2, "Buy2", "2026-08-28T11:00:00")
    sig_id2 = await save_signal(db_conn, msg_id2, {
        "symbol": "RELIANCE", "exchange": "NSE", "action": "BUY",
        "entry_min": 110, "entry_max": 115, "stop_loss": 100,
        "targets": [120], "confidence": 0.9,
    })
    cid2 = await save_trade_candidate(db_conn, sig_id2, "RELIANCE", 5, 575, 100, 112, 110, 115)
    t2 = await save_trade(db_conn, cid2, "RELIANCE", "NSE", "BUY", 5, 115, "ORD2", 10)
    await close_trade(db_conn, t2, 125, "SELL2", 10)

    result = await get_symbol_pnl(db_conn, "RELIANCE")
    assert result["trade_count"] == 1
    assert result["total_pnl"] == 30.0
