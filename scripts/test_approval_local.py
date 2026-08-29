import asyncio
import aiosqlite
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
from db import init_db

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
db_path = os.path.join(os.path.dirname(__file__), "..", "agent.db")


def format_card(signal):
    targets = json.loads(signal["targets"]) if isinstance(signal["targets"], str) else signal["targets"]
    targets_str = " / ".join(f"{t:,.0f}" for t in targets)

    risk_str = "N/A"
    if targets and signal["stop_loss"]:
        reward = targets[0] - signal["entry_max"]
        risk_amt = signal["entry_max"] - signal["stop_loss"]
        if risk_amt > 0:
            risk_str = f"{round(reward / risk_amt, 1)}x"

    sl_str = f"{signal['stop_loss']:,.0f}" if signal["stop_loss"] else "Not set (default 15%)"

    return (
        f"\n{'='*50}\n"
        f"TRADE CANDIDATE (Signal #{signal['id']})\n"
        f"{'='*50}\n\n"
        f"Original message:\n\"{signal['text'][:200]}...\"\n\n"
        f"Symbol:      {signal['symbol']} ({signal['exchange']})\n"
        f"Action:      {signal['action']}\n"
        f"Entry range: {signal['entry_min']:,.2f} - {signal['entry_max']:,.2f}\n"
        f"Stop-loss:   {sl_str}\n"
        f"Targets:     {targets_str}\n"
        f"Confidence:  {int(signal['confidence'] * 100)}%\n"
        f"Reasoning:   {signal.get('reasoning', 'N/A')}\n"
        f"R/R to T1:   {risk_str}\n\n"
        f"[A]pprove  [R]eject  [S]kip\n"
        f"{'-'*50}"
    )


async def main():
    async with aiosqlite.connect(db_path) as conn:
        await init_db(conn)
        conn.row_factory = aiosqlite.Row

        cursor = await conn.execute("""
            SELECT s.*, m.text FROM signals s
            JOIN messages m ON s.message_id = m.id
            ORDER BY s.id
        """)
        signals = [dict(row) for row in await cursor.fetchall()]

        print(f"\nFound {len(signals)} trade signals to review\n")

        approved = []
        rejected = []

        for signal in signals:
            print(format_card(signal))

            while True:
                choice = input("\nYour decision: ").strip().lower()
                if choice in ("a", "approve"):
                    print(f"\n>>> APPROVED: {signal['symbol']} {signal['action']}")
                    print(f"    [DUMMY] Would place order: BUY {signal['symbol']} @ {signal['entry_max']}")
                    approved.append(signal)
                    break
                elif choice in ("r", "reject"):
                    print(f"\n>>> REJECTED: {signal['symbol']}")
                    rejected.append(signal)
                    break
                elif choice in ("s", "skip"):
                    print(f"\n>>> SKIPPED: {signal['symbol']}")
                    break
                else:
                    print("Invalid input. Enter A, R, or S.")

        print(f"\n{'='*50}")
        print(f"SUMMARY")
        print(f"{'='*50}")
        print(f"Approved: {len(approved)}")
        for s in approved:
            print(f"  {s['symbol']} {s['action']} @ {s['entry_min']}-{s['entry_max']}")
        print(f"Rejected: {len(rejected)}")
        for s in rejected:
            print(f"  {s['symbol']}")
        print(f"Skipped:  {len(signals) - len(approved) - len(rejected)}")


asyncio.run(main())
