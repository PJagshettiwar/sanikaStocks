import pytest
import httpx
from unittest.mock import AsyncMock, patch
from brokers.base import BrokerInterface, Order, Quote, OrderResult
from brokers.indstocks import INDstocksBroker


def test_indstocks_implements_interface():
    assert issubclass(INDstocksBroker, BrokerInterface)


@pytest.mark.asyncio
async def test_place_order_sends_correct_payload():
    mock_response = httpx.Response(
        200,
        json={"status": "success", "data": {"order_id": "ORD123", "order_status": "placed"}},
        request=httpx.Request("POST", "https://api.indstocks.com/order"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=mock_response)

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", token="test_token", http_client=client)
    order = Order(
        symbol="RELIANCE",
        exchange="NSE",
        security_id="2885",
        txn_type="BUY",
        qty=10,
        order_type="LIMIT",
        limit_price=1490.0,
        product="CNC",
        validity="DAY",
    )
    result = await broker.place_order(order)

    assert result.order_id == "ORD123"
    client.request.assert_called_once()
    call_kwargs = client.request.call_args
    body = call_kwargs.kwargs.get("json")
    assert body["txn_type"] == "BUY"
    assert body["security_id"] == "2885"
    assert body["qty"] == 10


@pytest.mark.asyncio
async def test_get_quote_returns_quote():
    mock_response = httpx.Response(
        200,
        json={"status": "success", "data": {"NSE_2885": {"live_price": 1486.0, "volume": 3546732, "day_high": 1495.0, "day_low": 1480.0}}},
        request=httpx.Request("GET", "https://api.indstocks.com/market/quotes/full"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=mock_response)

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", token="test_token", http_client=client)
    broker._instrument_cache = {"RELIANCE": "2885"}
    quote = await broker.get_quote("RELIANCE", "NSE")

    assert quote.price == 1486.0
    assert quote.volume == 3546732


@pytest.mark.asyncio
async def test_403_triggers_reauth_and_retry():
    """H13: On 403, broker re-authenticates and retries the request."""
    forbidden = httpx.Response(
        403, json={"message": "Token expired"},
        request=httpx.Request("GET", "https://api.indstocks.com/funds"),
    )
    success = httpx.Response(
        200, json={"status": "success", "data": {"available_balance": 50000}},
        request=httpx.Request("GET", "https://api.indstocks.com/funds"),
    )
    auth_response = httpx.Response(
        200, json={"token": "new_token"},
        request=httpx.Request("POST", "https://api.indstocks.com/generate/token"),
    )

    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(side_effect=[forbidden, success])
    client.post = AsyncMock(return_value=auth_response)

    broker = INDstocksBroker(
        client_id="test", totp_secret="JBSWY3DPEHPK3PXP",
        mpin="1234", token="expired_token", http_client=client,
    )
    balance = await broker.get_balance()

    assert balance == 50000
    assert client.request.call_count == 2
    client.post.assert_called_once()


@pytest.mark.asyncio
async def test_get_instruments_parses_csv():
    csv_content = "TRADING_SYMBOL,SECURITY_ID,OTHER\nRELIANCE,2885,x\nINFY,5678,y\n"
    mock_response = httpx.Response(
        200, text=csv_content,
        request=httpx.Request("GET", "https://api.indstocks.com/market/instruments"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=mock_response)

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", token="test_token", http_client=client)
    instruments = await broker.get_instruments()

    assert instruments["RELIANCE"] == "2885"
    assert instruments["INFY"] == "5678"


@pytest.mark.asyncio
async def test_get_instruments_caches_result():
    csv_content = "TRADING_SYMBOL,SECURITY_ID,OTHER\nRELIANCE,2885,x\n"
    mock_response = httpx.Response(
        200, text=csv_content,
        request=httpx.Request("GET", "https://api.indstocks.com/market/instruments"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=mock_response)

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", token="test_token", http_client=client)
    await broker.get_instruments()
    await broker.get_instruments()

    assert client.request.call_count == 1
