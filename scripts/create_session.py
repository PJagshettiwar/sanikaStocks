"""Interactive Telegram session generator. Run with a TTY attached."""
import asyncio
import os
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

async def main():
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    session_name = os.getenv("TELEGRAM_SESSION_NAME", "data/stock_agent")

    print(f"Creating session: {session_name}.session")
    print("You will be prompted for your phone number and a verification code.\n")

    client = TelegramClient(session_name, api_id, api_hash)
    await client.start()

    me = await client.get_me()
    print(f"\nAuthenticated as: {me.first_name} (id={me.id})")
    print(f"Session saved to: {session_name}.session")
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
