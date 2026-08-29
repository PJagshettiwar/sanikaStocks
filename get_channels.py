from telethon.sync import TelegramClient

api_id = REDACTED
api_hash = "REDACTED"

with TelegramClient("stock_agent", api_id, api_hash) as client:
    for dialog in client.iter_dialogs():
        if dialog.is_group or dialog.is_channel:
            print(f"{dialog.name}: {dialog.id}")
