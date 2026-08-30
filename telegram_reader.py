from db import save_message, get_last_message_id

MAX_MESSAGE_LENGTH = 1000


async def fetch_new_messages(client, conn, channel_ids):
    all_messages = []
    for channel_id in channel_ids:
        last_id = await get_last_message_id(conn, channel_id)
        min_id = last_id if last_id else 0

        async for message in client.iter_messages(channel_id, min_id=min_id, limit=10, reverse=True):
            if not message.text:
                continue
            text = message.text[:MAX_MESSAGE_LENGTH]
            db_id = await save_message(
                conn,
                channel_id=channel_id,
                message_id=message.id,
                text=text,
                timestamp=str(message.date),
            )
            if db_id:
                all_messages.append({
                    "db_id": db_id,
                    "channel_id": channel_id,
                    "message_id": message.id,
                    "text": text,
                    "timestamp": str(message.date),
                })
    return all_messages
