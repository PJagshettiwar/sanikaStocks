import asyncio
import aiosqlite
from telethon import TelegramClient
from dotenv import load_dotenv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from db import init_db, save_message

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

api_id = int(os.environ["TELEGRAM_API_ID"])
api_hash = os.environ["TELEGRAM_API_HASH"]
channels = [int(c.strip()) for c in os.environ["WATCHED_CHANNELS"].split(",")]
db_path = os.path.join(os.path.dirname(__file__), "..", "agent.db")
session_path = os.path.join(os.path.dirname(__file__), "..", "stock_agent")


async def main():
    async with aiosqlite.connect(db_path) as conn:
        await init_db(conn)

        client = TelegramClient(session_path, api_id, api_hash)
        await client.start()

        total = 0
        for channel_id in channels:
            entity = await client.get_entity(channel_id)
            name = getattr(entity, "title", str(channel_id))

            count = 0
            async for msg in client.iter_messages(channel_id, limit=10):
                if not msg.text:
                    continue
                db_id = await save_message(conn, channel_id, msg.id, msg.text, str(msg.date))
                if db_id:
                    count += 1

            print(f"{name}: saved {count} messages")
            total += count

        await client.disconnect()
        print(f"\nTotal: {total} messages saved to {db_path}")


asyncio.run(main())
