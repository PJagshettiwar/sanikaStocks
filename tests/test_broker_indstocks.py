import time

import pytest
import httpx
from unittest.mock import AsyncMock, patch
from brokers.base import BrokerInterface, Order, Position, Quote, OrderResult, OrderStatus
from brokers.indstocks import INDstocksBroker, RateLimitError


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

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", http_client=client)
    broker._token = "test_token"
    broker._headers["Authorization"] = "test_token"
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

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", http_client=client)
    broker._token = "test_token"
    broker._headers["Authorization"] = "test_token"
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
        200, json={"status": "success", "data": {"detailed_avl_balance": {"eq_cnc": 50000}}},
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
        mpin="1234", http_client=client,
    )
    broker._token = "expired_token"
    broker._headers["Authorization"] = "expired_token"
    balance = await broker.get_balance()

    assert balance == 50000
    assert client.request.call_count == 2
    client.post.assert_called_once()


@pytest.mark.asyncio
async def test_get_instruments_parses_csv():
    csv_content = "EXCH,TRADING_SYMBOL,SECURITY_ID,OTHER\nNSE,RELIANCE,2885,x\nNSE,INFY,5678,y\nBSE,RELIANCE,500325,z\n"
    mock_response = httpx.Response(
        200, text=csv_content,
        request=httpx.Request("GET", "https://api.indstocks.com/market/instruments"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=mock_response)

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", http_client=client)
    broker._token = "test_token"
    broker._headers["Authorization"] = "test_token"
    instruments = await broker.get_instruments()

    assert instruments["RELIANCE"] == "2885"
    assert instruments["INFY"] == "5678"
    assert len(instruments) == 2


@pytest.mark.asyncio
async def test_get_instruments_clears_stale_entries():
    csv_v1 = "EXCH,TRADING_SYMBOL,SECURITY_ID,OTHER\nNSE,RELIANCE,2885,x\nNSE,DELISTED,9999,y\n"
    csv_v2 = "EXCH,TRADING_SYMBOL,SECURITY_ID,OTHER\nNSE,RELIANCE,2885,x\n"
    resp_v1 = httpx.Response(200, text=csv_v1, request=httpx.Request("GET", "https://api.indstocks.com/market/instruments"))
    resp_v2 = httpx.Response(200, text=csv_v2, request=httpx.Request("GET", "https://api.indstocks.com/market/instruments"))
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(side_effect=[resp_v1, resp_v2])

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", http_client=client)
    broker._token = "test_token"
    broker._headers["Authorization"] = "test_token"
    instruments = await broker.get_instruments()
    assert "DELISTED" in instruments

    broker._instrument_cache_time = time.monotonic() - 86401
    instruments = await broker.get_instruments()
    assert "DELISTED" not in instruments
    assert instruments["RELIANCE"] == "2885"


@pytest.mark.asyncio
async def test_get_instruments_caches_result():
    csv_content = "EXCH,TRADING_SYMBOL,SECURITY_ID,OTHER\nNSE,RELIANCE,2885,x\n"
    mock_response = httpx.Response(
        200, text=csv_content,
        request=httpx.Request("GET", "https://api.indstocks.com/market/instruments"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=mock_response)

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", http_client=client)
    broker._token = "test_token"
    broker._headers["Authorization"] = "test_token"
    await broker.get_instruments()
    await broker.get_instruments()

    assert client.request.call_count == 1


@pytest.mark.asyncio
async def test_authenticate_429_raises_rate_limit_error():
    rate_limited = httpx.Response(
        429, json={"message": "Too Many Requests"},
        headers={"Retry-After": "60"},
        request=httpx.Request("POST", "https://api.indstocks.com/generate/token"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=rate_limited)

    broker = INDstocksBroker(
        client_id="test", totp_secret="JBSWY3DPEHPK3PXP",
        mpin="1234", http_client=client,
    )
    with pytest.raises(RateLimitError) as exc_info:
        await broker.authenticate()
    assert exc_info.value.retry_after == 60.0


@pytest.mark.asyncio
async def test_authenticate_429_blocks_subsequent_calls():
    rate_limited = httpx.Response(
        429, json={"message": "Too Many Requests"},
        request=httpx.Request("POST", "https://api.indstocks.com/generate/token"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=rate_limited)

    broker = INDstocksBroker(
        client_id="test", totp_secret="JBSWY3DPEHPK3PXP",
        mpin="1234", http_client=client,
    )
    with pytest.raises(RateLimitError):
        await broker.authenticate()

    # Second call should be blocked without hitting the API
    with pytest.raises(RateLimitError):
        await broker.authenticate()
    assert client.post.call_count == 1


@pytest.mark.asyncio
async def test_request_429_raises_rate_limit_error():
    rate_limited = httpx.Response(
        429, json={"message": "Too Many Requests"},
        headers={"Retry-After": "30"},
        request=httpx.Request("GET", "https://api.indstocks.com/funds"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=rate_limited)

    broker = INDstocksBroker(
        client_id="test", totp_secret="test",
        mpin="test", http_client=client,
    )
    broker._token = "valid_token"
    broker._headers["Authorization"] = "valid_token"
    with pytest.raises(RateLimitError) as exc_info:
        await broker.get_balance()
    assert exc_info.value.retry_after == 30.0


@pytest.mark.asyncio
async def test_request_retries_on_500(monkeypatch):
    monkeypatch.setattr("brokers.indstocks._BASE_DELAY", 0)
    error_resp = httpx.Response(
        500, json={"message": "Internal Server Error"},
        request=httpx.Request("GET", "https://api.indstocks.com/funds"),
    )
    success_resp = httpx.Response(
        200, json={"status": "success", "data": {"detailed_avl_balance": {"eq_cnc": 25000}}},
        request=httpx.Request("GET", "https://api.indstocks.com/funds"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(side_effect=[error_resp, success_resp])

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", http_client=client)
    broker._token = "valid_token"
    broker._headers["Authorization"] = "valid_token"

    assert await broker.get_balance() == 25000
    assert client.request.call_count == 2


@pytest.mark.asyncio
async def test_request_retries_on_network_error(monkeypatch):
    monkeypatch.setattr("brokers.indstocks._BASE_DELAY", 0)
    success_resp = httpx.Response(
        200, json={"status": "success", "data": {"detailed_avl_balance": {"eq_cnc": 10000}}},
        request=httpx.Request("GET", "https://api.indstocks.com/funds"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(side_effect=[httpx.ConnectError("connection refused"), success_resp])

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", http_client=client)
    broker._token = "valid_token"
    broker._headers["Authorization"] = "valid_token"

    assert await broker.get_balance() == 10000
    assert client.request.call_count == 2


def _resp(status, url="https://api.indstocks.com/funds", **kw):
    return httpx.Response(status, request=httpx.Request("GET", url), **kw)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"status": "success", "data": {}},
    {"status": "success", "data": {"detailed_avl_balance": {}}},
    {"status": "error", "message": "something broke"},
])
async def test_get_balance_raises_on_unexpected_payload(payload):
    """Balance gates order placement, so a bad payload must not read as zero funds."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=_resp(200, json=payload))

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", http_client=client)
    broker._token = "valid_token"
    broker._headers["Authorization"] = "valid_token"

    with pytest.raises(ValueError, match="funds"):
        await broker.get_balance()


@pytest.mark.asyncio
async def test_get_balance_accepts_zero_balance():
    """A genuine zero must still come through as zero, not an error."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=_resp(
        200, json={"status": "success", "data": {"detailed_avl_balance": {"eq_cnc": 0}}},
    ))

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", http_client=client)
    broker._token = "valid_token"
    broker._headers["Authorization"] = "valid_token"

    assert await broker.get_balance() == 0.0


@pytest.mark.asyncio
async def test_403_reauth_happens_once_per_request(monkeypatch):
    """A 403 on a later retry attempt must not trigger a second authenticate()."""
    monkeypatch.setattr("brokers.indstocks._BASE_DELAY", 0)
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(side_effect=[
        _resp(403, json={"message": "Token expired"}),
        _resp(500, json={"message": "boom"}),
        _resp(403, json={"message": "Token expired"}),
        _resp(500, json={"message": "boom"}),
        _resp(500, json={"message": "boom"}),
    ])
    client.post = AsyncMock(return_value=httpx.Response(
        200, json={"token": "new_token"},
        request=httpx.Request("POST", "https://api.indstocks.com/generate/token"),
    ))

    broker = INDstocksBroker(
        client_id="test", totp_secret="JBSWY3DPEHPK3PXP",
        mpin="1234", http_client=client,
    )
    broker._token = "expired_token"
    broker._headers["Authorization"] = "expired_token"

    with pytest.raises(httpx.HTTPStatusError):
        await broker._request("GET", "https://api.indstocks.com/funds")
    assert client.post.call_count == 1


def _order():
    return Order(
        symbol="RELIANCE", exchange="NSE", security_id="2885", txn_type="BUY",
        qty=10, order_type="LIMIT", limit_price=1490.0, product="CNC", validity="DAY",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    httpx.Response(500, json={"message": "Internal Server Error"},
                   request=httpx.Request("POST", "https://api.indstocks.com/order")),
    httpx.ConnectError("connection reset"),
])
async def test_place_order_never_retries(monkeypatch, failure):
    """A retried order can duplicate a fill the broker already accepted."""
    monkeypatch.setattr("brokers.indstocks._BASE_DELAY", 0)
    client = AsyncMock(spec=httpx.AsyncClient)
    if isinstance(failure, Exception):
        client.request = AsyncMock(side_effect=failure)
        expected = httpx.ConnectError
    else:
        client.request = AsyncMock(return_value=failure)
        expected = httpx.HTTPStatusError

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", http_client=client)
    broker._token = "valid_token"
    broker._headers["Authorization"] = "valid_token"

    with pytest.raises(expected):
        await broker.place_order(_order())
    assert client.request.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_qty", [0, -1])
async def test_place_order_rejects_invalid_qty(bad_qty):
    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test")
    broker._token = "valid_token"
    broker._headers["Authorization"] = "valid_token"
    order = Order(
        symbol="RELIANCE", exchange="NSE", security_id="2885", txn_type="BUY",
        qty=bad_qty, order_type="LIMIT", limit_price=1490.0, product="CNC", validity="DAY",
    )
    with pytest.raises(ValueError, match="must be >= 1"):
        await broker.place_order(order)


@pytest.mark.asyncio
async def test_get_order_status_returns_order_status():
    mock_response = httpx.Response(
        200,
        json={"status": "success", "data": {
            "id": "EQ-99536862",
            "status": "SUCCESS",
            "traded_qty": 12,
            "traded_price": 411.40,
            "requested_qty": 12,
            "requested_price": 412.22,
            "extra_info": "",
        }},
        request=httpx.Request("GET", "https://api.indstocks.com/order"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=mock_response)

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", http_client=client)
    broker._token = "test_token"
    broker._headers["Authorization"] = "test_token"
    result = await broker.get_order_status("EQ-99536862")

    assert isinstance(result, OrderStatus)
    assert result.order_id == "EQ-99536862"
    assert result.status == "SUCCESS"
    assert result.traded_qty == 12
    assert result.traded_price == 411.40
    assert result.requested_qty == 12
    assert result.requested_price == 412.22
    assert result.extra_info == ""

    call_kwargs = client.request.call_args
    assert call_kwargs.args[0] == "GET"
    body = call_kwargs.kwargs.get("json")
    assert body == {"order_id": "EQ-99536862", "segment": "EQUITY"}


@pytest.mark.asyncio
async def test_get_order_status_failed_order_has_extra_info():
    mock_response = httpx.Response(
        200,
        json={"status": "success", "data": {
            "id": "EQ-99536862",
            "status": "FAILED",
            "traded_qty": 0,
            "traded_price": 0,
            "requested_qty": 12,
            "requested_price": 412.22,
            "extra_info": "RMS:Blocked for nse_cm ACMESOLAR-EQ Insufficient Margin",
        }},
        request=httpx.Request("GET", "https://api.indstocks.com/order"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=mock_response)

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", http_client=client)
    broker._token = "test_token"
    broker._headers["Authorization"] = "test_token"
    result = await broker.get_order_status("EQ-99536862")

    assert result.status == "FAILED"
    assert result.traded_qty == 0
    assert "Insufficient Margin" in result.extra_info


@pytest.mark.asyncio
async def test_get_holdings_uses_total_qty_field():
    mock_response = httpx.Response(
        200,
        json={"status": "success", "data": [
            {"security_id": "2885", "symbol": "RELIANCE", "exchange": "NSE",
             "total_qty": 15, "avg_price": 1490.0},
            {"security_id": "5678", "symbol": "INFY",
             "total_qty": 30, "avg_price": 1200.0},
        ]},
        request=httpx.Request("GET", "https://api.indstocks.com/portfolio/holdings"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=mock_response)

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", http_client=client)
    broker._token = "test_token"
    broker._headers["Authorization"] = "test_token"
    holdings = await broker.get_holdings()

    assert len(holdings) == 2
    assert all(isinstance(h, Position) for h in holdings)
    assert holdings[0].security_id == "2885"
    assert holdings[0].net_qty == 15
    assert holdings[0].avg_price == 1490.0
    assert holdings[0].exchange == "NSE"
    assert holdings[1].symbol == "INFY"
    assert holdings[1].net_qty == 30
    assert holdings[1].exchange == "NSE"


@pytest.mark.asyncio
async def test_get_holdings_falls_back_to_net_qty():
    mock_response = httpx.Response(
        200,
        json={"status": "success", "data": [
            {"security_id": "2885", "symbol": "RELIANCE", "exchange": "NSE",
             "net_qty": 10, "avg_price": 1490.0},
        ]},
        request=httpx.Request("GET", "https://api.indstocks.com/portfolio/holdings"),
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=mock_response)

    broker = INDstocksBroker(client_id="test", totp_secret="test", mpin="test", http_client=client)
    broker._token = "test_token"
    broker._headers["Authorization"] = "test_token"
    holdings = await broker.get_holdings()

    assert holdings[0].net_qty == 10
