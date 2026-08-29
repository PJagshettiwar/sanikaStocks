import math
from datetime import datetime, timedelta, timezone

from brokers.base import BrokerInterface, Order
from risk_engine import ValidationResult
from config import FIXED_ALLOCATION_AMOUNT
from db import (
    get_pending_candidate, update_candidate_status,
    save_decision, save_audit_log, save_trade,
    set_telegram_msg_id, get_all_pending_candidates,
)

APPROVE_WORDS = {"a", "approve", "yes", "y"}
REJECT_WORDS = {"r", "reject", "no", "n"}

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


def format_trade_card(candidate: dict, validation: ValidationResult, original_message: str, wallet_balance: float = 0, held_symbols: set[str] | None = None) -> str:
    targets = sorted(validation.targets)
    price = validation.current_price

    target_lines = []
    for i, t in enumerate(targets, 1):
        pct = round((t - price) / price * 100, 1) if price else 0
        target_lines.append(f"T{i}: {t:,.0f} ({pct:+.1f}%)")
    targets_str = " | ".join(target_lines)

    sl_line = ""
    if validation.stop_loss:
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
            age_min = int((datetime.utcnow() - dt).total_seconds() / 60)
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
        f"Allocation: Fixed 5,000/trade\n"
        f"Wallet: {wallet_balance:,.0f}\n"
        f"Signal age: {age_str}\n\n"
        f"Source:\n\"{original_message}\"\n\n"
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
        await bot_client.send_message(chat_id, f"Rejected: {candidate['symbol']}")
        return "finalized"

    is_open, reason = _is_market_open()
    if not is_open:
        await bot_client.send_message(chat_id, f"{reason}. Reply A again during market hours.")
        return "market_closed"

    try:
        balance = await broker.get_balance()
    except Exception as e:
        await bot_client.send_message(chat_id, f"Broker unavailable: {e}\nReply A again when broker is back.")
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
        await bot_client.send_message(chat_id, f"Quote unavailable for {candidate['symbol']}: {e}\nReply A again to retry.")
        return "error"

    entry_min = candidate["entry_min"]
    entry_max = candidate["entry_max"]

    qty = math.floor(FIXED_ALLOCATION_AMOUNT / quote.price)
    if qty < 1:
        await bot_client.send_message(chat_id, f"Price {quote.price:,.0f} exceeds allocation {FIXED_ALLOCATION_AMOUNT:,.0f}.")
        return "error"
    amount = round(qty * quote.price, 2)

    if quote.price < entry_min or quote.price > entry_max:
        sl_str = f"SL: {candidate['stop_loss']:,.0f}\n" if candidate['stop_loss'] else ""
        reapproval_card = (
            f"--- PRICE CHANGED ---\n\n"
            f"{candidate['symbol']} ({candidate['exchange']})\n"
            f"Original entry: {entry_min:,.0f} - {entry_max:,.0f}\n"
            f"Current price: {quote.price:,.2f}\n"
            f"{sl_str}"
            f"New qty: {qty} | Amount: {amount:,.0f}\n"
            f"Wallet: {balance:,.0f}\n\n"
            f"Reply to this message: A to approve at current price, R to reject\n"
            f"---"
        )
        msg = await bot_client.send_message(chat_id, reapproval_card)
        _remove_pending(candidate_id)
        _msg_to_candidate[msg.id] = candidate_id
        await set_telegram_msg_id(db_conn, candidate_id, msg.id)
        return "reapproval_sent"

    try:
        instruments = await broker.get_instruments()
        security_id = instruments.get(candidate["symbol"])
        order = Order(
            symbol=candidate["symbol"],
            exchange=candidate["exchange"],
            security_id=security_id,
            txn_type=candidate["action"],
            qty=qty,
            order_type="LIMIT",
            limit_price=quote.price,
            product="CNC",
            validity="DAY",
        )
        await save_decision(db_conn, candidate_id, "approve", quote.price)
        result = await broker.place_order(order)
    except Exception as e:
        await bot_client.send_message(chat_id, f"Order failed for {candidate['symbol']}: {e}")
        return "error"

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
    await update_candidate_status(db_conn, candidate_id, "executed")
    _remove_pending(candidate_id)
    await bot_client.send_message(
        chat_id,
        f"Order placed: {candidate['symbol']} {candidate['action']} x{order.qty} @ {quote.price:,.2f}\n"
        f"Amount: {amount:,.0f} | Wallet: {balance - amount:,.0f}\n"
        f"Order ID: {result.order_id}",
    )
    return "finalized"
