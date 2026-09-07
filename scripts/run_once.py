"""Fetch the latest message from one channel, run the full pipeline, wait for approval.

Stops at approval instead of placing the order. For testing when the market is closed.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

import aiosqlite
import httpx
from telethon import TelegramClient, events

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from db import init_db, save_message, save_signal, save_trade_candidate
from stock_agent import analyze_message
from risk_engine import validate_signal
from approval_bot import format_trade_card, send_approval, parse_approval_reply, get_candidate_for_msg

CHANNEL = sys.argv[1] if len(sys.argv) > 1 else "sanika_post_bot"
if CHANNEL.lstrip("-").isdigit():
    CHANNEL = int(CHANNEL)


def line(s=""):
    print(s, flush=True)


async def main(state):
    os.makedirs("data", exist_ok=True)
    conn = state["conn"] = await aiosqlite.connect("data/agent.db")
    await init_db(conn)

    user = state["user"] = TelegramClient(config.TELEGRAM_SESSION_NAME, config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH)
    await user.connect()
    if not await user.is_user_authorized():
        line("No Telegram session. Run: .venv/bin/python scripts/create_session.py")
        return

    bot = state["bot"] = TelegramClient("approval_bot", config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH)
    await bot.start(bot_token=config.TELEGRAM_BOT_TOKEN)

    from brokers.indstocks import INDstocksBroker
    broker = INDstocksBroker(config.INDSTOCKS_CLIENT_ID, config.INDSTOCKS_TOTP_SECRET, config.INDSTOCKS_MPIN)
    http = state["http"] = httpx.AsyncClient()

    line("1. FETCH")
    entity = await user.get_entity(CHANNEL)
    line(f"   chat: {getattr(entity, 'title', None) or getattr(entity, 'username', CHANNEL)} (id={entity.id})")
    msgs = [m async for m in user.iter_messages(entity, limit=1) if m.text]
    if not msgs:
        line("   no text messages")
        return
    msg = msgs[0]
    age_min = int((datetime.now(timezone.utc) - msg.date).total_seconds() // 60)
    line(f"   msg {msg.id}, {msg.date}, {age_min} min old")
    for l in msg.text.splitlines():
        line(f"   | {l}")

    line()
    line("2. LLM PIPELINE")
    signal = await analyze_message(msg.text[:1000], [], config.TIER1_MODEL, config.TIER2_MODEL, http)
    if not signal:
        line("   not a tradeable signal, nothing to approve")
        return
    line(f"   {signal}")

    line()
    line("3. RISK ENGINE")
    ts = str(msg.date)
    result = await validate_signal(signal, entity.id, broker, conn, ts)
    line(f"   {result.reason}")
    if not result.valid and ("too old" in result.reason or "Duplicate" in result.reason):
        line("   re-running with a current timestamp so the approval path still gets exercised")
        result = await validate_signal(signal, entity.id, broker, conn,
                                       datetime.now(timezone.utc).isoformat())
        line(f"   {result.reason}")
    if not result.valid:
        return
    line(f"   {result.action} {result.quantity} {result.symbol} @ {result.current_price} = {result.amount}")

    line()
    line("4. APPROVAL CARD")
    balance = await broker.get_balance()
    held = {p.symbol for p in await broker.get_positions()}
    msg_db_id = await save_message(conn, entity.id, msg.id, msg.text[:1000], ts)
    signal_id = await save_signal(conn, msg_db_id, signal)
    cid = await save_trade_candidate(conn, signal_id, result.symbol, result.quantity, result.amount,
                                     result.stop_loss, result.current_price, result.entry_min, result.entry_max)
    card = format_trade_card({"id": cid, "created_at": ts}, result, msg.text, balance, held)
    await send_approval(bot, config.APPROVAL_CHAT_ID, cid, card, conn)
    line(f"   sent candidate #{cid} to chat {config.APPROVAL_CHAT_ID}")

    line()
    line("5. WAITING FOR YOUR REPLY (5 min timeout)")
    done = asyncio.Event()

    @bot.on(events.NewMessage(chats=config.APPROVAL_CHAT_ID))
    async def on_reply(event):
        decision = parse_approval_reply(event.text or "")
        if decision is None:
            return
        target = get_candidate_for_msg(event.message.reply_to_msg_id) if event.message.reply_to_msg_id else cid
        if target != cid:
            return
        line(f"   got: {decision}")
        await event.reply(f"Test run: {decision} received. Not placing the order, market is closed.")
        done.set()

    try:
        await asyncio.wait_for(done.wait(), timeout=300)
        line("   done, stopped before placing any order")
    except asyncio.TimeoutError:
        line("   timed out, no reply")


async def run():
    state = {}
    try:
        await main(state)
    finally:
        for closer in ("http", "conn", "user", "bot"):
            obj = state.get(closer)
            if obj is None:
                continue
            close = getattr(obj, "aclose", None) or getattr(obj, "close", None) or getattr(obj, "disconnect", None)
            try:
                r = close()
                if asyncio.iscoroutine(r):
                    await r
            except Exception:
                pass


asyncio.run(run())
