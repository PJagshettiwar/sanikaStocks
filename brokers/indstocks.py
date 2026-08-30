import asyncio
import csv
import io
import logging
import time

import httpx
import pyotp

from brokers.base import BrokerInterface, Order, OrderResult, Position, Quote

BASE_URL = "https://api.indstocks.com"
ALGO_ID_NSE = "99999"
ALGO_ID_BSE = "9999999999999999"
log = logging.getLogger(__name__)


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
            retry_after = float(resp.headers.get("Retry-After", self.AUTH_COOLDOWN))
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

    async def _request(self, method: str, url: str, **kwargs):
        await self._ensure_auth()
        resp = await self._client.request(method, url, headers=self._headers, **kwargs)
        if resp.status_code == 403:
            log.info("Token expired, re-authenticating")
            await self.authenticate()
            resp = await self._client.request(method, url, headers=self._headers, **kwargs)
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", self.AUTH_COOLDOWN))
            raise RateLimitError(retry_after)
        resp.raise_for_status()
        return resp

    async def get_instruments(self) -> dict[str, str]:
        if self._instrument_cache and (time.monotonic() - self._instrument_cache_time) < self._instrument_cache_ttl:
            return self._instrument_cache
        resp = await self._request("GET", f"{BASE_URL}/market/instruments", params={"source": "equity"})
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            symbol = row.get("TRADING_SYMBOL", "").strip()
            sec_id = row.get("SECURITY_ID", "").strip()
            if symbol and sec_id:
                self._instrument_cache[symbol] = sec_id
        self._instrument_cache_time = time.monotonic()
        return self._instrument_cache

    async def get_balance(self) -> float:
        resp = await self._request("GET", f"{BASE_URL}/funds")
        data = resp.json()
        return float(data.get("data", {}).get("available_balance", 0))

    async def get_quote(self, symbol: str, exchange: str) -> Quote:
        elapsed = time.monotonic() - self._last_quote_time
        if elapsed < self._quote_interval:
            await asyncio.sleep(self._quote_interval - elapsed)
        self._last_quote_time = time.monotonic()
        instruments = await self.get_instruments()
        sec_id = instruments.get(symbol)
        if not sec_id:
            raise ValueError(f"Unknown symbol: {symbol}")
        scrip_code = f"{exchange}_{sec_id}"
        resp = await self._request("GET", f"{BASE_URL}/market/quotes/full", params={"scrip-codes": scrip_code})
        quote_data = resp.json()["data"][scrip_code]
        return Quote(
            symbol=symbol,
            exchange=exchange,
            price=quote_data["live_price"],
            volume=quote_data.get("volume", 0),
            day_high=quote_data.get("day_high", 0),
            day_low=quote_data.get("day_low", 0),
        )

    async def place_order(self, order: Order) -> OrderResult:
        algo_id = ALGO_ID_NSE if order.exchange == "NSE" else ALGO_ID_BSE
        payload = {
            "txn_type": order.txn_type,
            "exchange": order.exchange,
            "segment": "EQUITY",
            "product": order.product,
            "order_type": order.order_type,
            "validity": order.validity,
            "security_id": order.security_id,
            "qty": order.qty,
            "algo_id": algo_id,
        }
        if order.limit_price is not None and order.order_type == "LIMIT":
            payload["limit_price"] = order.limit_price
        resp = await self._request("POST", f"{BASE_URL}/order", json=payload)
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
