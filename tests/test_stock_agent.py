import pytest
import httpx
import json
from unittest.mock import AsyncMock
from stock_agent import detect_signal, extract_trade, analyze_message


def _mock_openrouter_response(content: str):
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
    )


@pytest.mark.asyncio
async def test_detect_signal_identifies_tip():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_openrouter_response('{"is_tip": true, "confidence": 0.92}')

    result = await detect_signal(
        "Buy RELIANCE above 1480, SL 1455, Target 1525",
        api_key="test_key",
        model="nvidia/nemotron-3.5-lightning:free",
        http_client=client,
    )
    assert result["is_tip"] is True
    assert result["confidence"] >= 0.6


@pytest.mark.asyncio
async def test_detect_signal_rejects_chatter():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_openrouter_response('{"is_tip": false, "confidence": 0.15}')

    result = await detect_signal(
        "Market is volatile today",
        api_key="test_key",
        model="nvidia/nemotron-3.5-lightning:free",
        http_client=client,
    )
    assert result["is_tip"] is False


@pytest.mark.asyncio
async def test_extract_trade_returns_structured_signal():
    signal_json = json.dumps({
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "action": "BUY",
        "entry_min": 1482.0,
        "entry_max": 1490.0,
        "stop_loss": 1455.0,
        "targets": [1525.0, 1550.0],
        "allocation_pct": None,
        "confidence": 0.87,
        "reasoning": "Explicit entry with SL and targets",
    })
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_openrouter_response(signal_json)

    result = await extract_trade(
        "Buy RELIANCE above 1480-1490, SL 1455, Target 1525/1550",
        context_messages=["Market looking bullish"],
        api_key="test_key",
        model="nvidia/nemotron-3-super-120b-a12b:free",
        http_client=client,
    )
    assert result["symbol"] == "RELIANCE"
    assert result["entry_min"] == 1482.0
    assert result["stop_loss"] == 1455.0


@pytest.mark.asyncio
async def test_analyze_message_full_pipeline_no_tip():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_openrouter_response('{"is_tip": false, "confidence": 0.1}')

    result = await analyze_message(
        "Good morning everyone",
        context_messages=[],
        api_key="test_key",
        tier1_model="nvidia/nemotron-3.5-lightning:free",
        tier2_model="nvidia/nemotron-3-super-120b-a12b:free",
        http_client=client,
    )
    assert result is None
