import asyncio
import csv
import io
import logging
import time

import httpx
import pyotp

from brokers.base import BrokerInterface, Order, OrderResult, Position, Quote

BASE_URL = "https://api.indstocks.com"
ALGO_ID = "99999"
log = logging.getLogger(__name__)

RETRYABLE_STATUSES = {500, 502, 503, 504}
_MAX_RETRIES = 2
_BASE_DELAY = 1.0


def _retry_after(resp, fallback: float) -> float:
    """RFC 9110 allows Retry-After to be an HTTP date, which float() can't parse."""
    try:
        return float(resp.headers.get("Retry-After", fallback))
    except (TypeError, ValueError):
        return fallback


class RateLimitError(Exception):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Rate limited, retry after {retry_after:.0f}s")


class INDstocksBroker(BrokerInterface):
    AUTH_COOLDOWN = 30.0

    def __init__(self, client_id: str, totp_secret: str, mpin: str,
                 http_client: httpx.AsyncClient | None = None):
        self._client_id = client_id
        self._totp_secret = totp_secret
        self._mpin = mpin
        self._token = ""
        self._client = http_client or httpx.AsyncClient(timeout=30)
        self._headers = {"Authorization": "", "Content-Type": "application/json"}
        self._instrument_cache: dict[str, str] = {}
        self._tick_size_cache: dict[str, float] = {}
        self._instrument_cache_time: float = 0
        self._instrument_cache_ttl: float = 86400
        self._last_quote_time: float = 0
        self._quote_interval: float = 0.5
        self._auth_blocked_until: float = 0

    async def authenticate(self) -> str:
        now = time.monotonic()
        if now < self._auth_blocked_until:
            wait = self._auth_blocked_until - now
            raise RateLimitError(wait)

        totp_code = pyotp.TOTP(self._totp_secret).now()
        resp = await self._client.post(
            f"{BASE_URL}/generate/token",
            headers={"x-api-key": self._client_id, "Content-Type": "application/json"},
            json={"mpin": self._mpin, "totp": totp_code},
        )
        if resp.status_code == 429:
            retry_after = _retry_after(resp, self.AUTH_COOLDOWN)
            self._auth_blocked_until = time.monotonic() + retry_after
            raise RateLimitError(retry_after)
        resp.raise_for_status()
        self._token = resp.json()["token"]
        self._headers["Authorization"] = self._token
        self._auth_blocked_until = 0
        log.info("INDstocks token refreshed")
        return self._token

    async def _ensure_auth(self):
        if not self._token:
            await self.authenticate()

    async def _request(self, method: str, url: str, retry: bool = True, **kwargs):
        await self._ensure_auth()
        reauthed = False

        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await self._client.request(method, url, headers=self._headers, **kwargs)
            except httpx.TransportError as e:
                if not retry or attempt == _MAX_RETRIES:
                    raise
                delay = _BASE_DELAY * (2 ** attempt)
                log.warning("Request to %s failed (attempt %d/%d): %s. Retrying in %.1fs",
                            url, attempt + 1, _MAX_RETRIES + 1, e, delay)
                await asyncio.sleep(delay)
                continue

            if resp.status_code == 403 and not reauthed:
                log.info("Token expired, re-authenticating")
                reauthed = True
                await self.authenticate()
                resp = await self._client.request(method, url, headers=self._headers, **kwargs)

            if resp.status_code == 429:
                raise RateLimitError(_retry_after(resp, self.AUTH_COOLDOWN))

            if resp.status_code in RETRYABLE_STATUSES and retry and attempt < _MAX_RETRIES:
                delay = _BASE_DELAY * (2 ** attempt)
                log.warning("Request to %s returned %d (attempt %d/%d). Retrying in %.1fs",
                            url, resp.status_code, attempt + 1, _MAX_RETRIES + 1, delay)
                await asyncio.sleep(delay)
                continue

            resp.raise_for_status()
            return resp

    async def get_instruments(self) -> dict[str, str]:
        if self._instrument_cache and (time.monotonic() - self._instrument_cache_time) < self._instrument_cache_ttl:
            return self._instrument_cache
        resp = await self._request("GET", f"{BASE_URL}/market/instruments", params={"source": "equity"})
        reader = csv.DictReader(io.StringIO(resp.text))
        new_cache: dict[str, str] = {}
        new_ticks: dict[str, float] = {}
        for row in reader:
            exchange = row.get("EXCH", "").strip()
            if exchange != "NSE":
                continue
            symbol = row.get("TRADING_SYMBOL", "").strip()
            sec_id = row.get("SECURITY_ID", "").strip()
            if symbol and sec_id:
                new_cache[symbol] = sec_id
                raw_tick = row.get("TICK_SIZE", "").strip()
                if raw_tick:
                    new_ticks[symbol] = float(raw_tick) / 100.0
        self._instrument_cache = new_cache
        self._tick_size_cache = new_ticks
        self._instrument_cache_time = time.monotonic()
        return self._instrument_cache

    def get_tick_size(self, symbol: str) -> float:
        return self._tick_size_cache.get(symbol, 0.05)

    async def get_balance(self) -> float:
        resp = await self._request("GET", f"{BASE_URL}/funds")
        data = resp.json()
        avl = data.get("data", {}).get("detailed_avl_balance")
        if not isinstance(avl, dict) or "eq_cnc" not in avl:
            raise ValueError(f"Unexpected /funds payload, cannot read balance: {data}")
        return float(avl["eq_cnc"])

    async def get_quote(self, symbol: str, exchange: str) -> Quote:
        elapsed = time.monotonic() - self._last_quote_time
        if elapsed < self._quote_interval:
            await asyncio.sleep(self._quote_interval - elapsed)
        self._last_quote_time = time.monotonic()
        instruments = await self.get_instruments()
        sec_id = instruments.get(symbol)
        if not sec_id:
            raise ValueError(f"Unknown symbol: {symbol}")
        scrip_code = f"NSE_{sec_id}"
        resp = await self._request("GET", f"{BASE_URL}/market/quotes/full", params={"scrip-codes": scrip_code})
        quote_data = resp.json()["data"][scrip_code]
        return Quote(
            symbol=symbol,
            exchange="NSE",
            price=quote_data["live_price"],
            volume=quote_data.get("volume", 0),
            day_high=quote_data.get("day_high", 0),
            day_low=quote_data.get("day_low", 0),
        )

    async def place_order(self, order: Order) -> OrderResult:
        if order.qty < 1:
            raise ValueError(f"Order quantity must be >= 1, got {order.qty}")
        payload = {
            "txn_type": order.txn_type,
            "exchange": "NSE",
            "segment": "EQUITY",
            "product": order.product,
            "order_type": order.order_type,
            "validity": order.validity,
            "security_id": order.security_id,
            "qty": order.qty,
            "algo_id": ALGO_ID,
        }
        if order.limit_price is not None and order.order_type == "LIMIT":
            payload["limit_price"] = order.limit_price
        resp = await self._request("POST", f"{BASE_URL}/order", retry=False, json=payload)
        data = resp.json()["data"]
        return OrderResult(order_id=data["order_id"], status=data["order_status"])

    async def get_positions(self) -> list[Position]:
        resp = await self._request("GET", f"{BASE_URL}/portfolio/positions", params={"segment": "equity", "product": "cnc"})
        return [
            Position(
                security_id=p["security_id"],
                symbol=p["symbol"],
                exchange=p.get("exchange", "NSE"),
                net_qty=p["net_qty"],
                avg_price=p["avg_price"],
            )
            for p in resp.json().get("data", [])
        ]
