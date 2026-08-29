# Telegram Stock Trading Agent

Standalone Python application that monitors 4 private Telegram channels for stock tips, extracts structured trade signals using LLMs, validates them through a deterministic risk engine, and executes trades on INDstocks after user approval via Telegram.

## Architecture

```
Scheduler (APScheduler, every 10 min)
        │
        ▼
telegram_reader.py
  Telethon: fetch new messages from 4 private channels
  Track last_message_id per channel in SQLite
        │
        ▼
stock_agent.py
  Tier 1: Nemotron 3.5 Lightning (free, OpenRouter)
    → "Is this a stock tip?" (yes/no filter)
  Tier 2: Nemotron 3 Super (free, OpenRouter)
    → Extract structured trade details (JSON)
        │
        ▼
risk_engine.py
  Deterministic validation:
    - Is symbol valid on NSE/BSE?
    - Is stop-loss present? (default 15% if missing)
    - Calculate position size (tip % or 10% of wallet)
    - Is wallet balance sufficient?
    - Is this a duplicate signal?
    - Is the market open?
    - Is signal age acceptable?
        │
        ▼
approval_bot.py
  Send trade card to user's Telegram:
    1. Original message verbatim
    2. Extracted trade details (symbol, action, entry, SL, targets, qty, amount)
    3. "Reply A to approve, R to reject"
  Listen for reply (real-time event handler, not polling)
        │
        ▼
Re-approval loop:
  On "A" reply:
    - Fetch live price
    - If price outside original entry_min..entry_max:
        Send new card with updated numbers, wait again
    - If price within range:
        Execute trade
  On "R" reply:
    - Cancel, log decision
  Loop until user approves at valid price or rejects
        │
        ▼
brokers/indstocks.py
  Re-validate price one final time
  Place order via INDstocks REST API
  Log execution to audit_log
```

## Concurrency Model

Two concurrent tasks run inside a single asyncio event loop:

1. **Scheduler task:** APScheduler triggers every 10 minutes. Fetches messages from the 4 channels, runs them through the LLM pipeline, validates, and sends approval cards. Exits after processing.
2. **Approval listener task:** Persistent Telethon event handler on the bot's own chat. Fires only when the user replies. No polling, no timeout.

Both share the same Telethon client and SQLite connection.

## Project Structure

```
connectTelegram/
├── config.py              — channels, API keys, risk defaults, all from .env
├── main.py                — entrypoint: starts scheduler + approval listener
├── telegram_reader.py     — fetch new messages since last_message_id
├── stock_agent.py         — two-tier LLM: signal detection + extraction
├── risk_engine.py         — deterministic validation rules
├── approval_bot.py        — send trade cards, handle A/R replies
├── market_data.py         — live price/quote fetching
├── db.py                  — SQLite schema, queries, migrations
├── brokers/
│   ├── base.py            — abstract BrokerInterface (ABC)
│   └── indstocks.py       — INDstocks REST API implementation
├── requirements.txt
├── .env.example
├── docker-compose.yml
└── Dockerfile
```

## Module Specifications

### config.py

Loads all configuration from environment variables (.env file). No hardcoded secrets.

```
TELEGRAM_API_ID          — Telegram app API ID (from my.telegram.org)
TELEGRAM_API_HASH        — Telegram app API hash
TELEGRAM_SESSION_NAME    — Telethon session file name
WATCHED_CHANNELS         — comma-separated list of 4 channel/group IDs
TELEGRAM_BOT_TOKEN       — bot token from @BotFather (for approvals)
APPROVAL_CHAT_ID         — your chat ID with the bot (for approvals)

OPENROUTER_API_KEY       — OpenRouter API key
TIER1_MODEL              — default: nvidia/nemotron-3.5-lightning:free
TIER2_MODEL              — default: nvidia/nemotron-3-super-120b-a12b:free

INDSTOCKS_API_KEY        — INDstocks API key
INDSTOCKS_API_SECRET     — INDstocks API secret

DEFAULT_STOP_LOSS_PCT    — default: 15
DEFAULT_ALLOCATION_PCT   — default: 10
MAX_SIGNAL_AGE_MINUTES   — default: 60
POLL_INTERVAL_MINUTES    — default: 10
```

### telegram_reader.py

- Uses Telethon with a **user account** (not a bot) to access private groups.
- On each poll cycle, fetches messages newer than `last_message_id` for each channel.
- Updates `last_message_id` in the `messages` table after processing.
- Stores every fetched message in SQLite for context and audit.

### stock_agent.py

Two-tier LLM pipeline via OpenRouter REST API (no SDK dependency).

**Tier 1 (Nemotron 3.5 Lightning):** Signal detection.
- Input: message text
- System prompt: "You are a stock tip detector. Respond with JSON: {\"is_tip\": true/false, \"confidence\": 0.0-1.0}. A stock tip contains a buy/sell recommendation with a specific stock name. General market commentary, news, or discussion is NOT a tip."
- If `is_tip == false` or `confidence < 0.6`: discard, done.

**Tier 2 (Nemotron 3 Super):** Trade extraction.
- Input: message text + last 5 messages from the same channel for context
- System prompt instructs structured JSON output:

```json
{
  "symbol": "RELIANCE",
  "exchange": "NSE",
  "action": "BUY",
  "entry_min": 1482.0,
  "entry_max": 1490.0,
  "stop_loss": 1455.0,
  "targets": [1525.0, 1550.0],
  "allocation_pct": null,
  "confidence": 0.87,
  "reasoning": "Breakout signal with explicit entry, SL, and targets"
}
```

- `allocation_pct` is null if the tip does not specify a percentage.
- If the LLM cannot extract a valid signal (missing symbol or entry), return null.

### risk_engine.py

Deterministic Python rules. The LLM does not make risk decisions.

Validation checks (all must pass):
1. **Symbol resolution:** LLM output may contain a company name or approximate ticker. Validate against a cached NSE/BSE instrument list (CSV from NSE, refreshed daily). If exact ticker match fails, fuzzy-search by company name (e.g., "Reliance" -> "RELIANCE", "Infosys" -> "INFY"). Reject if no match found.
2. **Stop-loss present:** use tip's SL if provided, else set at 15% below entry_max
3. **Position size:** use tip's `allocation_pct` if present, else 10% of wallet balance
4. **Balance check:** wallet has enough for the calculated position
5. **Duplicate check:** no pending or executed signal for this symbol from the same source within 24h
6. **Market hours:** IST 9:15 AM to 3:30 PM on weekdays (skip if AMO supported later)
7. **Signal age:** reject if original message is older than MAX_SIGNAL_AGE_MINUTES

Output: `ValidationResult` with `valid: bool`, `reason: str`, and the enriched trade candidate with calculated quantity, amount, and SL price.

### approval_bot.py

Uses a **Telegram bot** (created via @BotFather, separate from the user account) to send and receive approval messages. The user must create a bot, start a chat with it, and configure the bot token and chat ID in .env.

**Sending the trade card:**

```
--- TRADE CANDIDATE ---

Original message:
"{verbatim original message text}"

Extracted trade:
Symbol: RELIANCE (NSE)
Action: BUY
Entry range: 1,482 - 1,490
Current price: 1,486
Stop-loss: 1,455
Targets: 1,525 / 1,550
Quantity: 13
Amount: ~19,318
Allocation: 10% of wallet

Risk/reward to T1: 1.4x
Signal age: 3 min
LLM confidence: 87%

Reply A to approve, R to reject
---
```

**Handling replies:**
- Listens via `@bot.on(events.NewMessage)` on APPROVAL_CHAT_ID only.
- Accepts: "A", "a", "approve", "Approve", "yes", "y" (case-insensitive, trimmed)
- Rejects: "R", "r", "reject", "Reject", "no", "n"
- Anything else: reply "Unrecognized. Reply A to approve, R to reject."

**Re-approval loop:**
- On approval, fetch live price.
- If current price is outside [entry_min, entry_max]: send a new card with updated current price, recalculated quantity/amount, and updated risk/reward. Wait for reply again.
- If current price is within range: proceed to broker execution.
- No timeout. The trade candidate stays pending until explicitly approved or rejected.

### market_data.py

Fetches live stock quotes. Uses a free source (Yahoo Finance via `yfinance` library, or INDstocks quote API if available).

- `get_quote(symbol, exchange) -> Quote` returns current price, volume, day high/low.
- Used by risk_engine (pre-approval) and approval_bot (re-approval check).

### db.py

SQLite database: `agent.db`

**Tables:**

`messages` — every fetched message
- id, channel_id, message_id, text, timestamp, processed (bool)

`signals` — LLM-extracted signals
- id, message_id, symbol, exchange, action, entry_min, entry_max, stop_loss, targets (JSON), allocation_pct, confidence, reasoning, created_at

`trade_candidates` — validated, pending approval
- id, signal_id, symbol, quantity, amount, stop_loss, status (pending/approved/rejected/executed/expired), current_price_at_send, created_at, decided_at

`decisions` — approval/rejection log
- id, trade_candidate_id, decision (approve/reject), decided_at, price_at_decision

`audit_log` — all broker API calls and results
- id, trade_candidate_id, action, request (JSON), response (JSON), timestamp

### brokers/base.py

Abstract broker interface:

```python
class BrokerInterface(ABC):
    async def authenticate(self) -> None
    async def get_balance(self) -> float
    async def get_quote(self, symbol: str, exchange: str) -> Quote
    async def place_order(self, order: Order) -> OrderResult
    async def get_order_status(self, order_id: str) -> OrderStatus
    async def get_positions(self) -> list[Position]
```

### brokers/indstocks.py

Implements BrokerInterface for INDstocks REST API.

- Base URL: `https://api.indstocks.com`
- Auth: API key + secret in headers
- `place_order` maps to `POST /order` with params: txn_type, exchange, segment, security_id, qty, order_type, limit_price, validity, product
- Rate limit: 10 requests/second (respect 429 with exponential backoff)

## Deployment

**docker-compose.yml** runs a single service:
- Python 3.12 slim image
- Mounts `.env` for secrets
- Mounts `agent.db` volume for persistence
- Restart policy: `unless-stopped`

## Dependencies

```
telethon           — Telegram client (user account + bot)
apscheduler        — 10-minute polling scheduler
httpx              — async HTTP for OpenRouter and INDstocks APIs
yfinance           — market data fallback
aiosqlite          — async SQLite
python-dotenv      — .env loading
```

## What This Spec Does NOT Cover

- Specific Telegram channel IDs (configured at runtime via .env)
- INDstocks API authentication flow details (will be implemented from their docs)
- Exact LLM prompt tuning (will be refined by sampling real messages)
- AMO (After Market Orders) support
- Partial position exits or trailing stop-loss
- Multiple broker simultaneous execution
