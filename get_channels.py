from telethon.sync import TelegramClient

api_id = 30261858
api_hash = "33b537f62e2dfea822f6f667269d51cd"

with TelegramClient("stock_agent", api_id, api_hash) as client:
    for dialog in client.iter_dialogs():
        if dialog.is_group or dialog.is_channel:
            print(f"{dialog.name}: {dialog.id}")
