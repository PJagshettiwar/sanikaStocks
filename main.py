import asyncio
import json
import logging
import math
import os
from datetime import datetime, timezone

import aiosqlite
import httpx
from telethon import TelegramClient, events

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from config import FIXED_ALLOCATION_AMOUNT
from db import (
    init_db, get_recent_messages, save_signal, save_trade_candidate,
    get_all_pending_candidates, update_candidate_status,
    get_total_api_cost, get_api_cost_summary, cleanup_old_audit_logs,
)
from telegram_reader import fetch_new_messages
from stock_agent import analyze_message, set_cost_db
from risk_engine import validate_signal, ValidationResult
from approval_bot import (
    format_trade_card, send_approval, handle_approval_reply,
    load_pending_from_db, get_candidate_for_msg, parse_approval_reply,
    _remove_pending,
)
from brokers.indstocks import INDstocksBroker, RateLimitError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("stock_agent")

user_client: TelegramClient = None
bot_client: TelegramClient = None
db_conn: aiosqlite.Connection = None
http_client: httpx.AsyncClient = None
broker: INDstocksBroker = None

last_poll_at: str | None = None
last_poll_message_count: int = 0

HELP_TEXT = (
    "Commands:\n"
    "/status - health check (broker, LLM, Telegram)\n"
    "/pending - resend all pending trade cards with live prices\n"
    "/cancel <symbol> - cancel a pending trade\n"
    "/costs - show LLM API usage and costs\n"
    "/help - show this message"
)


async def poll_channels():
    global last_poll_at, last_poll_message_count
    if user_client is None:
        log.warning("Skipping poll: user client not connected")
        return
    log.info("Polling %d channels...", len(config.WATCHED_CHANNELS))

    messages = await fetch_new_messages(user_client, db_conn, config.WATCHED_CHANNELS)
    log.info("Fetched %d new messages", len(messages))
    last_poll_at = datetime.now(timezone.utc).isoformat()
    last_poll_message_count = len(messages)

    for msg in messages:
        try:
            context = await get_recent_messages(db_conn, msg["channel_id"], limit=5)
            context_texts = [m["text"] for m in context if m["message_id"] != msg["message_id"]]

            signal = await analyze_message(
                msg["text"], context_texts,
                api_key=config.OPENROUTER_API_KEY,
                tier1_model=config.TIER1_MODEL,
                tier2_model=config.TIER2_MODEL,
                http_client=http_client,
            )
            if not signal:
                continue

            log.info("Signal detected: %s %s", signal.get("action"), signal.get("symbol"))

            signal_id = await save_signal(db_conn, msg["db_id"], signal)

            validation = await validate_signal(
                signal, msg["channel_id"], broker, db_conn, msg["timestamp"],
            )
            if not validation.valid:
                log.info("Signal rejected: %s", validation.reason)
                continue

            balance = await broker.get_balance()
            positions = await broker.get_positions()
            held = {p.symbol for p in positions}

            candidate_id = await save_trade_candidate(
                db_conn, signal_id, validation.symbol, validation.quantity,
                validation.amount, validation.stop_loss, validation.current_price,
                validation.entry_min, validation.entry_max,
            )

            card = format_trade_card(
                candidate={"id": candidate_id, "created_at": msg["timestamp"]},
                validation=validation,
                original_message=msg["text"],
                wallet_balance=balance,
                held_symbols=held,
            )
            await send_approval(bot_client, config.APPROVAL_CHAT_ID, candidate_id, card, db_conn)
            log.info("Approval sent for candidate #%d: %s", candidate_id, validation.symbol)
        except Exception as e:
            log.error("Error processing message %s: %s", msg.get("message_id"), e, exc_info=True)
            reason = str(e)[:200]
            try:
                await bot_client.send_message(
                    config.APPROVAL_CHAT_ID,
                    f"Poll error on msg {msg.get('message_id')}:\n{reason}",
                )
            except Exception:
                pass


async def handle_pending_command():
    rows = await get_all_pending_candidates(db_conn)
    if not rows:
        await bot_client.send_message(config.APPROVAL_CHAT_ID, "No pending trades.")
        return

    try:
        balance = await broker.get_balance()
    except Exception:
        balance = 0

    try:
        positions = await broker.get_positions()
        held = {p.symbol for p in positions}
    except Exception:
        held = set()

    await bot_client.send_message(config.APPROVAL_CHAT_ID, f"Resending {len(rows)} pending card(s) with live prices...")

    for row in rows:
        _remove_pending(row["id"])
        targets = json.loads(row["targets"]) if isinstance(row["targets"], str) else row["targets"]
        try:
            quote = await broker.get_quote(row["symbol"], row["exchange"])
            price = quote.price
        except Exception:
            price = row["entry_max"]

        qty = max(1, math.floor(FIXED_ALLOCATION_AMOUNT / price))
        validation = ValidationResult(
            valid=True, reason="ok",
            symbol=row["symbol"], exchange=row["exchange"], action=row["action"],
            entry_min=row["entry_min"], entry_max=row["entry_max"],
            stop_loss=row["stop_loss"] or 0, targets=targets,
            current_price=price, quantity=qty,
            amount=round(qty * price, 2),
        )
        candidate = {"id": row["id"], "created_at": row["created_at"]}
        card = format_trade_card(candidate, validation, row["original_message"],
                                 wallet_balance=balance, held_symbols=held)
        await send_approval(bot_client, config.APPROVAL_CHAT_ID, row["id"], card, db_conn)


async def handle_status_command():
    checks = []

    checks.append("Telegram: connected")

    try:
        bal = await broker.get_balance()
        checks.append(f"Broker (INDstocks): connected (balance: {bal:,.0f})")
    except Exception as e:
        try:
            await broker.authenticate()
            checks.append("Broker (INDstocks): reconnected")
        except Exception as e2:
            checks.append(f"Broker (INDstocks): DOWN ({e2})")

    try:
        resp = await http_client.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {config.OPENROUTER_API_KEY}"},
            timeout=10,
        )
        if resp.status_code == 200:
            checks.append("LLM (OpenRouter): connected")
        else:
            checks.append(f"LLM (OpenRouter): error {resp.status_code}")
    except Exception as e:
        checks.append(f"LLM (OpenRouter): DOWN ({e})")

    pending = await get_all_pending_candidates(db_conn)
    checks.append(f"Pending trades: {len(pending)}")

    if last_poll_at:
        checks.append(f"Last poll: {last_poll_at} ({last_poll_message_count} msgs)")
    else:
        checks.append("Last poll: not yet run")

    await bot_client.send_message(config.APPROVAL_CHAT_ID, "\n".join(checks))


async def handle_cancel_command(text: str):
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await bot_client.send_message(config.APPROVAL_CHAT_ID, "Usage: /cancel SYMBOL")
        return

    symbol = parts[1].strip().upper()
    rows = await get_all_pending_candidates(db_conn)
    matched = [r for r in rows if r["symbol"] == symbol]

    if not matched:
        await bot_client.send_message(config.APPROVAL_CHAT_ID, f"No pending trade for {symbol}.")
        return

    for row in matched:
        await update_candidate_status(db_conn, row["id"], "cancelled")
        _remove_pending(row["id"])
        if row.get("telegram_msg_id"):
            try:
                await bot_client.edit_message(
                    config.APPROVAL_CHAT_ID, row["telegram_msg_id"],
                    f"--- CANCELLED: {symbol} ---",
                )
            except Exception:
                pass

    await bot_client.send_message(config.APPROVAL_CHAT_ID, f"Cancelled {len(matched)} pending trade(s) for {symbol}.")


async def handle_costs_command():
    totals = await get_total_api_cost(db_conn)
    summary = await get_api_cost_summary(db_conn)

    lines = [
        f"--- LLM COSTS ---",
        f"Total calls: {totals['total_calls']}",
        f"Total cost: ${totals['total_cost_usd']:.4f}",
        "",
    ]
    if summary:
        lines.append("By model:")
        for row in summary:
            lines.append(f"  {row['model']}: {row['calls']} calls, {row['tokens'] or 0} tokens, ${row['cost'] or 0:.4f}")

    await bot_client.send_message(config.APPROVAL_CHAT_ID, "\n".join(lines))


async def main():
    global user_client, bot_client, db_conn, http_client, broker

    db_conn = None
    http_client = None
    try:
        db_conn = await aiosqlite.connect("data/agent.db")
        http_client = httpx.AsyncClient(timeout=30)
        await init_db(db_conn)
        set_cost_db(db_conn)
        await cleanup_old_audit_logs(db_conn)

        cooldown_file = "data/.auth_cooldown"
        if os.path.exists(cooldown_file):
            try:
                with open(cooldown_file) as f:
                    cooldown_ts = float(f.read().strip())
                age = datetime.now(timezone.utc).timestamp() - cooldown_ts
                if age < 1800:
                    log.error("Auth cooldown active (%.0fs remaining). Exiting cleanly.", 1800 - age)
                    return
            except (ValueError, OSError):
                pass
            try:
                os.remove(cooldown_file)
            except OSError:
                pass

        broker = INDstocksBroker(
            client_id=config.INDSTOCKS_CLIENT_ID,
            totp_secret=config.INDSTOCKS_TOTP_SECRET,
            mpin=config.INDSTOCKS_MPIN,
            http_client=http_client,
        )
        max_auth_attempts = 3
        for attempt in range(max_auth_attempts):
            try:
                await broker.authenticate()
                break
            except RateLimitError as e:
                log.warning("Broker rate limited: %s. Writing cooldown marker.", e)
                with open(cooldown_file, "w") as f:
                    f.write(str(datetime.now(timezone.utc).timestamp()))
                return
            except Exception as e:
                delay = min(30 * (2 ** attempt), 120)
                log.warning("Broker auth failed (attempt %d/%d): %s. Retrying in %ds",
                            attempt + 1, max_auth_attempts, e, delay)
                if attempt == max_auth_attempts - 1:
                    raise
                await asyncio.sleep(delay)

        bot_client = TelegramClient(
            "data/approval_bot",
            config.TELEGRAM_API_ID,
            config.TELEGRAM_API_HASH,
        )
        await bot_client.start(bot_token=config.TELEGRAM_BOT_TOKEN)
        log.info("Bot client connected")

        await bot_client.send_message(
            config.APPROVAL_CHAT_ID,
            "Stock agent started. Send /status for health check.",
        )
        log.info("Startup notification sent to Telegram")

        try:
            user_client = TelegramClient(
                config.TELEGRAM_SESSION_NAME,
                config.TELEGRAM_API_ID,
                config.TELEGRAM_API_HASH,
            )
            await user_client.start()
            log.info("User client connected")
        except EOFError:
            log.error("Telegram user session missing or expired. Channel polling disabled.")
            await bot_client.send_message(
                config.APPROVAL_CHAT_ID,
                "WARNING: Telegram user session invalid. Channel polling disabled.\n"
                "Run session setup script on server to fix.",
            )
            user_client = None

        pending_count = await load_pending_from_db(db_conn)
        log.info("Loaded %d pending candidates from DB", pending_count)

        if pending_count > 0:
            await bot_client.send_message(
                config.APPROVAL_CHAT_ID,
                f"{pending_count} pending trade(s) awaiting approval.\nSend /pending to review them.",
            )

        bot_me = await bot_client.get_me()

        @bot_client.on(events.NewMessage(chats=config.APPROVAL_CHAT_ID))
        async def on_message(event):
            if event.sender_id == bot_me.id:
                return

            text = (event.text or "").strip()

            if text == "/pending":
                await handle_pending_command()
                return
            if text == "/status":
                await handle_status_command()
                return
            if text.startswith("/cancel"):
                await handle_cancel_command(text)
                return
            if text == "/costs":
                await handle_costs_command()
                return
            if text == "/help":
                await bot_client.send_message(config.APPROVAL_CHAT_ID, HELP_TEXT)
                return

            if text.startswith("/"):
                await bot_client.send_message(config.APPROVAL_CHAT_ID, f"Unknown command. Send /help for options.")
                return

            if not event.reply_to:
                return

            reply_to_msg_id = event.reply_to.reply_to_msg_id
            candidate_id = get_candidate_for_msg(reply_to_msg_id)
            if candidate_id is None:
                return

            if parse_approval_reply(text) is None:
                await bot_client.send_message(
                    config.APPROVAL_CHAT_ID,
                    "Reply A to approve, R to reject.",
                )
                return

            status = await handle_approval_reply(
                text, candidate_id, broker, db_conn,
                bot_client, config.APPROVAL_CHAT_ID,
            )
            log.info("Approval reply for #%d: %s", candidate_id, status)

        scheduler = AsyncIOScheduler()
        scheduler.add_job(poll_channels, "interval", minutes=config.POLL_INTERVAL_MINUTES)
        scheduler.add_job(handle_pending_command, "cron", hour=9, minute=15, timezone="Asia/Kolkata")
        scheduler.start()
        log.info("Scheduler started (every %d min, morning notify 09:15 IST)", config.POLL_INTERVAL_MINUTES)

        await poll_channels()

        log.info("Listening for approval replies...")
        await bot_client.run_until_disconnected()
    finally:
        if http_client:
            await http_client.aclose()
        if db_conn:
            await db_conn.close()
        log.info("Connections closed")


if __name__ == "__main__":
    asyncio.run(main())
