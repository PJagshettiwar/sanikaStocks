from telethon.sync import TelegramClient
from dotenv import load_dotenv
import os
from datetime import datetime

load_dotenv()

api_id = int(os.environ["TELEGRAM_API_ID"])
api_hash = os.environ["TELEGRAM_API_HASH"]
channels = [int(c.strip()) for c in os.environ["WATCHED_CHANNELS"].split(",")]

output_path = os.path.join(os.path.dirname(__file__), "..", "docs", "sample_messages.md")

with TelegramClient("stock_agent", api_id, api_hash) as client:
    lines = [f"# Sample Messages\n\nFetched at {datetime.now().isoformat()}\n"]

    for channel_id in channels:
        entity = client.get_entity(channel_id)
        name = getattr(entity, "title", str(channel_id))
        lines.append(f"\n---\n\n## {name} (`{channel_id}`)\n")

        count = 0
        for msg in client.iter_messages(channel_id, limit=10):
            if not msg.text:
                continue
            count += 1
            date_str = msg.date.strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"### Message {count} (ID: {msg.id}, {date_str})\n")
            lines.append(f"```\n{msg.text}\n```\n")

        if count == 0:
            lines.append("_No text messages found._\n")

    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    print(f"Saved {output_path}")
