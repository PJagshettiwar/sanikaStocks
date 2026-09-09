import pytest
import httpx
import json
from unittest.mock import AsyncMock
from stock_agent import detect_signal, extract_trade, analyze_message


def _mock_llm_response(content: str):
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=httpx.Request("POST", "https://example.com/chat/completions"),
    )


@pytest.mark.asyncio
async def test_detect_signal_identifies_tip():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_llm_response('{"is_tip": true, "confidence": 0.92}')

    result = await detect_signal(
        "Buy RELIANCE above 1480, SL 1455, Target 1525",
        model="nvidia/nemotron-3.5-lightning:free",
        http_client=client,
    )
    assert result["is_tip"] is True
    assert result["confidence"] >= 0.6


@pytest.mark.asyncio
async def test_detect_signal_rejects_chatter():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_llm_response('{"is_tip": false, "confidence": 0.15}')

    result = await detect_signal(
        "Market is volatile today",
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
    client.post.return_value = _mock_llm_response(signal_json)

    result = await extract_trade(
        "Buy RELIANCE above 1480-1490, SL 1455, Target 1525/1550",
        context_messages=["Market looking bullish"],
        model="nvidia/nemotron-3-super-120b-a12b:free",
        http_client=client,
    )
    assert result["symbol"] == "RELIANCE"
    assert result["entry_min"] == 1482.0
    assert result["stop_loss"] == 1455.0


@pytest.mark.asyncio
async def test_analyze_message_full_pipeline_no_tip():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_llm_response('{"is_tip": false, "confidence": 0.1}')

    result = await analyze_message(
        "Good morning everyone",
        context_messages=[],
        tier1_model="nvidia/nemotron-3.5-lightning:free",
        tier2_model="nvidia/nemotron-3-super-120b-a12b:free",
        http_client=client,
    )
    assert result is None


@pytest.mark.asyncio
async def test_detect_signal_returns_none_on_non_json():
    """H7: Free-tier models sometimes return plain text instead of JSON."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_llm_response(
        "I cannot process stock tips. Please consult a financial advisor."
    )
    result = await detect_signal(
        "Buy RELIANCE above 1480",
        model="nvidia/nemotron-3.5-lightning:free",
        http_client=client,
    )
    assert result is None


@pytest.mark.asyncio
async def test_detect_signal_returns_none_on_partial_json():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_llm_response(
        '{"is_tip": true, "confidence":'
    )
    result = await detect_signal(
        "Buy RELIANCE above 1480",
        model="nvidia/nemotron-3.5-lightning:free",
        http_client=client,
    )
    assert result is None


@pytest.mark.asyncio
async def test_extract_trade_returns_none_on_non_json():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_llm_response(
        "Here is my analysis of the stock tip:\nRELIANCE looks bullish"
    )
    result = await extract_trade(
        "Buy RELIANCE above 1480",
        context_messages=[],
        model="nvidia/nemotron-3-super-120b-a12b:free",
        http_client=client,
    )
    assert result is None


@pytest.mark.asyncio
async def test_analyze_message_handles_json_failure_in_tier1():
    """Full pipeline: tier1 returns garbage, pipeline returns None without calling tier2."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_llm_response("Not valid JSON at all")

    result = await analyze_message(
        "Buy RELIANCE above 1480",
        context_messages=[],
        tier1_model="nvidia/nemotron-3.5-lightning:free",
        tier2_model="nvidia/nemotron-3-super-120b-a12b:free",
        http_client=client,
    )
    assert result is None
    assert client.post.call_count == 1


@pytest.mark.asyncio
async def test_analyze_message_full_pipeline_tip_detected_and_extracted():
    """H11: Full two-tier pipeline happy path."""
    tier1_response = _mock_llm_response('{"is_tip": true, "confidence": 0.92}')
    signal_json = json.dumps({
        "symbol": "RELIANCE", "exchange": "NSE", "action": "BUY",
        "entry_min": 1482.0, "entry_max": 1490.0, "stop_loss": 1455.0,
        "targets": [1525.0, 1550.0], "allocation_pct": None,
        "confidence": 0.87, "reasoning": "Strong setup",
    })
    tier2_response = _mock_llm_response(signal_json)

    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.side_effect = [tier1_response, tier2_response]

    result = await analyze_message(
        "Buy RELIANCE above 1480-1490, SL 1455, Target 1525/1550",
        context_messages=["Market looking bullish"],
        tier1_model="nvidia/nemotron-3.5-lightning:free",
        tier2_model="nvidia/nemotron-3-super-120b-a12b:free",
        http_client=client,
    )
    assert result is not None
    assert result["symbol"] == "RELIANCE"
    assert result["action"] == "BUY"
    assert result["stop_loss"] == 1455.0
    assert client.post.call_count == 2


@pytest.mark.asyncio
async def test_analyze_message_low_confidence_skips_tier2():
    """Tip detected but below confidence threshold should skip tier2."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_llm_response('{"is_tip": true, "confidence": 0.3}')

    result = await analyze_message(
        "Maybe buy RELIANCE?",
        context_messages=[],
        tier1_model="nvidia/nemotron-3.5-lightning:free",
        tier2_model="nvidia/nemotron-3-super-120b-a12b:free",
        http_client=client,
    )
    assert result is None
    assert client.post.call_count == 1


@pytest.mark.asyncio
async def test_analyze_message_tier2_fails_returns_none():
    """Tier1 succeeds but tier2 returns non-JSON."""
    tier1_response = _mock_llm_response('{"is_tip": true, "confidence": 0.92}')
    tier2_response = _mock_llm_response("I cannot extract a signal from this")

    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.side_effect = [tier1_response, tier2_response]

    result = await analyze_message(
        "Buy RELIANCE above 1480",
        context_messages=[],
        tier1_model="nvidia/nemotron-3.5-lightning:free",
        tier2_model="nvidia/nemotron-3-super-120b-a12b:free",
        http_client=client,
    )
    assert result is None
    assert client.post.call_count == 2


@pytest.mark.asyncio
async def test_extract_trade_overrides_non_nse_exchange():
    signal_json = json.dumps({
        "symbol": "GOLDPETAL",
        "exchange": "MCX",
        "action": "BUY",
        "entry_min": 5000.0,
        "entry_max": 5100.0,
        "stop_loss": 4900.0,
        "targets": [5200.0],
        "allocation_pct": None,
        "confidence": 0.80,
        "reasoning": "Commodity play",
    })
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_llm_response(signal_json)

    result = await extract_trade(
        "Buy GOLDPETAL above 5000",
        context_messages=[],
        model="nvidia/nemotron-3-super-120b-a12b:free",
        http_client=client,
    )
    assert result is not None
    assert result["exchange"] == "NSE"


@pytest.mark.asyncio
async def test_extract_trade_normalizes_null_targets():
    signal_json = json.dumps({
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "action": "BUY",
        "entry_min": 1482.0,
        "entry_max": 1490.0,
        "stop_loss": 1455.0,
        "targets": None,
        "allocation_pct": None,
        "confidence": 0.87,
        "reasoning": "Strong setup",
    })
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_llm_response(signal_json)

    result = await extract_trade(
        "Buy RELIANCE above 1480",
        context_messages=[],
        model="nvidia/nemotron-3-super-120b-a12b:free",
        http_client=client,
    )
    assert result is not None
    assert result["targets"] == []


@pytest.mark.asyncio
async def test_extract_trade_filters_invalid_targets():
    signal_json = json.dumps({
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "action": "BUY",
        "entry_min": 1482.0,
        "entry_max": 1490.0,
        "stop_loss": 1455.0,
        "targets": [-1, 1500, 99999999],
        "allocation_pct": None,
        "confidence": 0.87,
        "reasoning": "Strong setup",
    })
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_llm_response(signal_json)

    result = await extract_trade(
        "Buy RELIANCE above 1480",
        context_messages=[],
        model="nvidia/nemotron-3-super-120b-a12b:free",
        http_client=client,
    )
    assert result is not None
    assert result["targets"] == [1500]


@pytest.mark.asyncio
async def test_detect_signal_retries_on_500(monkeypatch):
    monkeypatch.setattr("stock_agent._BASE_DELAY", 0)
    error_resp = httpx.Response(
        500, text="Internal Server Error",
        request=httpx.Request("POST", "https://example.com/chat/completions"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(side_effect=[
        error_resp,
        _mock_llm_response('{"is_tip": true, "confidence": 0.92}'),
    ])

    result = await detect_signal("Buy RELIANCE above 1480", model="test-model", http_client=client)
    assert result["is_tip"] is True
    assert client.post.call_count == 2


@pytest.mark.asyncio
async def test_detect_signal_raises_after_max_retries(monkeypatch):
    monkeypatch.setattr("stock_agent._BASE_DELAY", 0)
    error_resp = httpx.Response(
        500, text="Internal Server Error",
        request=httpx.Request("POST", "https://example.com/chat/completions"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=error_resp)

    with pytest.raises(httpx.HTTPStatusError):
        await detect_signal("Buy RELIANCE above 1480", model="test-model", http_client=client)
    assert client.post.call_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("header,expected_sleep", [
    ("2", 2.0),
    ("600", 10.0),                              # capped at _MAX_DELAY
    ("Wed, 21 Oct 2015 07:28:00 GMT", 1.0),     # HTTP-date form falls back to backoff
])
async def test_llm_429_retry_after_handling(monkeypatch, header, expected_sleep):
    slept = []

    async def _fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("stock_agent.asyncio.sleep", _fake_sleep)
    rate_limited = httpx.Response(
        429, text="rate limited", headers={"Retry-After": header},
        request=httpx.Request("POST", "https://example.com/chat/completions"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(side_effect=[
        rate_limited,
        _mock_llm_response('{"is_tip": true, "confidence": 0.9}'),
    ])

    result = await detect_signal("Buy RELIANCE above 1480", model="test-model", http_client=client)
    assert result["is_tip"] is True
    assert slept == [expected_sleep]


@pytest.mark.asyncio
async def test_detect_signal_no_retry_on_400(monkeypatch):
    monkeypatch.setattr("stock_agent._BASE_DELAY", 0)
    error_resp = httpx.Response(
        400, text="Bad Request",
        request=httpx.Request("POST", "https://example.com/chat/completions"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=error_resp)

    with pytest.raises(httpx.HTTPStatusError):
        await detect_signal("Buy RELIANCE above 1480", model="test-model", http_client=client)
    assert client.post.call_count == 1


# --- Sell/exit signal tests ---


@pytest.mark.asyncio
async def test_extract_trade_sell_zeroes_entry_and_sets_sell_pct():
    signal_json = json.dumps({
        "symbol": "CAPLINPOINT", "exchange": "NSE", "action": "SELL",
        "sell_pct": 100, "entry_min": 0, "entry_max": 0, "stop_loss": None,
        "targets": [], "allocation_pct": None, "confidence": 0.85,
        "reasoning": "Exit call",
    })
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_llm_response(signal_json)

    result = await extract_trade("Exit Caplin Point", [], model="test", http_client=client)
    assert result["action"] == "SELL"
    assert result["entry_min"] == 0
    assert result["entry_max"] == 0
    assert result["stop_loss"] is None
    assert result["targets"] == []
    assert result["sell_pct"] == 100


@pytest.mark.asyncio
async def test_extract_trade_sell_partial_pct():
    signal_json = json.dumps({
        "symbol": "CAPLINPOINT", "exchange": "NSE", "action": "SELL",
        "sell_pct": 50, "entry_min": 0, "entry_max": 0, "stop_loss": None,
        "targets": [], "allocation_pct": None, "confidence": 0.8,
        "reasoning": "Partial exit",
    })
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_llm_response(signal_json)

    result = await extract_trade("Exit 50% Caplin Point", [], model="test", http_client=client)
    assert result["sell_pct"] == 50


@pytest.mark.asyncio
async def test_extract_trade_sell_invalid_pct_defaults_to_100():
    signal_json = json.dumps({
        "symbol": "RELIANCE", "exchange": "NSE", "action": "SELL",
        "sell_pct": 150, "entry_min": 0, "entry_max": 0, "stop_loss": None,
        "targets": [], "allocation_pct": None, "confidence": 0.8,
        "reasoning": "Exit",
    })
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_llm_response(signal_json)

    result = await extract_trade("Exit Reliance", [], model="test", http_client=client)
    assert result["sell_pct"] == 100


@pytest.mark.asyncio
async def test_extract_trade_sell_forces_zero_entry():
    """Even if LLM returns nonzero entry for SELL, extract_trade zeroes them."""
    signal_json = json.dumps({
        "symbol": "RELIANCE", "exchange": "NSE", "action": "SELL",
        "sell_pct": 100, "entry_min": 1500.0, "entry_max": 1520.0,
        "stop_loss": 1400.0, "targets": [1480.0], "allocation_pct": None,
        "confidence": 0.8, "reasoning": "Exit",
    })
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_llm_response(signal_json)

    result = await extract_trade("Exit Reliance", [], model="test", http_client=client)
    assert result["entry_min"] == 0
    assert result["entry_max"] == 0
    assert result["stop_loss"] is None
    assert result["targets"] == []


@pytest.mark.asyncio
async def test_extract_trade_buy_gets_sell_pct_zero():
    signal_json = json.dumps({
        "symbol": "RELIANCE", "exchange": "NSE", "action": "BUY",
        "entry_min": 1482.0, "entry_max": 1490.0, "stop_loss": 1455.0,
        "targets": [1525.0], "allocation_pct": None, "confidence": 0.87,
        "reasoning": "Strong setup",
    })
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _mock_llm_response(signal_json)

    result = await extract_trade("Buy RELIANCE above 1480", [], model="test", http_client=client)
    assert result["sell_pct"] == 0
