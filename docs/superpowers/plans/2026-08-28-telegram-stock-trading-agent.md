# Telegram Stock Trading Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Python application that polls 4 private Telegram channels every 10 minutes, extracts stock tips via Nemotron LLMs on OpenRouter, validates through a deterministic risk engine, and executes trades on INDstocks after Telegram-based user approval.

**Architecture:** Two concurrent asyncio tasks in one process: an APScheduler polling job (reads channels, runs LLM pipeline, sends approval cards) and a persistent Telegram bot listener (handles user A/R replies, triggers broker execution with re-approval on price drift). SQLite for all state.

**Tech Stack:** Python 3.12, Telethon, APScheduler, httpx, aiosqlite, yfinance, python-dotenv, Docker

## Global Constraints

- Python 3.12+
- All secrets loaded from `.env`, never hardcoded
- Async throughout (asyncio event loop)
- No SDK dependencies for OpenRouter or INDstocks (raw httpx calls)
- Broker layer abstracted behind `BrokerInterface` ABC
- LLM layer: Nemotron 3.5 Lightning (Tier 1), Nemotron 3 Super (Tier 2), both `:free` on OpenRouter
- INDstocks API: token-based auth, `Authorization` header, 10 req/s rate limit
- SQLite database: `agent.db`
- Docker deployment with `unless-stopped` restart

---

### Task 1: Project Scaffolding, Config, and Database

**Files:**
- Create: `config.py`
- Create: `db.py`
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.gitignore`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `config.py`: module-level constants `TELEGRAM_API_ID: int`, `TELEGRAM_API_HASH: str`, `TELEGRAM_SESSION_NAME: str`, `WATCHED_CHANNELS: list[int]`, `TELEGRAM_BOT_TOKEN: str`, `APPROVAL_CHAT_ID: int`, `OPENROUTER_API_KEY: str`, `TIER1_MODEL: str`, `TIER2_MODEL: str`, `INDSTOCKS_TOKEN: str`, `DEFAULT_STOP_LOSS_PCT: float`, `DEFAULT_ALLOCATION_PCT: float`, `MAX_SIGNAL_AGE_MINUTES: int`, `POLL_INTERVAL_MINUTES: int`
  - `db.py`: `async def init_db() -> aiosqlite.Connection`, `async def save_message(conn, channel_id, message_id, text, timestamp) -> int`, `async def get_last_message_id(conn, channel_id) -> int | None`, `async def update_last_message_id(conn, channel_id, message_id) -> None`, `async def get_recent_messages(conn, channel_id, limit=5) -> list[dict]`, `async def save_signal(conn, message_id, signal_data: dict) -> int`, `async def save_trade_candidate(conn, signal_id, symbol, quantity, amount, stop_loss, current_price) -> int`, `async def get_pending_candidate(conn, candidate_id) -> dict | None`, `async def update_candidate_status(conn, candidate_id, status, decided_at=None) -> None`, `async def save_decision(conn, candidate_id, decision, price_at_decision) -> None`, `async def save_audit_log(conn, candidate_id, action, request_data, response_data) -> None`, `async def has_duplicate_signal(conn, symbol, channel_id, hours=24) -> bool`

- [ ] **Step 1: Write the failing test for database initialization**

```python
# tests/test_db.py
import asyncio
import aiosqlite
import pytest
from db import init_db, save_message, get_last_message_id, update_last_message_id

@pytest.fixture
def db_conn():
    async def _make():
        conn = await aiosqlite.connect(":memory:")
        await init_db(conn)
        return conn
    conn = asyncio.get_event_loop().run_until_complete(_make())
    yield conn
    asyncio.get_event_loop().run_until_complete(conn.close())

def test_init_db_creates_tables(db_conn):
    async def _check():
        cursor = await db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in await cursor.fetchall()]
        assert "messages" in tables
        assert "signals" in tables
        assert "trade_candidates" in tables
        assert "decisions" in tables
        assert "audit_log" in tables
    asyncio.get_event_loop().run_until_complete(_check())

def test_save_and_get_message(db_conn):
    async def _check():
        msg_id = await save_message(db_conn, channel_id=123, message_id=456, text="Buy RELIANCE", timestamp="2026-08-28T10:00:00")
        assert msg_id is not None
        last_id = await get_last_message_id(db_conn, channel_id=123)
        assert last_id == 456
    asyncio.get_event_loop().run_until_complete(_check())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/jagshep/IdeaProjects/Pankaj-Ideas/n8n-projects/connectTelegram && python -m pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 3: Create requirements.txt**

```
telethon==1.36.0
APScheduler==3.10.4
httpx==0.27.0
yfinance==0.2.40
aiosqlite==0.20.0
python-dotenv==1.0.1
pytest==8.3.2
pytest-asyncio==0.23.8
```

- [ ] **Step 4: Create .env.example**

```
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_SESSION_NAME=stock_agent
WATCHED_CHANNELS=
TELEGRAM_BOT_TOKEN=
APPROVAL_CHAT_ID=

OPENROUTER_API_KEY=
TIER1_MODEL=nvidia/nemotron-3.5-lightning:free
TIER2_MODEL=nvidia/nemotron-3-super-120b-a12b:free

INDSTOCKS_TOKEN=

DEFAULT_STOP_LOSS_PCT=15
DEFAULT_ALLOCATION_PCT=10
MAX_SIGNAL_AGE_MINUTES=60
POLL_INTERVAL_MINUTES=10
```

- [ ] **Step 5: Create .gitignore**

```
__pycache__/
*.pyc
.env
*.session
*.session-journal
agent.db
.venv/
```

- [ ] **Step 6: Implement config.py**

```python
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_API_ID = int(os.environ["TELEGRAM_API_ID"])
TELEGRAM_API_HASH = os.environ["TELEGRAM_API_HASH"]
TELEGRAM_SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "stock_agent")
WATCHED_CHANNELS = [int(c.strip()) for c in os.environ["WATCHED_CHANNELS"].split(",")]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
APPROVAL_CHAT_ID = int(os.environ["APPROVAL_CHAT_ID"])

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
TIER1_MODEL = os.getenv("TIER1_MODEL", "nvidia/nemotron-3.5-lightning:free")
TIER2_MODEL = os.getenv("TIER2_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")

INDSTOCKS_TOKEN = os.environ["INDSTOCKS_TOKEN"]

DEFAULT_STOP_LOSS_PCT = float(os.getenv("DEFAULT_STOP_LOSS_PCT", "15"))
DEFAULT_ALLOCATION_PCT = float(os.getenv("DEFAULT_ALLOCATION_PCT", "10"))
MAX_SIGNAL_AGE_MINUTES = int(os.getenv("MAX_SIGNAL_AGE_MINUTES", "60"))
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "10"))
```

- [ ] **Step 7: Implement db.py**

```python
import aiosqlite
import json
from datetime import datetime


async def init_db(conn: aiosqlite.Connection) -> None:
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            processed INTEGER DEFAULT 0,
            UNIQUE(channel_id, message_id)
        );
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL REFERENCES messages(id),
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL,
            action TEXT NOT NULL,
            entry_min REAL NOT NULL,
            entry_max REAL NOT NULL,
            stop_loss REAL NOT NULL,
            targets TEXT NOT NULL,
            allocation_pct REAL,
            confidence REAL NOT NULL,
            reasoning TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS trade_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER NOT NULL REFERENCES signals(id),
            symbol TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            amount REAL NOT NULL,
            stop_loss REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            current_price_at_send REAL,
            entry_min REAL NOT NULL,
            entry_max REAL NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            decided_at TEXT
        );
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_candidate_id INTEGER NOT NULL REFERENCES trade_candidates(id),
            decision TEXT NOT NULL,
            decided_at TEXT NOT NULL DEFAULT (datetime('now')),
            price_at_decision REAL
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_candidate_id INTEGER,
            action TEXT NOT NULL,
            request TEXT,
            response TEXT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    await conn.commit()


async def save_message(conn, channel_id, message_id, text, timestamp):
    cursor = await conn.execute(
        "INSERT OR IGNORE INTO messages (channel_id, message_id, text, timestamp) VALUES (?, ?, ?, ?)",
        (channel_id, message_id, text, str(timestamp)),
    )
    await conn.commit()
    return cursor.lastrowid


async def get_last_message_id(conn, channel_id):
    cursor = await conn.execute(
        "SELECT MAX(message_id) FROM messages WHERE channel_id = ?",
        (channel_id,),
    )
    row = await cursor.fetchone()
    return row[0] if row and row[0] else None


async def update_last_message_id(conn, channel_id, message_id):
    pass


async def get_recent_messages(conn, channel_id, limit=5):
    cursor = await conn.execute(
        "SELECT message_id, text, timestamp FROM messages WHERE channel_id = ? ORDER BY message_id DESC LIMIT ?",
        (channel_id, limit),
    )
    rows = await cursor.fetchall()
    return [{"message_id": r[0], "text": r[1], "timestamp": r[2]} for r in rows]


async def save_signal(conn, message_db_id, signal_data):
    cursor = await conn.execute(
        """INSERT INTO signals (message_id, symbol, exchange, action, entry_min, entry_max, stop_loss, targets, allocation_pct, confidence, reasoning)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            message_db_id,
            signal_data["symbol"],
            signal_data["exchange"],
            signal_data["action"],
            signal_data["entry_min"],
            signal_data["entry_max"],
            signal_data["stop_loss"],
            json.dumps(signal_data["targets"]),
            signal_data.get("allocation_pct"),
            signal_data["confidence"],
            signal_data.get("reasoning"),
        ),
    )
    await conn.commit()
    return cursor.lastrowid


async def save_trade_candidate(conn, signal_id, symbol, quantity, amount, stop_loss, current_price, entry_min, entry_max):
    cursor = await conn.execute(
        """INSERT INTO trade_candidates (signal_id, symbol, quantity, amount, stop_loss, current_price_at_send, entry_min, entry_max)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (signal_id, symbol, quantity, amount, stop_loss, current_price, entry_min, entry_max),
    )
    await conn.commit()
    return cursor.lastrowid


async def get_pending_candidate(conn, candidate_id):
    cursor = await conn.execute(
        """SELECT tc.*, s.exchange, s.action, s.targets, s.reasoning, s.confidence, s.allocation_pct,
                  m.text as original_message, m.channel_id
           FROM trade_candidates tc
           JOIN signals s ON tc.signal_id = s.id
           JOIN messages m ON s.message_id = m.id
           WHERE tc.id = ? AND tc.status = 'pending'""",
        (candidate_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


async def update_candidate_status(conn, candidate_id, status, decided_at=None):
    decided_at = decided_at or datetime.utcnow().isoformat()
    await conn.execute(
        "UPDATE trade_candidates SET status = ?, decided_at = ? WHERE id = ?",
        (status, decided_at, candidate_id),
    )
    await conn.commit()


async def save_decision(conn, candidate_id, decision, price_at_decision):
    await conn.execute(
        "INSERT INTO decisions (trade_candidate_id, decision, price_at_decision) VALUES (?, ?, ?)",
        (candidate_id, decision, price_at_decision),
    )
    await conn.commit()


async def save_audit_log(conn, candidate_id, action, request_data, response_data):
    await conn.execute(
        "INSERT INTO audit_log (trade_candidate_id, action, request, response) VALUES (?, ?, ?, ?)",
        (candidate_id, action, json.dumps(request_data), json.dumps(response_data)),
    )
    await conn.commit()


async def has_duplicate_signal(conn, symbol, channel_id, hours=24):
    cursor = await conn.execute(
        """SELECT COUNT(*) FROM signals s
           JOIN messages m ON s.message_id = m.id
           JOIN trade_candidates tc ON s.id = tc.signal_id
           WHERE s.symbol = ? AND m.channel_id = ?
             AND tc.status IN ('pending', 'approved', 'executed')
             AND s.created_at > datetime('now', ? || ' hours')""",
        (symbol, channel_id, f"-{hours}"),
    )
    row = await cursor.fetchone()
    return row[0] > 0
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd /Users/jagshep/IdeaProjects/Pankaj-Ideas/n8n-projects/connectTelegram && pip install -r requirements.txt && python -m pytest tests/test_db.py -v`
Expected: PASS

- [ ] **Step 9: Create Dockerfile and docker-compose.yml**

```dockerfile
# Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
```

```yaml
# docker-compose.yml
services:
  stock-agent:
    build: .
    env_file: .env
    volumes:
      - ./agent.db:/app/agent.db
      - ./stock_agent.session:/app/stock_agent.session
    restart: unless-stopped
```

- [ ] **Step 10: Commit**

```bash
git add config.py db.py requirements.txt .env.example .gitignore Dockerfile docker-compose.yml tests/test_db.py
git commit -m "feat: project scaffolding with config, database, and Docker setup"
```

---

### Task 2: Broker Abstraction and INDstocks Implementation

**Files:**
- Create: `brokers/__init__.py`
- Create: `brokers/base.py`
- Create: `brokers/indstocks.py`
- Test: `tests/test_broker_indstocks.py`

**Interfaces:**
- Consumes: `config.INDSTOCKS_TOKEN`
- Produces:
  - `brokers.base`: `class Quote(symbol, exchange, price, volume, day_high, day_low)` (dataclass), `class Order(symbol, exchange, security_id, txn_type, qty, order_type, limit_price, product, validity)` (dataclass), `class OrderResult(order_id, status)` (dataclass), `class Position(security_id, symbol, exchange, net_qty, avg_price)` (dataclass), `class BrokerInterface(ABC)` with methods `async def get_balance(self) -> float`, `async def get_quote(self, symbol, exchange) -> Quote`, `async def place_order(self, order: Order) -> OrderResult`, `async def get_positions(self) -> list[Position]`, `async def get_instruments(self) -> dict[str, str]` (symbol -> security_id mapping)
  - `brokers.indstocks`: `class INDstocksBroker(BrokerInterface)` with `__init__(self, token: str, http_client: httpx.AsyncClient | None = None)`

- [ ] **Step 1: Write failing tests for broker**

```python
# tests/test_broker_indstocks.py
import pytest
import httpx
import json
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
    client.post = AsyncMock(return_value=mock_response)

    broker = INDstocksBroker(token="test_token", http_client=client)
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
    client.post.assert_called_once()
    call_kwargs = client.post.call_args
    body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
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
    client.get = AsyncMock(return_value=mock_response)

    broker = INDstocksBroker(token="test_token", http_client=client)
    broker._instrument_cache = {"RELIANCE": "2885"}
    quote = await broker.get_quote("RELIANCE", "NSE")

    assert quote.price == 1486.0
    assert quote.volume == 3546732
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_broker_indstocks.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'brokers'`

- [ ] **Step 3: Implement brokers/base.py**

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Quote:
    symbol: str
    exchange: str
    price: float
    volume: int
    day_high: float
    day_low: float


@dataclass
class Order:
    symbol: str
    exchange: str
    security_id: str
    txn_type: str  # BUY or SELL
    qty: int
    order_type: str  # LIMIT or MARKET
    limit_price: float | None
    product: str  # CNC, INTRADAY, MARGIN
    validity: str  # DAY or IOC


@dataclass
class OrderResult:
    order_id: str
    status: str


@dataclass
class Position:
    security_id: str
    symbol: str
    exchange: str
    net_qty: int
    avg_price: float


class BrokerInterface(ABC):
    @abstractmethod
    async def get_balance(self) -> float: ...

    @abstractmethod
    async def get_quote(self, symbol: str, exchange: str) -> Quote: ...

    @abstractmethod
    async def place_order(self, order: Order) -> OrderResult: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_instruments(self) -> dict[str, str]: ...
```

- [ ] **Step 4: Implement brokers/indstocks.py**

```python
import httpx
import csv
import io
from brokers.base import BrokerInterface, Quote, Order, OrderResult, Position

BASE_URL = "https://api.indstocks.com"
ALGO_ID_NSE = "99999"
ALGO_ID_BSE = "9999999999999999"


class INDstocksBroker(BrokerInterface):
    def __init__(self, token: str, http_client: httpx.AsyncClient | None = None):
        self._token = token
        self._client = http_client or httpx.AsyncClient(timeout=30)
        self._headers = {"Authorization": token, "Content-Type": "application/json"}
        self._instrument_cache: dict[str, str] = {}

    async def get_instruments(self) -> dict[str, str]:
        if self._instrument_cache:
            return self._instrument_cache
        resp = await self._client.get(
            f"{BASE_URL}/market/instruments",
            params={"source": "equity"},
            headers=self._headers,
        )
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            symbol = row.get("TRADING_SYMBOL", "").strip()
            sec_id = row.get("SECURITY_ID", "").strip()
            if symbol and sec_id:
                self._instrument_cache[symbol] = sec_id
        return self._instrument_cache

    async def get_balance(self) -> float:
        resp = await self._client.get(
            f"{BASE_URL}/user/funds",
            headers=self._headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return float(data.get("data", {}).get("available_balance", 0))

    async def get_quote(self, symbol: str, exchange: str) -> Quote:
        instruments = await self.get_instruments()
        sec_id = instruments.get(symbol)
        if not sec_id:
            raise ValueError(f"Unknown symbol: {symbol}")
        scrip_code = f"{exchange}_{sec_id}"
        resp = await self._client.get(
            f"{BASE_URL}/market/quotes/full",
            params={"scrip-codes": scrip_code},
            headers=self._headers,
        )
        resp.raise_for_status()
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
        resp = await self._client.post(
            f"{BASE_URL}/order",
            json=payload,
            headers=self._headers,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        return OrderResult(order_id=data["order_id"], status=data["order_status"])

    async def get_positions(self) -> list[Position]:
        resp = await self._client.get(
            f"{BASE_URL}/portfolio/positions",
            params={"segment": "equity", "product": "cnc"},
            headers=self._headers,
        )
        resp.raise_for_status()
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
```

- [ ] **Step 5: Create brokers/__init__.py**

```python
# empty
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_broker_indstocks.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add brokers/ tests/test_broker_indstocks.py
git commit -m "feat: broker abstraction with INDstocks REST API implementation"
```

---

### Task 3: Market Data Module

**Files:**
- Create: `market_data.py`
- Test: `tests/test_market_data.py`

**Interfaces:**
- Consumes: `brokers.base.Quote`, `brokers.indstocks.INDstocksBroker`
- Produces: `async def get_quote(symbol: str, exchange: str, broker: BrokerInterface | None = None) -> Quote` — tries broker first, falls back to yfinance

- [ ] **Step 1: Write failing test**

```python
# tests/test_market_data.py
import pytest
from unittest.mock import AsyncMock
from brokers.base import Quote
from market_data import get_quote


@pytest.mark.asyncio
async def test_get_quote_from_broker():
    mock_broker = AsyncMock()
    mock_broker.get_quote.return_value = Quote(
        symbol="RELIANCE", exchange="NSE", price=1486.0,
        volume=3546732, day_high=1495.0, day_low=1480.0,
    )
    quote = await get_quote("RELIANCE", "NSE", broker=mock_broker)
    assert quote.price == 1486.0
    mock_broker.get_quote.assert_called_once_with("RELIANCE", "NSE")


@pytest.mark.asyncio
async def test_get_quote_falls_back_to_yfinance():
    mock_broker = AsyncMock()
    mock_broker.get_quote.side_effect = Exception("API down")
    quote = await get_quote("RELIANCE", "NSE", broker=mock_broker)
    assert quote is not None
    assert quote.symbol == "RELIANCE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_market_data.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement market_data.py**

```python
import asyncio
import yfinance as yf
from brokers.base import BrokerInterface, Quote

NSE_SUFFIX = ".NS"
BSE_SUFFIX = ".BO"


async def get_quote(symbol: str, exchange: str, broker: BrokerInterface | None = None) -> Quote:
    if broker:
        try:
            return await broker.get_quote(symbol, exchange)
        except Exception:
            pass
    return await _yfinance_quote(symbol, exchange)


async def _yfinance_quote(symbol: str, exchange: str) -> Quote:
    suffix = NSE_SUFFIX if exchange == "NSE" else BSE_SUFFIX
    ticker_symbol = f"{symbol}{suffix}"

    def _fetch():
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.fast_info
        return Quote(
            symbol=symbol,
            exchange=exchange,
            price=float(info.last_price),
            volume=int(info.last_volume or 0),
            day_high=float(info.day_high or 0),
            day_low=float(info.day_low or 0),
        )

    return await asyncio.to_thread(_fetch)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_market_data.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add market_data.py tests/test_market_data.py
git commit -m "feat: market data module with broker-first, yfinance fallback"
```

---

### Task 4: Stock Agent (Two-Tier LLM Pipeline)

**Files:**
- Create: `stock_agent.py`
- Test: `tests/test_stock_agent.py`

**Interfaces:**
- Consumes: `config.OPENROUTER_API_KEY`, `config.TIER1_MODEL`, `config.TIER2_MODEL`
- Produces:
  - `async def detect_signal(text: str, api_key: str, model: str, http_client: httpx.AsyncClient) -> dict | None` — returns `{"is_tip": bool, "confidence": float}` or None
  - `async def extract_trade(text: str, context_messages: list[str], api_key: str, model: str, http_client: httpx.AsyncClient) -> dict | None` — returns structured signal dict or None
  - `async def analyze_message(text: str, context_messages: list[str], api_key: str, tier1_model: str, tier2_model: str, http_client: httpx.AsyncClient) -> dict | None` — full pipeline: detect then extract

- [ ] **Step 1: Write failing tests**

```python
# tests/test_stock_agent.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_stock_agent.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement stock_agent.py**

```python
import json
import httpx

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

TIER1_SYSTEM_PROMPT = """You are a stock tip detector for Indian stock markets (NSE/BSE).
Respond ONLY with JSON: {"is_tip": true/false, "confidence": 0.0-1.0}
A stock tip contains a buy/sell recommendation with a specific stock name and at least one of: entry price, stop-loss, or target.
General market commentary, news, greetings, or discussion is NOT a tip."""

TIER2_SYSTEM_PROMPT = """You are a stock trade signal extractor for Indian markets (NSE/BSE).
Extract the trade signal from the message and return ONLY valid JSON with this exact structure:
{
  "symbol": "TRADING_SYMBOL (e.g. RELIANCE, INFY, TCS)",
  "exchange": "NSE or BSE",
  "action": "BUY or SELL",
  "entry_min": <number>,
  "entry_max": <number>,
  "stop_loss": <number or null>,
  "targets": [<number>, ...],
  "allocation_pct": <number or null>,
  "confidence": <0.0-1.0>,
  "reasoning": "<one line explanation>"
}
Rules:
- Use the NSE trading symbol (e.g., "Reliance Industries" -> "RELIANCE", "Infosys" -> "INFY")
- If only one entry price is given, use it for both entry_min and entry_max
- If stop-loss is not mentioned, set it to null
- If allocation percentage is not mentioned, set it to null
- If you cannot determine the symbol or entry price, return null
- Do NOT wrap in markdown code blocks"""

CONFIDENCE_THRESHOLD = 0.6


async def _call_openrouter(messages, api_key, model, http_client):
    resp = await http_client.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={"model": model, "messages": messages, "temperature": 0.1},
        timeout=30,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(content)


async def detect_signal(text, api_key, model, http_client):
    messages = [
        {"role": "system", "content": TIER1_SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    return await _call_openrouter(messages, api_key, model, http_client)


async def extract_trade(text, context_messages, api_key, model, http_client):
    context_block = ""
    if context_messages:
        context_block = "Recent messages from the same channel for context:\n" + "\n".join(f"- {m}" for m in context_messages) + "\n\n"
    user_content = f"{context_block}Extract the trade signal from this message:\n{text}"
    messages = [
        {"role": "system", "content": TIER2_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    result = await _call_openrouter(messages, api_key, model, http_client)
    if result is None or not isinstance(result, dict):
        return None
    if not result.get("symbol") or result.get("entry_min") is None:
        return None
    return result


async def analyze_message(text, context_messages, api_key, tier1_model, tier2_model, http_client):
    detection = await detect_signal(text, api_key, tier1_model, http_client)
    if not detection or not detection.get("is_tip") or detection.get("confidence", 0) < CONFIDENCE_THRESHOLD:
        return None
    return await extract_trade(text, context_messages, api_key, tier2_model, http_client)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_stock_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add stock_agent.py tests/test_stock_agent.py
git commit -m "feat: two-tier LLM stock signal detection and extraction via OpenRouter"
```

---

### Task 5: Risk Engine

**Files:**
- Create: `risk_engine.py`
- Test: `tests/test_risk_engine.py`

**Interfaces:**
- Consumes: `brokers.base.BrokerInterface` (for balance, instruments, positions), `config.DEFAULT_STOP_LOSS_PCT`, `config.DEFAULT_ALLOCATION_PCT`, `config.MAX_SIGNAL_AGE_MINUTES`, `db.has_duplicate_signal`
- Produces:
  - `@dataclass class ValidationResult: valid: bool, reason: str, symbol: str, exchange: str, action: str, security_id: str | None, quantity: int, amount: float, stop_loss: float, entry_min: float, entry_max: float, targets: list[float], current_price: float`
  - `async def validate_signal(signal: dict, channel_id: int, broker: BrokerInterface, db_conn, message_timestamp: str) -> ValidationResult`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_risk_engine.py
import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timedelta
from brokers.base import Quote
from risk_engine import validate_signal, ValidationResult


def _make_signal(**overrides):
    base = {
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "action": "BUY",
        "entry_min": 1482.0,
        "entry_max": 1490.0,
        "stop_loss": 1455.0,
        "targets": [1525.0, 1550.0],
        "allocation_pct": None,
        "confidence": 0.87,
        "reasoning": "test",
    }
    base.update(overrides)
    return base


def _make_broker(balance=100000, price=1486.0, instruments=None):
    broker = AsyncMock()
    broker.get_balance.return_value = balance
    broker.get_quote.return_value = Quote(
        symbol="RELIANCE", exchange="NSE", price=price,
        volume=1000000, day_high=1495.0, day_low=1480.0,
    )
    broker.get_instruments.return_value = instruments or {"RELIANCE": "2885"}
    return broker


@pytest.mark.asyncio
async def test_valid_signal_passes():
    broker = _make_broker()
    db_conn = AsyncMock()
    timestamp = datetime.utcnow().isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False):
        result = await validate_signal(
            _make_signal(), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is True
    assert result.quantity > 0
    assert result.security_id == "2885"


@pytest.mark.asyncio
async def test_unknown_symbol_rejected():
    broker = _make_broker(instruments={})
    db_conn = AsyncMock()
    timestamp = datetime.utcnow().isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False):
        result = await validate_signal(
            _make_signal(symbol="FAKESTOCK"), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is False
    assert "symbol" in result.reason.lower()


@pytest.mark.asyncio
async def test_default_stop_loss_applied():
    signal = _make_signal(stop_loss=None)
    broker = _make_broker()
    db_conn = AsyncMock()
    timestamp = datetime.utcnow().isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False):
        result = await validate_signal(
            signal, channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is True
    assert result.stop_loss == 1490.0 * 0.85  # 15% below entry_max


@pytest.mark.asyncio
async def test_insufficient_balance_rejected():
    broker = _make_broker(balance=500)
    db_conn = AsyncMock()
    timestamp = datetime.utcnow().isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False):
        result = await validate_signal(
            _make_signal(), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is False
    assert "balance" in result.reason.lower()


@pytest.mark.asyncio
async def test_duplicate_signal_rejected():
    broker = _make_broker()
    db_conn = AsyncMock()
    timestamp = datetime.utcnow().isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=True):
        result = await validate_signal(
            _make_signal(), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=timestamp,
        )
    assert result.valid is False
    assert "duplicate" in result.reason.lower()


@pytest.mark.asyncio
async def test_old_signal_rejected():
    broker = _make_broker()
    db_conn = AsyncMock()
    old_timestamp = (datetime.utcnow() - timedelta(hours=2)).isoformat()

    with patch("risk_engine.has_duplicate_signal", return_value=False):
        result = await validate_signal(
            _make_signal(), channel_id=123, broker=broker,
            db_conn=db_conn, message_timestamp=old_timestamp,
        )
    assert result.valid is False
    assert "age" in result.reason.lower() or "old" in result.reason.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_risk_engine.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement risk_engine.py**

```python
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import get_close_matches

from db import has_duplicate_signal as _has_duplicate
from config import DEFAULT_STOP_LOSS_PCT, DEFAULT_ALLOCATION_PCT, MAX_SIGNAL_AGE_MINUTES
from brokers.base import BrokerInterface


@dataclass
class ValidationResult:
    valid: bool
    reason: str
    symbol: str = ""
    exchange: str = ""
    action: str = ""
    security_id: str | None = None
    quantity: int = 0
    amount: float = 0.0
    stop_loss: float = 0.0
    entry_min: float = 0.0
    entry_max: float = 0.0
    targets: list[float] = field(default_factory=list)
    current_price: float = 0.0


def _fail(reason):
    return ValidationResult(valid=False, reason=reason)


async def has_duplicate_signal(conn, symbol, channel_id, hours=24):
    return await _has_duplicate(conn, symbol, channel_id, hours)


def _resolve_symbol(symbol_from_llm: str, instruments: dict[str, str]) -> str | None:
    if symbol_from_llm in instruments:
        return symbol_from_llm
    upper = symbol_from_llm.upper()
    if upper in instruments:
        return upper
    matches = get_close_matches(upper, instruments.keys(), n=1, cutoff=0.8)
    if matches:
        return matches[0]
    return None


async def validate_signal(signal, channel_id, broker: BrokerInterface, db_conn, message_timestamp):
    instruments = await broker.get_instruments()
    resolved = _resolve_symbol(signal["symbol"], instruments)
    if not resolved:
        return _fail(f"Unknown symbol: {signal['symbol']}")
    security_id = instruments[resolved]

    stop_loss = signal.get("stop_loss")
    entry_max = signal["entry_max"]
    if stop_loss is None:
        stop_loss = round(entry_max * (1 - DEFAULT_STOP_LOSS_PCT / 100), 2)

    msg_time = datetime.fromisoformat(message_timestamp)
    age = datetime.utcnow() - msg_time
    if age > timedelta(minutes=MAX_SIGNAL_AGE_MINUTES):
        return _fail(f"Signal too old: {int(age.total_seconds() // 60)} min")

    if await has_duplicate_signal(db_conn, resolved, channel_id):
        return _fail(f"Duplicate signal for {resolved} in last 24h")

    balance = await broker.get_balance()
    alloc_pct = signal.get("allocation_pct") or DEFAULT_ALLOCATION_PCT
    alloc_amount = balance * (alloc_pct / 100)
    quantity = math.floor(alloc_amount / entry_max)
    if quantity < 1:
        return _fail(f"Insufficient balance: need ~{entry_max} but allocation is {alloc_amount:.0f}")
    amount = round(quantity * entry_max, 2)

    quote = await broker.get_quote(resolved, signal["exchange"])

    return ValidationResult(
        valid=True,
        reason="All checks passed",
        symbol=resolved,
        exchange=signal["exchange"],
        action=signal["action"],
        security_id=security_id,
        quantity=quantity,
        amount=amount,
        stop_loss=stop_loss,
        entry_min=signal["entry_min"],
        entry_max=entry_max,
        targets=signal.get("targets", []),
        current_price=quote.price,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_risk_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add risk_engine.py tests/test_risk_engine.py
git commit -m "feat: deterministic risk engine with symbol resolution and validation"
```

---

### Task 6: Telegram Reader

**Files:**
- Create: `telegram_reader.py`
- Test: `tests/test_telegram_reader.py`

**Interfaces:**
- Consumes: `config.TELEGRAM_API_ID`, `config.TELEGRAM_API_HASH`, `config.TELEGRAM_SESSION_NAME`, `config.WATCHED_CHANNELS`, `db.save_message`, `db.get_last_message_id`
- Produces: `async def fetch_new_messages(client: TelegramClient, conn: aiosqlite.Connection, channel_ids: list[int]) -> list[dict]` — returns list of `{"db_id": int, "channel_id": int, "message_id": int, "text": str, "timestamp": str}`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_telegram_reader.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_telegram_reader.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement telegram_reader.py**

```python
from db import save_message, get_last_message_id


async def fetch_new_messages(client, conn, channel_ids):
    all_messages = []
    for channel_id in channel_ids:
        last_id = await get_last_message_id(conn, channel_id)
        min_id = last_id if last_id else 0

        async for message in client.iter_messages(channel_id, min_id=min_id, limit=100):
            if not message.text:
                continue
            db_id = await save_message(
                conn,
                channel_id=channel_id,
                message_id=message.id,
                text=message.text,
                timestamp=str(message.date),
            )
            if db_id:
                all_messages.append({
                    "db_id": db_id,
                    "channel_id": channel_id,
                    "message_id": message.id,
                    "text": message.text,
                    "timestamp": str(message.date),
                })
    return all_messages
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_telegram_reader.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add telegram_reader.py tests/test_telegram_reader.py
git commit -m "feat: telegram channel message fetcher with deduplication"
```

---

### Task 7: Approval Bot

**Files:**
- Create: `approval_bot.py`
- Test: `tests/test_approval_bot.py`

**Interfaces:**
- Consumes: `config.TELEGRAM_BOT_TOKEN`, `config.APPROVAL_CHAT_ID`, `risk_engine.ValidationResult`, `db.get_pending_candidate`, `db.update_candidate_status`, `db.save_decision`, `db.save_audit_log`, `market_data.get_quote`, `brokers.base.BrokerInterface`
- Produces:
  - `def format_trade_card(candidate: dict, validation: ValidationResult, original_message: str) -> str`
  - `def parse_approval_reply(text: str) -> str | None` — returns `"approve"`, `"reject"`, or `None`
  - `async def send_approval(bot_client, chat_id: int, candidate_id: int, card_text: str) -> None`
  - `async def handle_approval_reply(text: str, candidate_id: int, broker: BrokerInterface, db_conn, bot_client, chat_id: int) -> None`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_approval_bot.py
import pytest
from risk_engine import ValidationResult
from approval_bot import format_trade_card, parse_approval_reply


def test_format_trade_card_contains_all_fields():
    validation = ValidationResult(
        valid=True, reason="ok", symbol="RELIANCE", exchange="NSE",
        action="BUY", security_id="2885", quantity=13, amount=19318.0,
        stop_loss=1455.0, entry_min=1482.0, entry_max=1490.0,
        targets=[1525.0, 1550.0], current_price=1486.0,
    )
    card = format_trade_card(
        candidate={"id": 1, "created_at": "2026-08-28T10:00:00", "allocation_pct": 10, "confidence": 0.87},
        validation=validation,
        original_message="Buy RELIANCE above 1480-1490, SL 1455, Targets 1525/1550",
    )
    assert "RELIANCE" in card
    assert "1,482" in card or "1482" in card
    assert "1,455" in card or "1455" in card
    assert "Reply A" in card
    assert "Buy RELIANCE above" in card


def test_parse_approval_reply_accepts_variations():
    assert parse_approval_reply("A") == "approve"
    assert parse_approval_reply("a") == "approve"
    assert parse_approval_reply("approve") == "approve"
    assert parse_approval_reply("Approve") == "approve"
    assert parse_approval_reply("yes") == "approve"
    assert parse_approval_reply("y") == "approve"
    assert parse_approval_reply("  A  ") == "approve"


def test_parse_approval_reply_rejects_variations():
    assert parse_approval_reply("R") == "reject"
    assert parse_approval_reply("r") == "reject"
    assert parse_approval_reply("reject") == "reject"
    assert parse_approval_reply("no") == "reject"
    assert parse_approval_reply("n") == "reject"


def test_parse_approval_reply_unrecognized():
    assert parse_approval_reply("maybe") is None
    assert parse_approval_reply("hello") is None
    assert parse_approval_reply("") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_approval_bot.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement approval_bot.py**

```python
from datetime import datetime
from brokers.base import BrokerInterface, Order
from risk_engine import ValidationResult
from market_data import get_quote
from db import (
    get_pending_candidate, update_candidate_status,
    save_decision, save_audit_log, save_trade_candidate,
)

APPROVE_WORDS = {"a", "approve", "yes", "y"}
REJECT_WORDS = {"r", "reject", "no", "n"}

_pending_candidates: dict[int, dict] = {}


def format_trade_card(candidate, validation, original_message):
    targets_str = " / ".join(f"{t:,.0f}" for t in validation.targets)
    risk = 0
    if validation.targets and validation.stop_loss:
        reward = validation.targets[0] - validation.entry_max
        risk_amt = validation.entry_max - validation.stop_loss
        risk = round(reward / risk_amt, 1) if risk_amt > 0 else 0

    age_str = "N/A"
    created = candidate.get("created_at")
    if created:
        try:
            dt = datetime.fromisoformat(created)
            age_min = int((datetime.utcnow() - dt).total_seconds() / 60)
            age_str = f"{age_min} min"
        except (ValueError, TypeError):
            pass

    alloc_pct = candidate.get("allocation_pct") or 10
    confidence = candidate.get("confidence", 0)
    confidence_pct = int(confidence * 100) if isinstance(confidence, float) and confidence <= 1 else confidence

    return (
        f"--- TRADE CANDIDATE ---\n\n"
        f"Original message:\n\"{original_message}\"\n\n"
        f"Extracted trade:\n"
        f"Symbol: {validation.symbol} ({validation.exchange})\n"
        f"Action: {validation.action}\n"
        f"Entry range: {validation.entry_min:,.0f} - {validation.entry_max:,.0f}\n"
        f"Current price: {validation.current_price:,.0f}\n"
        f"Stop-loss: {validation.stop_loss:,.0f}\n"
        f"Targets: {targets_str}\n"
        f"Quantity: {validation.quantity}\n"
        f"Amount: ~{validation.amount:,.0f}\n"
        f"Allocation: {alloc_pct}% of wallet\n\n"
        f"Risk/reward to T1: {risk}x\n"
        f"Signal age: {age_str}\n"
        f"LLM confidence: {confidence_pct}%\n\n"
        f"Reply A to approve, R to reject\n"
        f"---"
    )


def parse_approval_reply(text):
    cleaned = text.strip().lower()
    if cleaned in APPROVE_WORDS:
        return "approve"
    if cleaned in REJECT_WORDS:
        return "reject"
    return None


async def send_approval(bot_client, chat_id, candidate_id, card_text):
    msg = await bot_client.send_message(chat_id, card_text)
    _pending_candidates[candidate_id] = {"message_id": msg.id}


async def handle_approval_reply(text, candidate_id, broker, db_conn, bot_client, chat_id):
    decision = parse_approval_reply(text)
    if decision is None:
        await bot_client.send_message(chat_id, "Unrecognized. Reply A to approve, R to reject.")
        return

    candidate = await get_pending_candidate(db_conn, candidate_id)
    if not candidate:
        await bot_client.send_message(chat_id, "Trade candidate not found or already processed.")
        return

    if decision == "reject":
        await update_candidate_status(db_conn, candidate_id, "rejected")
        await save_decision(db_conn, candidate_id, "reject", None)
        await bot_client.send_message(chat_id, f"Trade {candidate['symbol']} rejected.")
        return

    quote = await get_quote(candidate["symbol"], candidate["exchange"], broker=broker)
    entry_min = candidate["entry_min"]
    entry_max = candidate["entry_max"]

    if quote.price < entry_min or quote.price > entry_max:
        import math
        alloc_pct = candidate.get("allocation_pct") or 10
        balance = await broker.get_balance()
        new_qty = math.floor((balance * alloc_pct / 100) / quote.price)
        new_amount = round(new_qty * quote.price, 2)

        targets = []
        if candidate.get("targets"):
            import json
            targets = json.loads(candidate["targets"]) if isinstance(candidate["targets"], str) else candidate["targets"]

        reapproval_card = (
            f"--- PRICE CHANGED - RE-APPROVAL NEEDED ---\n\n"
            f"Symbol: {candidate['symbol']} ({candidate['exchange']})\n"
            f"Original entry range: {entry_min:,.0f} - {entry_max:,.0f}\n"
            f"Current price: {quote.price:,.0f}\n"
            f"Stop-loss: {candidate['stop_loss']:,.0f}\n"
            f"New quantity: {new_qty}\n"
            f"New amount: ~{new_amount:,.0f}\n\n"
            f"Reply A to approve at current price, R to reject\n"
            f"---"
        )
        await bot_client.send_message(chat_id, reapproval_card)
        return

    instruments = await broker.get_instruments()
    security_id = instruments.get(candidate["symbol"])
    order = Order(
        symbol=candidate["symbol"],
        exchange=candidate["exchange"],
        security_id=security_id,
        txn_type=candidate["action"],
        qty=candidate["quantity"],
        order_type="LIMIT",
        limit_price=quote.price,
        product="CNC",
        validity="DAY",
    )
    await save_decision(db_conn, candidate_id, "approve", quote.price)
    result = await broker.place_order(order)
    await save_audit_log(
        db_conn, candidate_id, "place_order",
        {"symbol": order.symbol, "qty": order.qty, "price": order.limit_price},
        {"order_id": result.order_id, "status": result.status},
    )
    await update_candidate_status(db_conn, candidate_id, "executed")
    await bot_client.send_message(
        chat_id,
        f"Order placed: {candidate['symbol']} {candidate['action']} x{order.qty} @ {quote.price:,.0f}\nOrder ID: {result.order_id}",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_approval_bot.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add approval_bot.py tests/test_approval_bot.py
git commit -m "feat: telegram approval bot with re-approval on price drift"
```

---

### Task 8: Main Entrypoint (Scheduler + Listener)

**Files:**
- Create: `main.py`
- Test: manual integration test (requires Telegram credentials)

**Interfaces:**
- Consumes: all modules
- Produces: `main.py` entrypoint that starts both the polling scheduler and the approval listener

- [ ] **Step 1: Implement main.py**

```python
import asyncio
import logging
import aiosqlite
import httpx
from telethon import TelegramClient, events
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from db import init_db, get_recent_messages, save_signal, save_trade_candidate
from telegram_reader import fetch_new_messages
from stock_agent import analyze_message
from risk_engine import validate_signal
from approval_bot import (
    format_trade_card, send_approval, handle_approval_reply,
    parse_approval_reply, _pending_candidates,
)
from brokers.indstocks import INDstocksBroker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("stock_agent")

user_client: TelegramClient = None
bot_client: TelegramClient = None
db_conn: aiosqlite.Connection = None
http_client: httpx.AsyncClient = None
broker: INDstocksBroker = None

_pending_candidate_ids: list[int] = []


async def poll_channels():
    log.info("Polling %d channels...", len(config.WATCHED_CHANNELS))

    messages = await fetch_new_messages(user_client, db_conn, config.WATCHED_CHANNELS)
    log.info("Fetched %d new messages", len(messages))

    for msg in messages:
        context = await get_recent_messages(db_conn, msg["channel_id"], limit=5)
        context_texts = [m["text"] for m in context if m["message_id"] != msg["message_id"]]

        signal = await analyze_message(
            msg["text"], context_texts,
            api_key=config.OPENROUTER_API_KEY,
            tier1_model=config.TIER1_MODEL,
            tier2_model=config.TIER2_MODEL,
            http_client=http_client,
        )
        if not signal:
            continue

        log.info("Signal detected: %s %s", signal.get("action"), signal.get("symbol"))

        signal_id = await save_signal(db_conn, msg["db_id"], signal)

        validation = await validate_signal(
            signal, msg["channel_id"], broker, db_conn, msg["timestamp"],
        )
        if not validation.valid:
            log.info("Signal rejected: %s", validation.reason)
            continue

        candidate_id = await save_trade_candidate(
            db_conn, signal_id, validation.symbol, validation.quantity,
            validation.amount, validation.stop_loss, validation.current_price,
            validation.entry_min, validation.entry_max,
        )

        card = format_trade_card(
            candidate={"id": candidate_id, "created_at": msg["timestamp"],
                       "allocation_pct": signal.get("allocation_pct"), "confidence": signal.get("confidence")},
            validation=validation,
            original_message=msg["text"],
        )
        await send_approval(bot_client, config.APPROVAL_CHAT_ID, candidate_id, card)
        _pending_candidate_ids.append(candidate_id)
        log.info("Approval sent for candidate #%d: %s", candidate_id, validation.symbol)


async def main():
    global user_client, bot_client, db_conn, http_client, broker, _latest_candidate_id

    db_conn = await aiosqlite.connect("agent.db")
    await init_db(db_conn)

    http_client = httpx.AsyncClient(timeout=30)
    broker = INDstocksBroker(token=config.INDSTOCKS_TOKEN, http_client=http_client)

    user_client = TelegramClient(
        config.TELEGRAM_SESSION_NAME,
        config.TELEGRAM_API_ID,
        config.TELEGRAM_API_HASH,
    )
    await user_client.start()
    log.info("User client connected")

    bot_client = TelegramClient(
        "approval_bot",
        config.TELEGRAM_API_ID,
        config.TELEGRAM_API_HASH,
    )
    await bot_client.start(bot_token=config.TELEGRAM_BOT_TOKEN)
    log.info("Bot client connected")

    @bot_client.on(events.NewMessage(chats=config.APPROVAL_CHAT_ID))
    async def on_approval(event):
        if not _pending_candidate_ids:
            return
        candidate_id = _pending_candidate_ids[0]
        await handle_approval_reply(
            event.text, candidate_id, broker, db_conn,
            bot_client, config.APPROVAL_CHAT_ID,
        )
        decision = parse_approval_reply(event.text)
        if decision in ("approve", "reject"):
            _pending_candidate_ids.pop(0)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(poll_channels, "interval", minutes=config.POLL_INTERVAL_MINUTES)
    scheduler.start()
    log.info("Scheduler started (every %d min)", config.POLL_INTERVAL_MINUTES)

    await poll_channels()

    log.info("Listening for approval replies...")
    await bot_client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Create tests/__init__.py**

```python
# empty
```

- [ ] **Step 3: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All tests from Tasks 1-7 pass

- [ ] **Step 4: Commit**

```bash
git add main.py tests/__init__.py
git commit -m "feat: main entrypoint with scheduler and approval listener"
```

---

### Task 9: Integration Test and First Run Setup

**Files:**
- Create: `setup_guide.md` (in project root, for the user to follow)

**Interfaces:**
- Consumes: all modules
- Produces: verified working application

- [ ] **Step 1: Create setup_guide.md**

```markdown
# Setup Guide

## Prerequisites

1. Python 3.12+
2. Docker (optional, for containerized deployment)

## Step 1: Telegram User Account API

1. Go to https://my.telegram.org
2. Log in with your phone number
3. Go to "API development tools"
4. Create an application, note API_ID and API_HASH

## Step 2: Create Approval Bot

1. Open Telegram, search for @BotFather
2. Send /newbot, follow prompts
3. Note the bot token
4. Start a chat with your new bot (send /start)
5. Get your chat ID: send a message to the bot, then visit
   https://api.telegram.org/bot<TOKEN>/getUpdates
   and find chat.id in the response

## Step 3: Get Channel IDs

For each of your 4 private channels:
1. Open Telegram Web (web.telegram.org)
2. Navigate to the channel
3. The URL will show the channel ID (e.g., -1001234567890)

## Step 4: INDstocks API Token

1. Log in to indstocks.com
2. Navigate to API section
3. Copy your access token (expires every 24h)

## Step 5: OpenRouter API Key

1. Go to https://openrouter.ai
2. Create an account
3. Purchase $10 credit (unlocks 1000 req/day on free models)
4. Copy your API key

## Step 6: Configure .env

Copy .env.example to .env and fill in all values.

## Step 7: First Run (Local)

pip install -r requirements.txt
python main.py

On first run, Telethon will ask for your phone number and
verification code to create the session file.

## Step 8: Docker Deployment

After the session file is created locally:

docker-compose up -d --build
docker-compose logs -f stock-agent
```

- [ ] **Step 2: Run the full test suite one final time**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All tests pass

- [ ] **Step 3: Commit**

```bash
git add setup_guide.md
git commit -m "docs: setup guide for first run and deployment"
```
