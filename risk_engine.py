import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches

from db import has_duplicate_signal as _has_duplicate
from config import DEFAULT_STOP_LOSS_PCT, FIXED_ALLOCATION_AMOUNT, MAX_SIGNAL_AGE_MINUTES
from brokers.base import BrokerInterface

IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist() -> datetime:
    return datetime.now(IST)


@dataclass
class ValidationResult:
    valid: bool
    reason: str
    symbol: str = ""
    exchange: str = ""
    action: str = ""
    security_id: str | None = None
    quantity: int = 0
    amount: float = 0.0
    stop_loss: float = 0.0
    entry_min: float = 0.0
    entry_max: float = 0.0
    targets: list[float] = field(default_factory=list)
    current_price: float = 0.0


def _fail(reason):
    return ValidationResult(valid=False, reason=reason)


async def has_duplicate_signal(conn, symbol, channel_id, hours=24):
    return await _has_duplicate(conn, symbol, channel_id, hours)


def _resolve_symbol(symbol_from_llm: str, instruments: dict[str, str]) -> str | None:
    if symbol_from_llm in instruments:
        return symbol_from_llm
    upper = symbol_from_llm.upper()
    if upper in instruments:
        return upper
    collapsed = upper.replace(" ", "").replace("-", "")
    if collapsed in instruments:
        return collapsed
    matches = get_close_matches(collapsed, instruments.keys(), n=1, cutoff=0.8)
    if matches:
        return matches[0]
    return None


async def validate_signal(signal, channel_id, broker: BrokerInterface, db_conn, message_timestamp):
    instruments = await broker.get_instruments()
    resolved = _resolve_symbol(signal["symbol"], instruments)
    if not resolved:
        return _fail(f"Unknown symbol: {signal['symbol']}")
    security_id = instruments[resolved]

    stop_loss = signal.get("stop_loss")
    entry_max = signal["entry_max"]
    if stop_loss is None:
        stop_loss = round(entry_max * (1 - DEFAULT_STOP_LOSS_PCT / 100), 2)

    msg_time = datetime.fromisoformat(message_timestamp)
    age = datetime.utcnow() - msg_time
    if age > timedelta(minutes=MAX_SIGNAL_AGE_MINUTES):
        return _fail(f"Signal too old: {int(age.total_seconds() // 60)} min")

    now_ist = _now_ist()
    if now_ist.weekday() >= 5:
        return _fail("Market closed: weekend")
    market_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    if now_ist < market_open or now_ist > market_close:
        return _fail(f"Market closed: current IST time {now_ist.strftime('%H:%M')}")

    if await has_duplicate_signal(db_conn, resolved, channel_id):
        return _fail(f"Duplicate signal for {resolved} in last 24h")

    balance = await broker.get_balance()
    if balance < FIXED_ALLOCATION_AMOUNT:
        return _fail(f"Insufficient funds: balance {balance:,.0f}, need {FIXED_ALLOCATION_AMOUNT:,.0f}. Please add funds.")
    quantity = math.floor(FIXED_ALLOCATION_AMOUNT / entry_max)
    if quantity < 1:
        return _fail(f"Price too high for allocation: {entry_max:,.0f} > {FIXED_ALLOCATION_AMOUNT:,.0f}")
    amount = round(quantity * entry_max, 2)

    quote = await broker.get_quote(resolved, signal["exchange"])

    return ValidationResult(
        valid=True,
        reason="All checks passed",
        symbol=resolved,
        exchange=signal["exchange"],
        action=signal["action"],
        security_id=security_id,
        quantity=quantity,
        amount=amount,
        stop_loss=stop_loss,
        entry_min=signal["entry_min"],
        entry_max=entry_max,
        targets=signal.get("targets", []),
        current_price=quote.price,
    )
