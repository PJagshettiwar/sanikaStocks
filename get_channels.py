from telethon.sync import TelegramClient
from config import TELEGRAM_API_ID, TELEGRAM_API_HASH

with TelegramClient("stock_agent", TELEGRAM_API_ID, TELEGRAM_API_HASH) as client:
    for dialog in client.iter_dialogs():
        if dialog.is_group or dialog.is_channel:
            print(f"{dialog.name}: {dialog.id}")
