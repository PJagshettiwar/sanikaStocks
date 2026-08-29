import aiosqlite
import json
from datetime import datetime, timezone


async def init_db(conn: aiosqlite.Connection) -> None:
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            processed INTEGER DEFAULT 0,
            UNIQUE(channel_id, message_id)
        );
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL REFERENCES messages(id),
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL,
            action TEXT NOT NULL,
            entry_min REAL NOT NULL,
            entry_max REAL NOT NULL,
            stop_loss REAL,
            targets TEXT NOT NULL,
            allocation_pct REAL,
            confidence REAL NOT NULL,
            reasoning TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS trade_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL REFERENCES signals(id),
            symbol TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            amount REAL NOT NULL,
            stop_loss REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            current_price_at_send REAL,
            entry_min REAL NOT NULL,
            entry_max REAL NOT NULL,
            telegram_msg_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            decided_at TEXT
        );
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_candidate_id INTEGER NOT NULL REFERENCES trade_candidates(id),
            decision TEXT NOT NULL,
            decided_at TEXT NOT NULL DEFAULT (datetime('now')),
            price_at_decision REAL
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_candidate_id INTEGER,
            action TEXT NOT NULL,
            request TEXT,
            response TEXT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS api_costs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service TEXT NOT NULL,
            model TEXT,
            endpoint TEXT,
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0,
            context TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_candidate_id INTEGER REFERENCES trade_candidates(id),
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            price REAL NOT NULL,
            amount REAL NOT NULL,
            order_id TEXT,
            broker_charges REAL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'open',
            sell_price REAL,
            sell_amount REAL,
            sell_order_id TEXT,
            sell_charges REAL DEFAULT 0,
            pnl REAL,
            pnl_pct REAL,
            opened_at TEXT NOT NULL DEFAULT (datetime('now')),
            closed_at TEXT
        );
    """)
    try:
        await conn.execute("ALTER TABLE trade_candidates ADD COLUMN telegram_msg_id INTEGER")
    except Exception:
        pass
    await conn.commit()


async def save_message(conn, channel_id, message_id, text, timestamp):
    cursor = await conn.execute(
        "INSERT OR IGNORE INTO messages (channel_id, message_id, text, timestamp) VALUES (?, ?, ?, ?)",
        (channel_id, message_id, text, str(timestamp)),
    )
    await conn.commit()
    if cursor.rowcount == 0:
        return None
    return cursor.lastrowid


async def get_last_message_id(conn, channel_id):
    cursor = await conn.execute(
        "SELECT MAX(message_id) FROM messages WHERE channel_id = ?",
        (channel_id,),
    )
    row = await cursor.fetchone()
    return row[0] if row and row[0] else None


async def get_recent_messages(conn, channel_id, limit=5):
    cursor = await conn.execute(
        "SELECT message_id, text, timestamp FROM messages WHERE channel_id = ? ORDER BY message_id DESC LIMIT ?",
        (channel_id, limit),
    )
    rows = await cursor.fetchall()
    return [{"message_id": r[0], "text": r[1], "timestamp": r[2]} for r in rows]


async def save_signal(conn, message_db_id, signal_data):
    cursor = await conn.execute(
        """INSERT INTO signals (message_id, symbol, exchange, action, entry_min, entry_max, stop_loss, targets, allocation_pct, confidence, reasoning)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            message_db_id,
            signal_data["symbol"],
            signal_data["exchange"],
            signal_data["action"],
            signal_data["entry_min"],
            signal_data["entry_max"],
            signal_data["stop_loss"],
            json.dumps(signal_data["targets"]),
            signal_data.get("allocation_pct"),
            signal_data["confidence"],
            signal_data.get("reasoning"),
        ),
    )
    await conn.commit()
    return cursor.lastrowid


async def save_trade_candidate(conn, signal_id, symbol, quantity, amount, stop_loss, current_price, entry_min, entry_max):
    cursor = await conn.execute(
        """INSERT INTO trade_candidates (signal_id, symbol, quantity, amount, stop_loss, current_price_at_send, entry_min, entry_max)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (signal_id, symbol, quantity, amount, stop_loss, current_price, entry_min, entry_max),
    )
    await conn.commit()
    return cursor.lastrowid


async def get_pending_candidate(conn, candidate_id):
    cursor = await conn.execute(
        """SELECT tc.*, s.exchange, s.action, s.targets, s.reasoning, s.confidence, s.allocation_pct,
                  m.text as original_message, m.channel_id
           FROM trade_candidates tc
           JOIN signals s ON tc.signal_id = s.id
           JOIN messages m ON s.message_id = m.id
           WHERE tc.id = ? AND tc.status = 'pending'""",
        (candidate_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


async def update_candidate_status(conn, candidate_id, status, decided_at=None):
    decided_at = decided_at or datetime.now(timezone.utc).isoformat()
    await conn.execute(
        "UPDATE trade_candidates SET status = ?, decided_at = ? WHERE id = ?",
        (status, decided_at, candidate_id),
    )
    await conn.commit()


async def save_decision(conn, candidate_id, decision, price_at_decision):
    await conn.execute(
        "INSERT INTO decisions (trade_candidate_id, decision, price_at_decision) VALUES (?, ?, ?)",
        (candidate_id, decision, price_at_decision),
    )
    await conn.commit()


async def save_audit_log(conn, candidate_id, action, request_data, response_data):
    await conn.execute(
        "INSERT INTO audit_log (trade_candidate_id, action, request, response) VALUES (?, ?, ?, ?)",
        (candidate_id, action, json.dumps(request_data), json.dumps(response_data)),
    )
    await conn.commit()


async def save_api_cost(conn, service, model, endpoint, prompt_tokens, completion_tokens, total_tokens, cost_usd, context=None):
    await conn.execute(
        """INSERT INTO api_costs (service, model, endpoint, prompt_tokens, completion_tokens, total_tokens, cost_usd, context)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (service, model, endpoint, prompt_tokens, completion_tokens, total_tokens, cost_usd, context),
    )
    await conn.commit()


async def get_api_cost_summary(conn, since=None):
    where = "WHERE created_at > ?" if since else ""
    params = (since,) if since else ()
    cursor = await conn.execute(
        f"""SELECT service, model, COUNT(*) as calls, SUM(total_tokens) as tokens, SUM(cost_usd) as cost
            FROM api_costs {where} GROUP BY service, model""",
        params,
    )
    return [dict(zip([d[0] for d in cursor.description], row)) for row in await cursor.fetchall()]


async def get_total_api_cost(conn):
    cursor = await conn.execute("SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM api_costs")
    row = await cursor.fetchone()
    return {"total_cost_usd": row[0], "total_calls": row[1]}


async def save_trade(conn, trade_candidate_id, symbol, exchange, side, quantity, price, order_id=None, broker_charges=0):
    amount = round(quantity * price, 2)
    cursor = await conn.execute(
        """INSERT INTO trades (trade_candidate_id, symbol, exchange, side, quantity, price, amount, order_id, broker_charges)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (trade_candidate_id, symbol, exchange, side, quantity, price, amount, order_id, broker_charges),
    )
    await conn.commit()
    return cursor.lastrowid


async def close_trade(conn, trade_id, sell_price, sell_order_id=None, sell_charges=0):
    cursor = await conn.execute("SELECT quantity, price, broker_charges FROM trades WHERE id = ?", (trade_id,))
    row = await cursor.fetchone()
    if not row:
        return None
    quantity, buy_price, buy_charges = row
    sell_amount = round(quantity * sell_price, 2)
    buy_amount = round(quantity * buy_price, 2)
    total_charges = buy_charges + sell_charges
    pnl = round(sell_amount - buy_amount - total_charges, 2)
    pnl_pct = round((pnl / buy_amount) * 100, 2) if buy_amount > 0 else 0
    await conn.execute(
        """UPDATE trades SET status = 'closed', sell_price = ?, sell_amount = ?, sell_order_id = ?,
           sell_charges = ?, pnl = ?, pnl_pct = ?, closed_at = datetime('now') WHERE id = ?""",
        (sell_price, sell_amount, sell_order_id, sell_charges, pnl, pnl_pct, trade_id),
    )
    await conn.commit()
    return {"pnl": pnl, "pnl_pct": pnl_pct}


async def get_symbol_pnl(conn, symbol):
    cursor = await conn.execute(
        """SELECT id FROM trades
           WHERE symbol = ? AND side = 'BUY' AND status = 'closed'
           ORDER BY id DESC LIMIT 1""",
        (symbol,),
    )
    anchor = await cursor.fetchone()
    if not anchor:
        where_clause = "WHERE symbol = ? AND status = 'closed' AND opened_at > datetime('now', '-1 year')"
        params = (symbol,)
    else:
        where_clause = "WHERE symbol = ? AND status = 'closed' AND id >= ?"
        params = (symbol, anchor[0])

    cursor = await conn.execute(
        f"""SELECT COUNT(*) as trade_count,
                   COALESCE(SUM(pnl), 0) as total_pnl,
                   COALESCE(SUM(broker_charges + sell_charges), 0) as total_charges
            FROM trades
            {where_clause}""",
        params,
    )
    row = await cursor.fetchone()
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


async def get_trade_history(conn, limit=50):
    cursor = await conn.execute(
        """SELECT * FROM trades ORDER BY opened_at DESC LIMIT ?""", (limit,)
    )
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in await cursor.fetchall()]


async def get_portfolio_summary(conn):
    cursor = await conn.execute("""
        SELECT
            SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open_trades,
            SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) as closed_trades,
            COALESCE(SUM(CASE WHEN status = 'closed' THEN pnl ELSE 0 END), 0) as total_pnl,
            COALESCE(SUM(CASE WHEN status = 'open' THEN amount ELSE 0 END), 0) as invested,
            COALESCE(SUM(broker_charges + sell_charges), 0) as total_charges
        FROM trades
    """)
    row = await cursor.fetchone()
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


async def get_candidate_status(conn, candidate_id):
    cursor = await conn.execute(
        "SELECT status FROM trade_candidates WHERE id = ?", (candidate_id,),
    )
    row = await cursor.fetchone()
    return row[0] if row else None


async def set_telegram_msg_id(conn, candidate_id, telegram_msg_id):
    await conn.execute(
        "UPDATE trade_candidates SET telegram_msg_id = ? WHERE id = ?",
        (telegram_msg_id, candidate_id),
    )
    await conn.commit()


async def get_all_pending_candidates(conn):
    cursor = await conn.execute(
        """SELECT tc.id, tc.telegram_msg_id, tc.symbol, s.exchange, tc.entry_min, tc.entry_max,
                  tc.stop_loss, tc.quantity, s.action, s.targets, m.text as original_message, tc.created_at
           FROM trade_candidates tc
           JOIN signals s ON tc.signal_id = s.id
           JOIN messages m ON s.message_id = m.id
           WHERE tc.status = 'pending'""",
    )
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in await cursor.fetchall()]


async def get_today_trade_count(conn):
    cursor = await conn.execute(
        """SELECT COUNT(*) FROM trade_candidates
           WHERE status IN ('approved', 'executed')
             AND created_at > datetime('now', 'start of day')""",
    )
    row = await cursor.fetchone()
    return row[0]


async def has_duplicate_signal(conn, symbol, channel_id, hours=24):
    cursor = await conn.execute(
        """SELECT COUNT(*) FROM signals s
           JOIN messages m ON s.message_id = m.id
           JOIN trade_candidates tc ON s.id = tc.signal_id
           WHERE s.symbol = ? AND m.channel_id = ?
             AND tc.status IN ('pending', 'approved', 'executed')
             AND s.created_at > datetime('now', ? || ' hours')""",
        (symbol, channel_id, f"-{hours}"),
    )
    row = await cursor.fetchone()
    return row[0] > 0
