import asyncio
import aiosqlite
import httpx
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
from stock_agent import analyze_message
from db import init_db, save_signal, get_recent_messages

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

db_path = os.path.join(os.path.dirname(__file__), "..", "agent.db")
api_key = os.environ["OPENROUTER_API_KEY"]
tier1_model = os.getenv("TIER1_MODEL", "nvidia/nemotron-3.5-lightning:free")
tier2_model = os.getenv("TIER2_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")


async def main():
    async with aiosqlite.connect(db_path) as conn:
        await init_db(conn)

        cursor = await conn.execute(
            "SELECT id, channel_id, message_id, text, timestamp FROM messages WHERE processed = 0 ORDER BY id"
        )
        rows = await cursor.fetchall()
        print(f"Found {len(rows)} unprocessed messages\n")

        tips_found = []

        async with httpx.AsyncClient() as http:
            for row in rows:
                db_id, channel_id, message_id, text, timestamp = row
                short_text = text[:60].replace("\n", " ")
                print(f"[{db_id}] Analyzing: {short_text}...")

                context = await get_recent_messages(conn, channel_id, limit=5)
                context_texts = [m["text"] for m in context if m["message_id"] != message_id]

                try:
                    result = await analyze_message(text, context_texts, api_key, tier1_model, tier2_model, http)
                except Exception as e:
                    print(f"  ERROR: {e}")
                    result = None

                if result:
                    signal_id = await save_signal(conn, db_id, result)
                    print(f"  TIP FOUND -> signal_id={signal_id}: {result['symbol']} {result['action']} @ {result['entry_min']}-{result['entry_max']}")
                    tips_found.append(result)
                else:
                    print(f"  Not a tip")

                await conn.execute("UPDATE messages SET processed = 1 WHERE id = ?", (db_id,))
                await conn.commit()

        print(f"\n{'='*60}")
        print(f"Summary: {len(tips_found)} tips found out of {len(rows)} messages")
        if tips_found:
            print(f"\nExtracted tips:")
            for t in tips_found:
                print(f"  {t['symbol']} ({t['exchange']}) - {t['action']} @ {t['entry_min']}-{t['entry_max']}, SL: {t.get('stop_loss')}, Targets: {t.get('targets')}")


asyncio.run(main())
