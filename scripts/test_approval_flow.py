"""
Reproduce the exact approval flow code path for a given symbol.
Tests: instrument resolution, quote, order construction, and placement.
"""
import asyncio
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from brokers.indstocks import INDstocksBroker
from brokers.base import Order
from config import FIXED_ALLOCATION_AMOUNT


async def main():
    broker = INDstocksBroker(
        client_id=os.environ["INDSTOCKS_CLIENT_ID"],
        totp_secret=os.environ["INDSTOCKS_TOTP_SECRET"],
        mpin=os.environ["INDSTOCKS_MPIN"],
    )

    symbol = "GABRIEL"
    exchange = "NSE"
    action = "BUY"

    print("1. Authenticating...")
    try:
        await broker.authenticate()
        print("   OK")
    except Exception as e:
        print(f"   FAILED: {e}")
        return

    print(f"\n2. Resolving {symbol}...")
    instruments = await broker.get_instruments()
    security_id = instruments.get(symbol)
    print(f"   security_id = {security_id!r}")
    if security_id is None:
        matches = {k: v for k, v in instruments.items() if "GABRIEL" in k.upper()}
        print(f"   Partial matches: {matches}")
        print("   THIS IS THE BUG: security_id is None, approval flow passes it to Order anyway")
        return

    print(f"\n3. Getting quote...")
    try:
        quote = await broker.get_quote(symbol, exchange)
        print(f"   Price: {quote.price:,.2f}")
    except Exception as e:
        print(f"   FAILED: {e}")
        return

    print(f"\n4. Building order (same as approval_bot.py:280-298)...")
    qty = math.floor(FIXED_ALLOCATION_AMOUNT / quote.price)
    if qty < 1:
        print(f"   qty=0, price too high")
        return
    limit_price = round(quote.price * 1.002, 2)
    order = Order(
        symbol=symbol,
        exchange=exchange,
        security_id=security_id,
        txn_type=action,
        qty=qty,
        order_type="LIMIT",
        limit_price=limit_price,
        product="CNC",
        validity="DAY",
    )
    print(f"   {order.txn_type} {order.qty}x {order.symbol} @ LIMIT {order.limit_price}")
    print(f"   security_id={order.security_id}, exchange={order.exchange}")

    if "--confirm" not in sys.argv:
        print("\n   Dry run. Pass --confirm to place the order.")
        return

    print(f"\n5. Placing order...")
    try:
        result = await broker.place_order(order)
        print(f"   Order ID: {result.order_id}")
        print(f"   Status: {result.status}")
    except Exception as e:
        print(f"   FAILED: {type(e).__name__}: {e}")


asyncio.run(main())
