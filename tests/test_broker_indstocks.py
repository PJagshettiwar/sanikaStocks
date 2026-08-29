import pytest
import httpx
from unittest.mock import AsyncMock
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
