# sanikaStocks

Automated stock trading agent for Indian markets (NSE/BSE). Monitors Telegram channels for trade tips, uses a 2-tier LLM pipeline (OpenRouter) to detect and extract signals, validates through a risk engine, sends approval cards via Telegram bot, and executes trades through INDstocks broker. Runs as a long-lived async Python process deployed via Docker on a Terraform-provisioned VM.

## Runbooks <!-- verified: 2026-09 -->

Read these before touching the server. They carry the traps that cost whole
sessions to rediscover.

- [docs/DEPLOY.md](docs/DEPLOY.md) — deploying to the VM, running Terraform,
  fixing the health watchdog. Disconnect the VPN first; never run
  `docker compose` as root.
- [docs/LOGS.md](docs/LOGS.md) — reading container logs from the laptop with
  `infra/scripts/logs.sh`, and what to check when they come back empty.

## Commands <!-- verified: 2026-08 -->

```bash
# Run tests (use venv python — system python lacks dependencies)
.venv/bin/python -m pytest

# Run a single test
.venv/bin/python -m pytest tests/test_risk_engine.py::test_function_name

# Run tests matching a keyword
.venv/bin/python -m pytest -k "keyword"

# Lint — no linter configured

# Run the app locally (requires .env with all secrets)
.venv/bin/python main.py

# Docker
docker compose up --build
```

## Architecture <!-- verified: 2026-08 -->

```
main.py              — entrypoint: scheduler, Telegram bot listener, poll loop
config.py            — env var loading (.env via python-dotenv)
telegram_reader.py   — polls Telegram channels for new messages (Telethon user client)
stock_agent.py       — 2-tier LLM pipeline: tier1 detects tips, tier2 extracts trade signals
risk_engine.py       — validates signals: symbol resolution, dedup, daily limits, price/age checks
approval_bot.py      — formats trade cards, sends to Telegram bot, handles approve/reject replies
market_data.py       — market data utilities (yfinance)
db.py                — SQLite via aiosqlite: schema init, all queries
brokers/base.py      — BrokerInterface ABC + dataclasses (Quote, Order, Position, OrderResult)
brokers/indstocks.py — INDstocks broker implementation (auth, quotes, orders, positions)
tests/               — pytest + pytest-asyncio, 116 tests across 9 files
scripts/             — standalone utilities (session setup, message analysis, local testing)
infra/               — Terraform (OCI compute) + cloud-init deployment scripts
```

**Data flow:** Telegram channels → `telegram_reader` → `stock_agent` (tier1 filter → tier2 extract) → `risk_engine` (validate) → `approval_bot` (human approval via Telegram) → `brokers/indstocks` (execute trade) → `db` (record everything).

**Key patterns:**
- All I/O is async (asyncio + aiosqlite + httpx + Telethon)
- Global module-level clients initialized in `main()`, passed as arguments
- SQLite database at `data/agent.db` with schema managed by `db.init_db()`
- APScheduler for periodic polling and morning notification cron
- Config is flat env vars loaded once at import time (`config.py`)

## Reuse Map <!-- verified: 2026-08 -->

- `BrokerInterface` → `brokers/base.py` — ABC for broker implementations (get_balance, get_quote, place_order, get_positions, get_instruments)
- `ValidationResult` → `risk_engine.py` — dataclass carrying validated signal data through the pipeline
- `_call_openrouter` → `stock_agent.py` — shared LLM call with cost tracking and JSON parsing
- `format_trade_card` → `approval_bot.py` — renders trade approval cards for Telegram
- `db.init_db` → `db.py` — schema creation/migration (idempotent)

## Do NOT <!-- verified: 2026-08 -->

- `data/` — runtime directory (SQLite database + Telegram session files, gitignored, created at runtime)
- `infra/terraform.tfstate` — Terraform state (gitignored)
- `infra/terraform.tfvars` — Terraform secrets (gitignored)
- `.env` — production secrets (gitignored)
- `infra/cloud-init/` — server provisioning scripts (modify carefully, deployed to production)

## Review Config <!-- verified: 2026-08 -->

- Ignore: `infra/.terraform/**`, `data/**`, `__pycache__/**`, `.venv/**`
- Max findings per review: 10
- Nits: summary block only, never inline PR comments
