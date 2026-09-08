"""
Test script to validate INDstocks broker connection with a real order.
Authenticates, resolves symbol, gets quote, and places order with confirmation.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from brokers.indstocks import INDstocksBroker
from brokers.base import Order


async def main():
    broker = INDstocksBroker(
        client_id=os.environ["INDSTOCKS_CLIENT_ID"],
        totp_secret=os.environ["INDSTOCKS_TOTP_SECRET"],
        mpin=os.environ["INDSTOCKS_MPIN"],
    )

    # Step 1: Authenticate
    print("1. Authenticating...")
    import httpx
    import pyotp
    totp_code = pyotp.TOTP(os.environ["INDSTOCKS_TOTP_SECRET"]).now()
    print(f"   Client ID: {os.environ['INDSTOCKS_CLIENT_ID']}")
    print(f"   TOTP code: {totp_code}")
    try:
        token = await broker.authenticate()
        print(f"   Token: {token[:20]}...")
    except httpx.HTTPStatusError as e:
        print(f"   HTTP {e.response.status_code}: {e.response.text}")
        return

    # Step 2: Check balance
    print("\n2. Checking wallet balance...")
    balance = await broker.get_balance()
    print(f"   Available balance: {balance:,.2f}")

    # Step 3: Resolve symbol — debug instrument CSV
    symbol = "WOCKPHARMA"
    exchange = "NSE"
    print(f"\n3. Resolving {symbol}...")

    import csv as csv_mod, io as io_mod
    await broker._ensure_auth()
    raw_resp = await broker._request("GET", f"https://api.indstocks.com/market/instruments", params={"source": "equity"})
    reader = csv_mod.DictReader(io_mod.StringIO(raw_resp.text))
    rows = list(reader)
    print(f"   CSV headers: {rows[0].keys() if rows else 'empty'}")
    wock_rows = [r for r in rows if "WOCK" in r.get("TRADING_SYMBOL", "").upper()]
    for r in wock_rows:
        print(f"   RAW: {dict(r)}")

    instruments = await broker.get_instruments()
    matches = {k: v for k, v in instruments.items() if "WOCK" in k.upper()}
    print(f"   Filtered matches: {matches}")
    sec_id = instruments.get(symbol)
    if not sec_id:
        print(f"   ERROR: {symbol} not found in instruments.")
        return
    print(f"   Security ID: {sec_id}")

    # Step 4: Get live quote
    print(f"\n4. Getting live quote for {symbol} (scrip: {exchange}_{sec_id})...")
    try:
        quote = await broker.get_quote(symbol, exchange)
    except httpx.HTTPStatusError as e:
        print(f"   Quote failed: HTTP {e.response.status_code}: {e.response.text}")
        return
    print(f"   Price: {quote.price:,.2f}")
    print(f"   Day High: {quote.day_high:,.2f}")
    print(f"   Day Low: {quote.day_low:,.2f}")
    print(f"   Volume: {quote.volume:,}")

    # Step 5: Build order
    txn_type = "SELL" if "--sell" in sys.argv else "BUY"
    if txn_type == "SELL":
        positions = await broker.get_positions()
        held = next((p for p in positions if p.symbol == symbol), None)
        if not held or held.net_qty <= 0:
            print(f"   No position held for {symbol}")
            return
        qty = held.net_qty
        print(f"   Selling held position: {qty} shares")
    else:
        qty = 2
    order = Order(
        symbol=symbol,
        exchange=exchange,
        security_id=sec_id,
        txn_type=txn_type,
        qty=qty,
        order_type="MARKET",
        limit_price=None,
        product="CNC",
        validity="DAY",
    )
    total_cost = quote.price * qty
    print(f"\n5. Order details:")
    print(f"   {order.txn_type} {order.qty}x {order.symbol} ({order.exchange})")
    print(f"   Type: {order.order_type} | Product: {order.product}")
    print(f"   Estimated value: {total_cost:,.2f}")
    print(f"   Wallet balance: {balance:,.2f}")

    # Step 6: Confirm and place
    if "--confirm" not in sys.argv:
        print("\n   Dry run. Pass --confirm to place the order.")
        return

    print("\n6. Placing order...")
    result = await broker.place_order(order)
    print(f"   Order ID: {result.order_id}")
    print(f"   Status: {result.status}")

    # Step 7: Check positions
    print("\n7. Checking positions...")
    positions = await broker.get_positions()
    for p in positions:
        print(f"   {p.symbol} ({p.exchange}): qty={p.net_qty}, avg_price={p.avg_price:,.2f}")
    if not positions:
        print("   No positions found (order may still be processing)")


asyncio.run(main())
