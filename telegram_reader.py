from db import save_message, get_last_message_id

MAX_MESSAGE_LENGTH = 1000


async def fetch_new_messages(client, conn, channel_ids):
    all_messages = []
    for channel_id in channel_ids:
        last_id = await get_last_message_id(conn, channel_id)
        first_run = last_id is None
        if first_run:
            # Record where the chat stands now; anything before this is history.
            iter_args = dict(limit=1)
        else:
            iter_args = dict(min_id=last_id, limit=10, reverse=True)

        async for message in client.iter_messages(channel_id, **iter_args):
            if not message.text and not first_run:
                continue
            text = (message.text or "")[:MAX_MESSAGE_LENGTH]
            db_id = await save_message(
                conn,
                channel_id=channel_id,
                message_id=message.id,
                text=text,
                timestamp=str(message.date),
            )
            if db_id and not first_run:
                all_messages.append({
                    "db_id": db_id,
                    "channel_id": channel_id,
                    "message_id": message.id,
                    "text": text,
                    "timestamp": str(message.date),
                })
    return all_messages
