import asyncio
import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone

from brokers.base import BrokerInterface, Order
from config import FIXED_ALLOCATION_AMOUNT
from risk_engine import ValidationResult
from db import (
    get_pending_candidate, update_candidate_status,
    save_decision, save_audit_log, save_trade,
    set_telegram_msg_id, get_all_pending_candidates,
    get_symbol_pnl,
)

APPROVE_WORDS = {"a", "approve", "yes", "y"}
REJECT_WORDS = {"r", "reject", "no", "n"}
log = logging.getLogger("approval_bot")
_URL_RE = re.compile(r"https?://\S+")

IST = timezone(timedelta(hours=5, minutes=30))

# msg_id -> candidate_id (persisted in DB, rebuilt on startup)
_msg_to_candidate: dict[int, int] = {}


def _is_market_open() -> tuple[bool, str]:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False, "Market closed: weekend"
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now < market_open or now > market_close:
        return False, f"Market closed: IST {now.strftime('%H:%M')}"
    return True, ""


def _sanitize_source(text: str, max_len: int = 200) -> str:
    cleaned = _URL_RE.sub("[link removed]", text)
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len] + "..."
    return cleaned


def format_trade_card(candidate: dict, validation: ValidationResult, original_message: str, wallet_balance: float = 0, held_symbols: set[str] | None = None) -> str:
    targets = sorted(validation.targets)
    price = validation.current_price

    target_lines = []
    for i, t in enumerate(targets, 1):
        pct = round((t - price) / price * 100, 1) if price else 0
        target_lines.append(f"T{i}: {t:,.0f} ({pct:+.1f}%)")
    targets_str = " | ".join(target_lines)

    sl_line = ""
    if validation.stop_loss is not None:
        sl_pct = round((validation.stop_loss - price) / price * 100, 1) if price else 0
        sl_line = f"SL: {validation.stop_loss:,.0f} ({sl_pct:+.1f}%)\n"

    no_action = ""
    if validation.action == "SELL" and held_symbols is not None and validation.symbol not in held_symbols:
        no_action = "No action - not in portfolio\n\n"

    age_str = "N/A"
    created = candidate.get("created_at")
    if created:
        try:
            dt = datetime.fromisoformat(created)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_min = int((datetime.now(timezone.utc) - dt).total_seconds() / 60)
            age_str = f"{age_min} min"
        except (ValueError, TypeError):
            pass

    qty = validation.quantity
    amount = round(qty * price, 2) if qty else 0

    return (
        f"--- TRADE CANDIDATE ---\n\n"
        f"{validation.symbol} ({validation.exchange}) - {validation.action}\n"
        f"{no_action}"
        f"Entry: {validation.entry_min:,.0f} - {validation.entry_max:,.0f}\n"
        f"Current Price: {price:,.2f}\n"
        f"{sl_line}"
        f"Targets: {targets_str}\n\n"
        f"Qty: {qty} | Amount: {amount:,.0f}\n"
        f"Allocation: Fixed {FIXED_ALLOCATION_AMOUNT:,.0f}/trade\n"
        f"Wallet: {wallet_balance:,.0f}\n"
        f"Signal age: {age_str}\n\n"
        f"Source:\n\"{_sanitize_source(original_message)}\"\n\n"
        f"Reply to this message: A to approve, R to reject\n"
        f"---"
    )


def parse_approval_reply(text: str) -> str | None:
    cleaned = text.strip().lower()
    if cleaned in APPROVE_WORDS:
        return "approve"
    if cleaned in REJECT_WORDS:
        return "reject"
    return None


async def load_pending_from_db(db_conn):
    _msg_to_candidate.clear()
    rows = await get_all_pending_candidates(db_conn)
    for row in rows:
        msg_id = row.get("telegram_msg_id")
        if msg_id:
            _msg_to_candidate[msg_id] = row["id"]
    return len(_msg_to_candidate)


def get_candidate_for_msg(reply_to_msg_id: int) -> int | None:
    return _msg_to_candidate.get(reply_to_msg_id)


async def send_approval(bot_client, chat_id: int, candidate_id: int, card_text: str, db_conn) -> None:
    msg = await bot_client.send_message(chat_id, card_text)
    _msg_to_candidate[msg.id] = candidate_id
    await set_telegram_msg_id(db_conn, candidate_id, msg.id)


def _remove_pending(candidate_id: int):
    to_remove = [k for k, v in _msg_to_candidate.items() if v == candidate_id]
    for k in to_remove:
        del _msg_to_candidate[k]


VERIFY_INTERVAL_SECONDS = 60
VERIFY_MAX_ATTEMPTS = 10


async def verify_order_fill(symbol, expected_qty, action, broker, bot_client, chat_id, pre_order_qty=0):
    try:
        for attempt in range(1, VERIFY_MAX_ATTEMPTS + 1):
            await asyncio.sleep(VERIFY_INTERVAL_SECONDS)
            try:
                positions = await broker.get_positions()
                held = next((p for p in positions if p.symbol == symbol), None)
                if action == "BUY" and held and held.net_qty >= pre_order_qty + expected_qty:
                    await bot_client.send_message(
                        chat_id,
                        f"Order FILLED: {symbol} BUY x{expected_qty} confirmed in portfolio "
                        f"(held: {held.net_qty} @ avg {held.avg_price:,.2f})",
                    )
                    return
                if action == "SELL" and (not held or held.net_qty <= pre_order_qty - expected_qty):
                    await bot_client.send_message(
                        chat_id,
                        f"Order FILLED: {symbol} SELL x{expected_qty} confirmed "
                        f"(remaining: {held.net_qty if held else 0})",
                    )
                    return
            except Exception as e:
                log.warning("Verify attempt %d/%d for %s failed: %s", attempt, VERIFY_MAX_ATTEMPTS, symbol, e)
        await bot_client.send_message(
            chat_id,
            f"Order NOT confirmed after {VERIFY_MAX_ATTEMPTS} min: {symbol} {action} x{expected_qty}. "
            f"Check broker manually.",
        )
    except Exception as e:
        log.error("verify_order_fill crashed for %s %s x%d: %s", symbol, action, expected_qty, e)


async def handle_approval_reply(text: str, candidate_id: int, broker: BrokerInterface, db_conn, bot_client, chat_id: int) -> str:
    decision = parse_approval_reply(text)
    if decision is None:
        await bot_client.send_message(chat_id, "Unrecognized. Reply A to approve, R to reject.")
        return "unrecognized"

    candidate = await get_pending_candidate(db_conn, candidate_id)
    if not candidate:
        from db import get_candidate_status
        status = await get_candidate_status(db_conn, candidate_id)
        if status:
            await bot_client.send_message(chat_id, f"Already {status}.")
        else:
            await bot_client.send_message(chat_id, "Trade not found.")
        _remove_pending(candidate_id)
        return "finalized"

    if decision == "reject":
        await update_candidate_status(db_conn, candidate_id, "rejected")
        await save_decision(db_conn, candidate_id, "reject", None)
        _remove_pending(candidate_id)
        await bot_client.send_message(
            chat_id,
            f"Rejected: {candidate.get('action', 'BUY')} {candidate['symbol']} ({candidate.get('exchange', '')}) "
            f"@ {candidate['entry_min']:,.0f}-{candidate['entry_max']:,.0f}",
        )
        return "finalized"

    is_open, reason = _is_market_open()
    if not is_open:
        await bot_client.send_message(chat_id, f"{reason}. Reply A again during market hours.")
        return "market_closed"

    try:
        balance = await broker.get_balance()
    except Exception as e:
        log.error("Broker balance check failed: %s", e)
        await bot_client.send_message(chat_id, "Broker unavailable. Reply A again when broker is back.")
        return "error"

    if balance < FIXED_ALLOCATION_AMOUNT:
        await bot_client.send_message(
            chat_id,
            f"Insufficient funds: {balance:,.0f} available, need {FIXED_ALLOCATION_AMOUNT:,.0f}.\nAdd funds and reply A again to this card.",
        )
        return "insufficient_funds"

    try:
        quote = await broker.get_quote(candidate["symbol"], candidate["exchange"])
    except Exception as e:
        log.error("Quote fetch failed for %s: %s", candidate["symbol"], e)
        await bot_client.send_message(chat_id, f"Quote unavailable for {candidate['symbol']}. Reply A again to retry.")
        return "error"

    entry_min = candidate["entry_min"]
    entry_max = candidate["entry_max"]

    qty = math.floor(FIXED_ALLOCATION_AMOUNT / quote.price)
    if qty < 1:
        await bot_client.send_message(chat_id, f"Price {quote.price:,.0f} exceeds allocation {FIXED_ALLOCATION_AMOUNT:,.0f}.")
        return "error"
    amount = round(qty * quote.price, 2)

    if quote.price < entry_min or quote.price > entry_max:
        sl_str = f"SL: {candidate['stop_loss']:,.0f}\n" if candidate['stop_loss'] is not None else ""
        raw_targets = candidate.get("targets", "[]")
        targets = json.loads(raw_targets) if isinstance(raw_targets, str) else raw_targets
        target_lines = []
        for i, t in enumerate(sorted(targets), 1):
            pct = round((t - quote.price) / quote.price * 100, 1) if quote.price else 0
            target_lines.append(f"T{i}: {t:,.0f} ({pct:+.1f}%)")
        targets_str = " | ".join(target_lines) if target_lines else "N/A"
        source = _sanitize_source(candidate.get("original_message", ""))
        reapproval_card = (
            f"--- PRICE CHANGED ---\n\n"
            f"{candidate['symbol']} ({candidate['exchange']}) - {candidate.get('action', 'BUY')}\n"
            f"Original entry: {entry_min:,.0f} - {entry_max:,.0f}\n"
            f"Current price: {quote.price:,.2f}\n"
            f"{sl_str}"
            f"Targets: {targets_str}\n"
            f"New qty: {qty} | Amount: {amount:,.0f}\n"
            f"Wallet: {balance:,.0f}\n\n"
            f"Source:\n\"{source}\"\n\n"
            f"Reply to this message: A to approve at current price, R to reject\n"
            f"---"
        )
        from db import update_candidate_entry_range
        margin = quote.price * 0.01
        await update_candidate_entry_range(db_conn, candidate_id, quote.price - margin, quote.price + margin)
        msg = await bot_client.send_message(chat_id, reapproval_card)
        _remove_pending(candidate_id)
        _msg_to_candidate[msg.id] = candidate_id
        await set_telegram_msg_id(db_conn, candidate_id, msg.id)
        return "reapproval_sent"

    from db import get_candidate_status as _get_status
    current_status = await _get_status(db_conn, candidate_id)
    if current_status != "pending":
        await bot_client.send_message(chat_id, f"Already {current_status}.")
        _remove_pending(candidate_id)
        return "finalized"

    pre_order_qty = 0
    try:
        positions = await broker.get_positions()
        held = next((p for p in positions if p.symbol == candidate["symbol"]), None)
        pre_order_qty = held.net_qty if held else 0
    except Exception:
        pass

    try:
        instruments = await broker.get_instruments()
        security_id = instruments.get(candidate["symbol"])
        limit_price = quote.price
        if candidate["action"] == "BUY":
            limit_price = round(quote.price * 1.002, 2)
        elif candidate["action"] == "SELL":
            limit_price = round(quote.price * 0.998, 2)
        order = Order(
            symbol=candidate["symbol"],
            exchange=candidate["exchange"],
            security_id=security_id,
            txn_type=candidate["action"],
            qty=qty,
            order_type="LIMIT",
            limit_price=limit_price,
            product="CNC",
            validity="DAY",
        )
        result = await broker.place_order(order)
        await save_decision(db_conn, candidate_id, "approve", quote.price)
    except Exception as e:
        log.error("Order failed for %s: %s", candidate["symbol"], e)
        await bot_client.send_message(chat_id, f"Order failed for {candidate['symbol']}. Check logs and reply A to retry.")
        return "error"

    try:
        await save_audit_log(
            db_conn, candidate_id, "place_order",
            {"symbol": order.symbol, "qty": order.qty, "price": order.limit_price},
            {"order_id": result.order_id, "status": result.status},
        )
        await save_trade(
            db_conn, trade_candidate_id=candidate_id, symbol=candidate["symbol"],
            exchange=candidate["exchange"], side=candidate["action"],
            quantity=order.qty, price=quote.price, order_id=result.order_id,
            broker_charges=10.0,
        )
        if candidate["action"] == "SELL":
            from db import get_open_buy_trade
            buy_trade_id = await get_open_buy_trade(db_conn, candidate["symbol"])
            if buy_trade_id:
                from db import close_trade
                await close_trade(db_conn, buy_trade_id, quote.price, result.order_id)
        await update_candidate_status(db_conn, candidate_id, "executed")
    except Exception as db_err:
        log.critical("DB write failed after order %s placed: %s", result.order_id, db_err)
        await bot_client.send_message(
            chat_id,
            f"CRITICAL: Order {result.order_id} placed but DB save failed. Do NOT re-approve. Check broker manually.",
        )
        try:
            await update_candidate_status(db_conn, candidate_id, "executed")
        except Exception:
            pass
        return "finalized"

    _remove_pending(candidate_id)
    confirm_lines = [
        f"Order placed: {candidate['symbol']} {candidate['action']} x{order.qty} @ {quote.price:,.2f}",
        f"Amount: {amount:,.0f} | Wallet: {balance - amount:,.0f}",
        f"Order ID: {result.order_id} | Status: {result.status}",
    ]
    if candidate["action"] == "SELL":
        try:
            pnl_data = await get_symbol_pnl(db_conn, candidate["symbol"])
            if pnl_data["trade_count"] > 0:
                sign = "+" if pnl_data["total_pnl"] >= 0 else ""
                confirm_lines.append(
                    f"P&L for {candidate['symbol']} ({pnl_data['trade_count']} trade(s)): "
                    f"{sign}{pnl_data['total_pnl']:,.0f} | Charges: {pnl_data['total_charges']:,.0f}"
                )
        except Exception:
            pass
    await bot_client.send_message(chat_id, "\n".join(confirm_lines))
    asyncio.create_task(verify_order_fill(
        candidate["symbol"], order.qty, candidate["action"],
        broker, bot_client, chat_id, pre_order_qty=pre_order_qty,
    ))
    return "finalized"
