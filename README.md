# Telegram Stock Trading Agent

Standalone Python application that monitors private Telegram channels for Indian stock tips (NSE/BSE), extracts structured trade signals using LLMs, validates them through a deterministic risk engine, and executes trades on INDstocks after user approval via a Telegram bot.

## How It Works

The system runs on a 10-minute polling loop:

1. **Fetch** new messages from watched Telegram channels (via a user account, not a bot, since these are private channels)
2. **Detect** whether each message is a stock tip using a fast/free LLM (Tier 1 filter)
3. **Extract** structured trade details (symbol, entry, stop-loss, targets) using a more capable LLM (Tier 2)
4. **Validate** the signal through deterministic risk checks (symbol resolution, balance, market hours, duplicates, signal age)
5. **Send** a trade card to the user via a Telegram bot for manual approval
6. **Execute** the trade on INDstocks if approved and the price is still within the entry range

If the price has drifted outside the original entry range by the time the user approves, the system sends a re-approval card with updated numbers. The loop continues until the user either approves at a valid price or rejects.

## Architecture

```
Scheduler (APScheduler, every 10 min)
        |
        v
telegram_reader.py ---- Telethon user client fetches from private channels
        |
        v
stock_agent.py --------- Tier 1: is this a tip? Tier 2: extract trade JSON
        |
        v
risk_engine.py ---------- Validate symbol, balance, market hours, duplicates
        |
        v
approval_bot.py --------- Send trade card to user, listen for A/R reply
        |
        v
brokers/indstocks.py ---- Place order via INDstocks REST API
```

Two concurrent async tasks share a single event loop:
- The **scheduler task** polls channels, runs the LLM pipeline, and sends approval cards.
- The **approval listener** is a persistent Telethon event handler that fires when the user replies. No polling, no timeout.

## Project Structure

```
connectTelegram/
  config.py              -- All configuration from .env (channels, API keys, risk defaults)
  main.py                -- Entrypoint: starts scheduler + approval listener
  telegram_reader.py     -- Fetch new messages since last_message_id per channel
  stock_agent.py         -- Two-tier LLM pipeline (OpenRouter): detection + extraction
  risk_engine.py         -- Deterministic validation rules, position sizing
  approval_bot.py        -- Trade card formatting, send/receive approval via bot
  market_data.py         -- Live quote fetching (INDstocks API, yfinance fallback)
  db.py                  -- SQLite schema (5 tables), all queries
  brokers/
    base.py              -- Abstract BrokerInterface (Quote, Order, OrderResult, Position)
    indstocks.py         -- INDstocks REST API implementation
  get_channels.py        -- Utility script to list your Telegram channel/group IDs
  tests/                 -- pytest-asyncio test suite
  setup_guide.md         -- Step-by-step first-run instructions
  .env.example           -- Template for required environment variables
  Dockerfile             -- Python 3.12 slim image
  docker-compose.yml     -- Single-service deployment with DB persistence
```

## File Details

### stock_agent.py (LLM Pipeline)

Uses OpenRouter REST API directly (no SDK). Two-tier approach with free Nvidia models:

- **Tier 1** (Nemotron 3.5 Lightning): Binary classifier. "Is this a stock tip?" Returns `{is_tip, confidence}`. Messages with confidence below 0.6 are discarded.
- **Tier 2** (Nemotron 3 Super 120B): Structured extraction. Given the message plus the last 5 messages from the same channel for context, extracts: symbol, exchange, action, entry_min, entry_max, stop_loss, targets, allocation_pct, confidence, reasoning.

Both prompts enforce JSON-only responses. Markdown code fences are stripped if present.

### risk_engine.py (Validation)

All checks are deterministic (no LLM involvement):

1. **Symbol resolution** against INDstocks instrument list (exact match, uppercase, then fuzzy with 0.8 cutoff)
2. **Stop-loss default** of 15% below entry_max if the tip omits it
3. **Position sizing** using tip's allocation_pct or default 10% of wallet balance
4. **Balance check** to ensure sufficient funds
5. **Duplicate check** across pending/approved/executed signals for the same symbol from the same channel in the last 24 hours
6. **Market hours** enforcement (IST 9:15 AM to 3:30 PM, weekdays only)
7. **Signal age** rejection if older than MAX_SIGNAL_AGE_MINUTES (default 60)

Returns a `ValidationResult` dataclass with the enriched trade candidate (calculated quantity, amount, resolved symbol, current price).

### approval_bot.py (User Interaction)

Sends a formatted trade card showing: original message, extracted trade details (symbol, action, entry range, current price, stop-loss, targets, quantity, amount, allocation), risk/reward ratio, signal age, and LLM confidence.

Accepts replies: A/approve/yes/y to approve, R/reject/no/n to reject (case-insensitive). Anything else prompts re-entry.

On approval, fetches a live quote. If the price is outside [entry_min, entry_max], sends a re-approval card with recalculated numbers. If within range, proceeds to order placement.

### db.py (Database)

SQLite database (`agent.db`) with 5 tables:

| Table | Purpose |
|-------|---------|
| `messages` | Every fetched message (channel_id, message_id, text, timestamp). Deduplication via UNIQUE(channel_id, message_id). |
| `signals` | LLM-extracted trade signals linked to messages. Stores full extraction (symbol, entry, SL, targets as JSON, confidence). |
| `trade_candidates` | Validated signals pending approval. Status: pending/approved/rejected/executed. |
| `decisions` | Approval/rejection log with price at decision time. |
| `audit_log` | All broker API calls with request/response JSON for post-mortem. |

### brokers/base.py and brokers/indstocks.py

Abstract `BrokerInterface` with methods: `get_balance`, `get_quote`, `place_order`, `get_positions`, `get_instruments`.

`INDstocksBroker` implements this against `https://api.indstocks.com`:
- Auth via token in Authorization header
- Instruments fetched as CSV, cached in memory
- Orders placed as LIMIT/CNC/DAY by default
- Quotes fetched using scrip codes (`{exchange}_{security_id}`)

### market_data.py

`get_quote(symbol, exchange)` tries the broker API first, falls back to yfinance (`{symbol}.NS` for NSE, `{symbol}.BO` for BSE`). Returns a `Quote` dataclass (price, volume, day_high, day_low).

### get_channels.py

Standalone utility. Connects to Telegram using hardcoded API credentials and prints all group/channel names and IDs. Used once during setup to find channel IDs for `.env`.

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| telethon | 1.44.0 | Telegram client (user account for reading channels, bot for approvals) |
| APScheduler | 3.10.4 | 10-minute polling scheduler |
| httpx | 0.27.0 | Async HTTP client for OpenRouter and INDstocks APIs |
| yfinance | 0.2.40 | Market data fallback (Yahoo Finance) |
| aiosqlite | 0.20.0 | Async SQLite |
| python-dotenv | 1.0.1 | .env file loading |
| pytest | 8.3.2 | Test framework |
| pytest-asyncio | 0.23.8 | Async test support |

## Configuration

All configuration is via environment variables loaded from a `.env` file. Copy `.env.example` to `.env` and fill in:

### Required

| Variable | Description |
|----------|-------------|
| `TELEGRAM_API_ID` | Telegram app API ID (from https://my.telegram.org) |
| `TELEGRAM_API_HASH` | Telegram app API hash |
| `WATCHED_CHANNELS` | Comma-separated channel/group IDs to monitor (e.g. `-1001234567890,-1009876543210`) |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather (used for sending approval cards) |
| `APPROVAL_CHAT_ID` | Your chat ID with the approval bot |
| `OPENROUTER_API_KEY` | API key from https://openrouter.ai |
| `INDSTOCKS_TOKEN` | INDstocks API access token (expires every 24h) |

### Optional (with defaults)

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_SESSION_NAME` | `stock_agent` | Telethon session file name |
| `TIER1_MODEL` | `nvidia/nemotron-3.5-lightning:free` | LLM for tip detection |
| `TIER2_MODEL` | `nvidia/nemotron-3-super-120b-a12b:free` | LLM for trade extraction |
| `DEFAULT_STOP_LOSS_PCT` | `15` | Default stop-loss percentage if tip omits it |
| `DEFAULT_ALLOCATION_PCT` | `10` | Default portfolio allocation percentage per trade |
| `MAX_SIGNAL_AGE_MINUTES` | `60` | Reject signals older than this |
| `POLL_INTERVAL_MINUTES` | `10` | How often to check channels for new messages |

## How to Run

### First-time setup

```bash
cp .env.example .env
# Fill in all required values in .env

pip install -r requirements.txt

# First run creates the Telethon session file (interactive: asks for phone + verification code)
python main.py
```

### Find your channel IDs

```bash
python get_channels.py
# Prints all your Telegram groups/channels with their IDs
```

### Docker deployment

After the session file is created locally:

```bash
docker-compose up -d --build
docker-compose logs -f stock-agent
```

The `docker-compose.yml` mounts `agent.db` and `stock_agent.session` as volumes for persistence.

### Run tests

```bash
pytest
```

Tests use `asyncio_mode = auto` (configured in `pytest.ini`).

## Current State (as of 2026-08-29)

### Verified Working

- Telegram connection: Telethon user client authenticated, 4 private channels accessible
- Message fetching: 206 messages fetched and stored in SQLite
- LLM pipeline: minimax/minimax-m3:free model, 9 trade signals extracted from 84 messages
- Ticker resolution: all extracted symbols resolve against 18,736 INDstocks instruments
- Broker auth: TOTP auto-login implemented (pyotp), no manual daily token refresh needed
- Broker API: balance, instruments, quotes endpoints tested and working
- API cost tracking: every LLM call logged to DB with tokens, cost, model
- Trade P&L tracking: buy/sell recorded with charges, pnl, pnl_pct for reporting
- Approval card includes API cost summary
- Local terminal approval flow for testing (scripts/test_approval_local.py)

### Not Yet Tested End-to-End

- Telegram bot posting (approval cards)
- Telegram bot approval reading
- Order execution via broker API
- Success message posting on Telegram

### Not Implemented

- AMO (After Market Orders) support
- Partial position exits or trailing stop-loss
- Multiple broker support (only INDstocks)
- Rate limiting / exponential backoff on API calls
- Concurrent approval handling (approvals are processed FIFO from a single list)

### Test Scripts (in scripts/)

- `read_messages.py`: fetch messages from channels to MD file
- `save_messages_to_db.py`: fetch messages to SQLite
- `analyze_messages.py`: run LLM pipeline on unprocessed messages
- `test_approval_local.py`: review signals and approve/reject from terminal
- `test_ticker_search.py`: resolve symbols against broker instruments

## Cloud Deployment (Oracle Cloud Free Tier)

This project is designed to run on Oracle Cloud's **always-free tier** at **$0/month**.

### Infrastructure (Terraform)

All cloud resources are managed as code in the `infra/` directory.

| Resource | Free Tier Allocation | This Project Uses |
|----------|---------------------|-------------------|
| Compute (A1.Flex ARM) | 4 OCPU + 24 GB RAM | 1 OCPU + 6 GB RAM |
| Boot Volume | 200 GB | 50 GB |
| Network (VCN, subnet, gateway) | Unlimited | 1 VCN |
| Reserved Public IP | 1 | 1 (for broker API whitelisting) |
| Boot Volume Backups | 5 slots | Weekly (silver policy) |
| Outbound Bandwidth | 10 TB/month | Minimal |

### Deploy from scratch

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your OCI credentials and home IP

terraform init
terraform plan
terraform apply
```

### First run on the VM

```bash
# 1. SSH in (command printed by terraform output)
ssh -i ~/.ssh/oracle_cloud ubuntu@<INSTANCE_IP>

# 2. Create .env
sudo -u stockagent nano /opt/stock-agent/.env

# 3. Copy session files from your local machine
scp -i ~/.ssh/oracle_cloud stock_agent.session approval_bot.session ubuntu@<INSTANCE_IP>:/opt/stock-agent/

# 4. Start the service
sudo systemctl start stock-agent

# 5. Verify
sudo systemctl status stock-agent
docker compose -f /opt/stock-agent/docker-compose.yml logs -f
```

### Update deployment

```bash
ssh -i ~/.ssh/oracle_cloud ubuntu@<INSTANCE_IP>
sudo -u stockagent /opt/stock-agent/infra/scripts/deploy.sh
```

### Monitoring

A systemd timer runs every 5 minutes and checks:
- Container is running (auto-restarts on failure)
- CPU usage (alerts if > 80%)
- Memory usage (alerts if > 85%)
- Disk usage (alerts if > 80%)

All alerts are sent to Telegram via the existing bot.
