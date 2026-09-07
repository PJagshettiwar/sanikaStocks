import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram_reader import fetch_new_messages


class FakeMessage:
    def __init__(self, id, text, date):
        self.id = id
        self.text = text
        self.date = date


@pytest.mark.asyncio
async def test_fetch_new_messages_saves_and_returns():
    fake_msg = FakeMessage(id=100, text="Buy RELIANCE 1480", date="2026-08-28T10:00:00")
    mock_client = AsyncMock()

    async def fake_iter(*args, **kwargs):
        yield fake_msg

    mock_client.iter_messages = MagicMock(return_value=fake_iter())
    mock_conn = AsyncMock()

    with patch("telegram_reader.get_last_message_id", return_value=99), \
         patch("telegram_reader.save_message", return_value=1):
        messages = await fetch_new_messages(mock_client, mock_conn, [123])

    assert len(messages) == 1
    assert messages[0]["text"] == "Buy RELIANCE 1480"
    assert messages[0]["channel_id"] == 123


@pytest.mark.asyncio
async def test_fetch_skips_non_text_messages():
    fake_msg = FakeMessage(id=101, text=None, date="2026-08-28T10:00:00")
    mock_client = AsyncMock()

    async def fake_iter(*args, **kwargs):
        yield fake_msg

    mock_client.iter_messages = MagicMock(return_value=fake_iter())
    mock_conn = AsyncMock()

    with patch("telegram_reader.get_last_message_id", return_value=100), \
         patch("telegram_reader.save_message", return_value=2):
        messages = await fetch_new_messages(mock_client, mock_conn, [123])

    assert len(messages) == 0


@pytest.mark.asyncio
async def test_first_run_returns_nothing_and_records_watermark():
    newest = FakeMessage(id=500, text="Buy RELIANCE 1480", date="2026-08-28T10:00:00")
    mock_client = AsyncMock()

    async def fake_iter(*args, **kwargs):
        yield newest

    mock_client.iter_messages = MagicMock(return_value=fake_iter())
    mock_conn = AsyncMock()

    with patch("telegram_reader.get_last_message_id", return_value=None), \
         patch("telegram_reader.save_message", return_value=1) as save:
        messages = await fetch_new_messages(mock_client, mock_conn, [123])

    assert messages == []
    save.assert_awaited_once()
    assert save.await_args.kwargs["message_id"] == 500
    assert mock_client.iter_messages.call_args.kwargs["limit"] == 1


@pytest.mark.asyncio
async def test_later_runs_read_only_past_the_watermark():
    mock_client = AsyncMock()

    async def fake_iter(*args, **kwargs):
        yield FakeMessage(id=501, text="Buy INFY 1500", date="2026-08-28T10:01:00")

    mock_client.iter_messages = MagicMock(return_value=fake_iter())
    mock_conn = AsyncMock()

    with patch("telegram_reader.get_last_message_id", return_value=500), \
         patch("telegram_reader.save_message", return_value=2):
        messages = await fetch_new_messages(mock_client, mock_conn, [123])

    assert [m["message_id"] for m in messages] == [501]
    assert mock_client.iter_messages.call_args.kwargs["min_id"] == 500


@pytest.mark.asyncio
async def test_first_run_with_empty_chat_records_nothing():
    mock_client = AsyncMock()

    async def fake_iter(*args, **kwargs):
        return
        yield

    mock_client.iter_messages = MagicMock(return_value=fake_iter())
    mock_conn = AsyncMock()

    with patch("telegram_reader.get_last_message_id", return_value=None), \
         patch("telegram_reader.save_message", return_value=1) as save:
        messages = await fetch_new_messages(mock_client, mock_conn, [123])

    assert messages == []
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_skips_duplicate_messages():
    msg1 = FakeMessage(id=200, text="Buy RELIANCE 1480", date="2026-08-28T10:00:00")
    msg2 = FakeMessage(id=201, text="Buy INFY 1500", date="2026-08-28T10:01:00")
    mock_client = AsyncMock()

    async def fake_iter(*args, **kwargs):
        yield msg1
        yield msg2

    mock_client.iter_messages = MagicMock(return_value=fake_iter())
    mock_conn = AsyncMock()

    with patch("telegram_reader.get_last_message_id", return_value=199), \
         patch("telegram_reader.save_message", side_effect=[1, None]):
        messages = await fetch_new_messages(mock_client, mock_conn, [123])

    assert len(messages) == 1
    assert messages[0]["text"] == "Buy RELIANCE 1480"
