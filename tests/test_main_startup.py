from datetime import datetime, timezone

import pytest
from unittest.mock import AsyncMock, patch

import main


@pytest.mark.asyncio
async def test_start_telegram_client_retries_transient_failure():
    client = AsyncMock()
    client.start = AsyncMock(side_effect=[RuntimeError("Telegram is having internal issues"), None])

    with patch("asyncio.sleep", new=AsyncMock()):
        await main.start_telegram_client(client, bot_token="t")

    assert client.start.await_count == 2


@pytest.mark.asyncio
async def test_start_telegram_client_gives_up_after_last_attempt():
    client = AsyncMock()
    client.start = AsyncMock(side_effect=ValueError("Request was unsuccessful 6 time(s)"))

    with patch("asyncio.sleep", new=AsyncMock()), pytest.raises(ValueError):
        await main.start_telegram_client(client)

    assert client.start.await_count == main.TELEGRAM_START_ATTEMPTS


@pytest.mark.asyncio
async def test_start_telegram_client_does_not_retry_missing_session():
    client = AsyncMock()
    client.start = AsyncMock(side_effect=EOFError())

    with patch("asyncio.sleep", new=AsyncMock()), pytest.raises(EOFError):
        await main.start_telegram_client(client)

    assert client.start.await_count == 1


@pytest.mark.asyncio
async def test_cooldown_absent_does_not_wait(tmp_path):
    sleep = AsyncMock()
    with patch("asyncio.sleep", new=sleep):
        await main.wait_out_auth_cooldown(str(tmp_path / "missing"))

    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_cooldown_waits_out_remaining_time_then_clears(tmp_path):
    marker = tmp_path / ".auth_cooldown"
    written_at = datetime.now(timezone.utc).timestamp() - 10
    marker.write_text(str(written_at))

    sleep = AsyncMock()
    with patch("asyncio.sleep", new=sleep):
        await main.wait_out_auth_cooldown(str(marker))

    waited = sleep.await_args[0][0]
    assert 40 < waited <= main.AUTH_COOLDOWN_SECONDS - 10
    assert not marker.exists()


@pytest.mark.asyncio
async def test_cooldown_already_expired_clears_without_waiting(tmp_path):
    marker = tmp_path / ".auth_cooldown"
    marker.write_text(str(datetime.now(timezone.utc).timestamp() - main.AUTH_COOLDOWN_SECONDS - 10))

    sleep = AsyncMock()
    with patch("asyncio.sleep", new=sleep):
        await main.wait_out_auth_cooldown(str(marker))

    sleep.assert_not_awaited()
    assert not marker.exists()


@pytest.mark.asyncio
async def test_cooldown_unreadable_marker_is_cleared(tmp_path):
    marker = tmp_path / ".auth_cooldown"
    marker.write_text("not a number")

    sleep = AsyncMock()
    with patch("asyncio.sleep", new=sleep):
        await main.wait_out_auth_cooldown(str(marker))

    sleep.assert_not_awaited()
    assert not marker.exists()


@pytest.mark.asyncio
async def test_notify_sends_message():
    client = AsyncMock()
    with patch.object(main, "bot_client", client):
        assert await main.notify("hello") is True

    client.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_notify_swallows_telegram_failure():
    client = AsyncMock()
    client.send_message = AsyncMock(side_effect=ValueError("Request was unsuccessful 6 time(s)"))

    with patch.object(main, "bot_client", client):
        assert await main.notify("hello") is False
