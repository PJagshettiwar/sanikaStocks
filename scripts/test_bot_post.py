import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

import aiosqlite
import config
from db import init_db
from approval_bot import format_trade_card
from risk_engine import ValidationResult
from telethon import TelegramClient

db_path = os.path.join(os.path.dirname(__file__), "..", "agent.db")


def build_sample_card():
    validation = ValidationResult(
        valid=True,
        reason="ok",
        symbol="HINDCOPPER",
        exchange="NSE",
        action="BUY",
        quantity=15,
        amount=4770.0,
        stop_loss=290.0,
        entry_min=310.0,
        entry_max=325.0,
        targets=[350.0, 380.0, 410.0],
        current_price=318.0,
    )
    candidate = {
        "id": 0,
        "created_at": "2026-08-29T10:30:00",
    }
    original = (
        "Long-Term : Buy Hind Copper  @ 310 - 325\n\n"
        "1st Target: 350\n"
        "2nd Target: 380\n"
        "3rd Target: 410\n\n"
        "SL: 290\n\n"
        "Holding: 2 - 4 Years"
    )
    return format_trade_card(candidate, validation, original)


async def post_from_db(bot, chat_id):
    if not os.path.exists(db_path):
        print("No agent.db found, skipping DB signals")
        return False

    async with aiosqlite.connect(db_path) as conn:
        await init_db(conn)
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute("""
            SELECT s.*, m.text FROM signals s
            JOIN messages m ON s.message_id = m.id
            ORDER BY s.id DESC LIMIT 1
        """)
        row = await cursor.fetchone()
        if not row:
            print("No signals in DB, skipping DB post")
            return False

        signal = dict(row)
        targets = json.loads(signal["targets"]) if isinstance(signal["targets"], str) else signal["targets"]

        validation = ValidationResult(
            valid=True,
            reason="ok",
            symbol=signal["symbol"],
            exchange=signal["exchange"],
            action=signal["action"],
            entry_min=signal["entry_min"],
            entry_max=signal["entry_max"],
            stop_loss=signal["stop_loss"] or 0,
            targets=targets,
            current_price=signal["entry_max"],
            quantity=max(1, int(5000 / signal["entry_max"])),
            amount=signal["entry_max"] * max(1, int(5000 / signal["entry_max"])),
        )
        candidate = {
            "id": signal["id"],
            "created_at": signal["created_at"],
        }
        card = format_trade_card(candidate, validation, signal["text"])
        msg = await bot.send_message(chat_id, card)
        print(f"DB signal posted (message ID: {msg.id})")
        return True


async def main():
    bot = TelegramClient(
        "approval_bot",
        config.TELEGRAM_API_ID,
        config.TELEGRAM_API_HASH,
    )
    await bot.start(bot_token=config.TELEGRAM_BOT_TOKEN)
    chat_id = config.APPROVAL_CHAT_ID

    me = await bot.get_me()
    print(f"Bot connected: @{me.username} ({me.first_name})")

    card = build_sample_card()
    msg = await bot.send_message(chat_id, card)
    print(f"Sample card posted (message ID: {msg.id})")

    posted_db = await post_from_db(bot, chat_id)
    if not posted_db:
        print("Only sample card was posted (no DB signals available)")

    print(f"\nCheck Telegram chat {chat_id} for the message(s).")
    await bot.disconnect()


asyncio.run(main())
