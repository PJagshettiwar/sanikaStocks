# sanikaStocks - Project Review Round 2

**Date:** 2026-08-30
**Reviewed by:** 5 AI personas (Developer, Tester, Product Owner, User, Architect)
**Baseline:** Builds on `docs/PROJECT_REVIEW.md` (48 findings, 20+ resolved). Only NEW findings listed below.
**Project goal:** Simple but effective personal stock trading agent via Telegram

---

## Summary

| Priority | Count |
|----------|-------|
| CRITICAL | 2 |
| HIGH | 5 |
| MEDIUM | 14 |
| LOW | 12 |
| **TOTAL** | **33** |

---

## CRITICAL Findings

### R2-C1. Hardcoded Telegram API credentials committed to git
**Personas:** Architect
**Module:** `get_channels.py`
**Lines:** 3-5

`api_id = 30261858` and `api_hash = "33b537f62e2dfea822f6f667269d51cd"` are hardcoded in plaintext. This file is tracked by git, so the credentials are in version history. Anyone with repo access gets full Telegram API identity.

**Proposed fix:** Delete the hardcoded values. Import from `config.py` (which reads env vars). Rotate the `api_hash` via my.telegram.org since the current value is in git history. Consider adding `get_channels.py` to `.gitignore` or deleting it (it's a one-off utility script).

**Owner observation:**
**Status:**
**Decision:**

---

### R2-C2. Reapproval loop: price-changed approval produces infinite reapproval cards
**Personas:** Product Owner, User
**Module:** `approval_bot.py`
**Lines:** 229-256

When the user approves a trade and the current price is outside `entry_min`/`entry_max`, a reapproval card is sent. But entry_min/entry_max in the DB are never updated. If the user approves the reapproval card, the same range check fires again with the same stale range, producing another reapproval card. The user is stuck in an infinite loop with no way to execute.

**Proposed fix:** When sending a reapproval card, update `entry_min` and `entry_max` in `trade_candidates` to reflect the current price (e.g., set to `quote.price * 0.99` and `quote.price * 1.01`). That way the next approval attempt uses the updated range.

**Owner observation:**
**Status:**
**Decision:**

---

## HIGH Findings

### R2-H1. Post-order DB writes are unprotected; order placed but no record saved
**Personas:** Developer
**Module:** `approval_bot.py`
**Lines:** 286-297

After `place_order` succeeds (line 279), the subsequent calls to `save_audit_log`, `save_trade`, and `update_candidate_status` (lines 286-297) are outside the try/except block. If any DB write fails (disk full, connection dropped), a real-money order exists at the broker but the local database has no trade record. The candidate stays "pending," and the user could approve it again, placing a duplicate order.

**Proposed fix:** Wrap lines 286-297 in try/except. On failure, log at CRITICAL level with order_id, send Telegram alert ("Order ORD123 placed but DB save failed. Do NOT re-approve."), and attempt to mark candidate as "executed" as a last resort.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-H2. No `exchange` field validation from LLM output
**Personas:** Architect
**Module:** `stock_agent.py`
**Lines:** 116-135

`extract_trade` validates `action`, `entry_min`, `entry_max`, and `confidence`, but never validates `exchange`. The LLM can return any string (e.g., "MCX", "EQUITY", ""). This value flows directly into `broker.get_quote(symbol, exchange)` and `broker.place_order()` where it's included in the API payload. A hallucinated exchange could route an order to the wrong market segment.

**Proposed fix:** Add after line 122:
```python
if result.get("exchange") not in ("NSE", "BSE"):
    log.warning("LLM returned invalid exchange: %s", result.get("exchange"))
    return None
```

**Owner observation:**
**Status:**
**Decision:**

---

### R2-H3. SELL trades never close the corresponding BUY trade record
**Personas:** Product Owner
**Module:** `approval_bot.py`
**Lines:** 291-296

When a SELL order executes, `save_trade()` creates a new row with `side='SELL'`. The existing `close_trade()` function in `db.py` (which computes P&L and marks a trade as 'closed') is never called anywhere. Every BUY trade stays `status='open'` forever. This means `get_portfolio_summary()` reports wrong invested amounts, and `get_symbol_pnl()` always returns 0 trades (it queries `status='closed'`). The P&L line in SELL confirmations (lines 304-314) is dead code.

**Proposed fix:** After a successful SELL execution, look up the matching open BUY trade for the same symbol and call `close_trade(db_conn, buy_trade_id, sell_price=quote.price, sell_order_id=result.order_id)`.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-H4. Balance check in validate_signal silently drops BUY opportunities
**Personas:** Product Owner
**Module:** `risk_engine.py`
**Lines:** 87-89

When a BUY signal arrives, `validate_signal` checks the broker balance immediately. If funds are temporarily low (pending settlement, planning to add funds), the signal is silently rejected with no trade card sent. The user never sees the opportunity. The approval flow already has its own balance check at execution time (`approval_bot.py:200-209`), making this early check redundant but harmful because it suppresses the card entirely.

**Proposed fix:** Remove the balance check from `validate_signal` for BUY signals. The trade card already shows wallet balance, and `handle_approval_reply` already guards against executing without funds.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-H5. No channel name shown on trade card
**Personas:** User
**Module:** `approval_bot.py`
**Lines:** 47-96

When watching multiple Telegram channels, every trade card looks identical in origin. The user sees the sanitized source text but not which channel it came from. During a busy morning with 3-4 signals, you can't tell if a tip came from a trusted analyst or a noisy channel, which directly affects the approve/reject decision.

**Proposed fix:** Pass `channel_id` (already available in the processing loop at `main.py:57`) through to `format_trade_card`. Resolve it to a channel name via Telethon and add a "Channel:" line to the card.

**Owner observation:**
**Status:**
**Decision:**

---

## MEDIUM Findings

### R2-M1. Daily trade limit resets at midnight UTC instead of midnight IST
**Personas:** Product Owner, Developer
**Module:** `db.py`
**Lines:** 343-350

`get_today_trade_count` uses `datetime('now', 'start of day')` which is UTC midnight (5:30 AM IST previous day). The counter resets at 5:30 AM IST instead of midnight IST. Between 18:30 IST and midnight, the user effectively gets a fresh daily limit. Unlikely to cause real problems with 5 trades/day, but misaligns with the user's mental model of "today."

**Proposed fix:** Compute IST start-of-day in SQLite: `datetime('now', '+5 hours', '+30 minutes', 'start of day', '-5 hours', '-30 minutes')`, or pass IST start-of-day from Python as a parameter.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-M2. Unvalidated `targets` field from LLM output
**Personas:** Architect, Tester
**Module:** `stock_agent.py`
**Lines:** 116-135

`extract_trade` validates `symbol`, `entry_min`, `entry_max`, `action`, and `confidence`, but never validates `targets`. If the LLM returns `"targets": null`, `"targets": "none"`, or a list of non-numeric values, the value flows unchecked into `sorted()` calls in `approval_bot.py` (line 48 and 234), which will crash. A very large array also bloats the DB and Telegram message.

**Proposed fix:** Add after line 134 in `extract_trade`:
```python
targets = result.get("targets", [])
if not isinstance(targets, list):
    result["targets"] = []
else:
    result["targets"] = [t for t in targets if isinstance(t, (int, float)) and 0 < t < 1_000_000]
```

**Owner observation:**
**Status:**
**Decision:**

---

### R2-M3. `/pending` resend creates duplicate cards with stale mappings
**Personas:** Product Owner
**Module:** `main.py`
**Lines:** 128-150

When `/pending` resends cards, `send_approval` adds new `msg_id -> candidate_id` entries, but never removes the old ones. The old cards still appear in chat, and if the user replies to an old card, it still routes to the same candidate. After several `/pending` calls, there could be 5+ cards for the same trade. The user doesn't know which card has the current price.

**Proposed fix:** Before resending, call `_remove_pending(row["id"])` for each candidate to clear old msg_id mappings. Then `send_approval` adds the fresh one.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-M4. `verify_order_fill` wrong for partial positions and pre-existing holdings
**Personas:** Product Owner, User
**Module:** `approval_bot.py`
**Lines:** 138-163

For BUY: checks `held.net_qty >= expected_qty`. If the user already holds 10 shares and buys 5 more, the check passes immediately (10 >= 5) even if the new order hasn't filled. Premature "Order FILLED" confirmation. For SELL: checks `net_qty == 0`. If partial sell (200 held, sold 100), position still exists at 100, so verification fails and sends a false "Order NOT confirmed" alarm.

**Proposed fix:** Capture `pre_order_qty` before placing the order. For BUY: check `held.net_qty >= pre_order_qty + expected_qty`. For SELL: check `held.net_qty <= pre_order_qty - expected_qty`.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-M5. Stale trade cards remain actionable after `/cancel`
**Personas:** User
**Module:** `main.py`
**Lines:** 200-205

`/cancel SYMBOL` updates the DB status and removes the in-memory mapping, but the original card message stays in chat. If the user scrolls back and replies "A" to a cancelled card, `get_candidate_for_msg` returns None and the reply is silently ignored with no feedback.

**Proposed fix:** After cancelling, edit or reply to the original card message with a "CANCELLED" label. The `telegram_msg_id` is in the DB; use `bot_client.edit_message` or send a reply to that message.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-M6. LIMIT order placed at exact quote price risks non-fill
**Personas:** User
**Module:** `approval_bot.py`
**Lines:** 273-279

The order is placed as LIMIT at `quote.price` (last traded price at quote time). By the time the order reaches the exchange, the price may have ticked up. The user sees "Order placed" but the order may never fill. After 10 minutes of verification attempts, they get "Order NOT confirmed, check broker manually."

**Proposed fix:** Add a small buffer to the limit price (e.g., `quote.price * 1.002` for BUY, `quote.price * 0.998` for SELL). Or make order type configurable via env var (`ORDER_TYPE=MARKET`).

**Owner observation:**
**Status:**
**Decision:**

---

### R2-M7. Test dependencies shipped in production Docker image
**Personas:** Architect
**Module:** `requirements.txt`
**Lines:** 8-9

`pytest==8.3.2` and `pytest-asyncio==0.23.8` are in the single `requirements.txt` used by the Dockerfile. These and their transitive dependencies are installed in the production container, expanding attack surface unnecessarily.

**Proposed fix:** Split into `requirements.txt` (runtime) and `requirements-dev.txt` (adds test deps). Dockerfile installs only `requirements.txt`.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-M8. Database file world-readable on Docker host via bind mount
**Personas:** Architect
**Module:** `docker-compose.yml`
**Lines:** 6-7

`agent.db` is bind-mounted from the host with default permissions (typically 0644). Any host user can `sqlite3 ./agent.db` and read all message texts, trade signals, order IDs, broker responses, and API cost data.

**Proposed fix:** Set `chmod 600 agent.db` on the host. Add a startup check or entrypoint that enforces this. Or use a named Docker volume instead of a bind mount.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-M9. `_call_openrouter` HTTP error (non-200) path untested
**Personas:** Tester
**Module:** `stock_agent.py`
**Lines:** 65-67

When OpenRouter returns 429 (rate limit) or 500, `resp.raise_for_status()` raises `httpx.HTTPStatusError`. No test verifies this propagation. In production it bubbles up through `poll_channels`, which does catch it, but unit-level behavior is unverified.

**Proposed fix:** Add a test that mocks a 429 response and asserts `httpx.HTTPStatusError` is raised from `detect_signal`.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-M10. `verify_order_fill` is completely untested
**Personas:** Tester
**Module:** `approval_bot.py`
**Lines:** 138-163

This function runs as a background task after every approved order, polling the broker up to 10 times. None of its three outcomes (BUY confirmed, SELL confirmed, NOT confirmed) are tested. A bug here means no fill notification or an incorrect one.

**Proposed fix:** Add 3 tests: (1) BUY confirmed on first check, (2) SELL confirmed (position gone), (3) all attempts fail and "NOT confirmed" message sent. Mock `asyncio.sleep` to avoid delays.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-M11. Double-approval race condition guard (lines 258-262) is untested
**Personas:** Tester
**Module:** `approval_bot.py`
**Lines:** 258-263

After price validation passes, there's a second `get_candidate_status` check to guard against two simultaneous approvals. This path is never tested. If it broke, two approvals could both reach `place_order`, placing duplicate orders with real money.

**Proposed fix:** Add a test where `get_pending_candidate` returns the candidate (first check passes) but `get_candidate_status` returns `"executed"` (race caught). Assert `place_order` is never called.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-M12. `get_instruments` CSV parsing has no test
**Personas:** Tester
**Module:** `brokers/indstocks.py`
**Lines:** 58-68

No test verifies the CSV parsing logic. If the broker API renames `TRADING_SYMBOL` to `trading_symbol`, the instrument map silently becomes empty and every symbol resolution fails.

**Proposed fix:** Add a test that mocks a CSV response with `TRADING_SYMBOL,SECURITY_ID` headers and asserts the returned dict has the expected entries. Also test caching (second call returns cached data without network request).

**Owner observation:**
**Status:**
**Decision:**

---

### R2-M13. `_cost_tracker` module-level state leaks between tests
**Personas:** Tester
**Module:** `stock_agent.py`
**Lines:** 40-52

`_cost_tracker` is module-level mutable state that accumulates across the entire test suite. If any test asserts on `get_session_costs()`, it gets non-deterministic results depending on test execution order.

**Proposed fix:** Add a fixture that resets `_cost_tracker` before each test, or expose a `reset_cost_tracker()` function.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-M14. Instrument cache never invalidates across multi-day bot runs
**Personas:** Product Owner
**Module:** `brokers/indstocks.py`
**Lines:** 58-68

`get_instruments()` caches the full instrument list on first call and never refreshes. If the bot runs for weeks, newly listed stocks won't resolve and the user sees "Unknown symbol" with no indication that a restart would fix it.

**Proposed fix:** Store a cache timestamp and refresh if older than 24 hours, or clear the cache daily via the scheduler.

**Owner observation:**
**Status:**
**Decision:**

---

## LOW Findings

### R2-L1. OpenRouter API response may not include `cost` field
**Personas:** Developer
**Module:** `stock_agent.py`
**Lines:** 69-73

`usage.get("cost", 0)` assumes OpenRouter includes `cost` in the `usage` object. OpenRouter includes cost in response headers or a separate endpoint, not reliably in `usage`. The `api_costs` table accumulates rows with `cost_usd = 0`, making cost tracking silently useless.

**Proposed fix:** Read cost from response headers if available: `cost = float(resp.headers.get("x-cost", 0))` as fallback.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-L2. `verify_order_fill` task is fire-and-forget with no crash protection
**Personas:** Developer
**Module:** `approval_bot.py`
**Lines:** 316-319

`asyncio.create_task(verify_order_fill(...))` launches a background task polling for 10 minutes. If an unhandled exception occurs (e.g., in `bot_client.send_message`), it's silently swallowed. If the bot restarts within the 10-minute window, verification is lost.

**Proposed fix:** Add try/except around the entire `verify_order_fill` body. For restart resilience, record pending verifications in the DB and re-check on startup.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-L3. Messages silently dropped when channel has >100 new messages per poll
**Personas:** Developer
**Module:** `telegram_reader.py`
**Lines:** 12

`client.iter_messages(channel_id, min_id=min_id, limit=100)` returns at most 100 messages in reverse chronological order. If >100 were posted since the last poll, the oldest are permanently skipped because the next poll's `min_id` advances past them.

**Proposed fix:** Use `reverse=True` to process oldest-first, so if interrupted, the next poll picks up where it left off. Or increase limit to 500.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-L4. Duplicate message filtering in `fetch_new_messages` untested
**Personas:** Tester
**Module:** `telegram_reader.py`
**Lines:** 16-30

When `save_message` returns `None` (duplicate), the message is silently dropped. Existing tests always return `db_id=1`, so the duplicate-skip path is unexercised. If `save_message` behavior changed to return 0 instead of None, the `if db_id:` check would still pass.

**Proposed fix:** Add a test where `save_message` returns `None` for one of two messages and assert only the non-duplicate appears in the result.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-L5. `market_data.get_quote` with `broker=None` path untested
**Personas:** Tester
**Module:** `market_data.py`
**Lines:** 9-15

`get_quote(symbol, exchange, broker=None)` with no broker goes to the yfinance fallback. Existing tests always pass a broker mock. The `broker=None` path is unexercised.

**Proposed fix:** Add a test calling `get_quote("RELIANCE", "NSE")` with no broker, using the existing yfinance stub.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-L6. `_sanitize_source` URL removal and truncation untested
**Personas:** Tester
**Module:** `approval_bot.py`
**Lines:** 40-44

No test verifies that URLs are removed or that truncation works. A failing sanitizer could leak channel-specific URLs into the approval chat.

**Proposed fix:** Test: `"Buy RELIANCE https://t.me/ch/123 above 1480"` should produce `"Buy RELIANCE [link removed] above 1480"`. Test truncation with 300-char input.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-L7. Broker token from env used without expiry tracking
**Personas:** Architect
**Module:** `config.py:20`, `brokers/indstocks.py:24-26`

`INDSTOCKS_TOKEN` from env is used directly without checking validity (beyond the 403-retry). A stale token causes cascading 403 retries during time-sensitive trade execution, delaying orders past the price window.

**Proposed fix:** Ignore `INDSTOCKS_TOKEN` from env. Always call `authenticate()` on startup to get a fresh token. Removes one secret from `.env`.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-L8. Audit log stores full broker response with no retention policy
**Personas:** Architect
**Module:** `approval_bot.py`
**Lines:** 286-290

`save_audit_log` serializes full request/response dicts. If the broker response ever includes tokens or PII, they're persisted in unencrypted SQLite. No retention policy, so data accumulates indefinitely.

**Proposed fix:** Allowlist which fields to log. Add retention: delete audit entries older than 90 days on startup.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-L9. No `/costs` command to see LLM spend
**Personas:** User
**Module:** `main.py`
**Lines:** 40-46

The system tracks every OpenRouter API call in the `api_costs` table, and `stock_agent.py` has `get_session_costs()`, but there's no Telegram command to surface this data. The user has no visibility into daily/monthly LLM costs without querying SQLite directly.

**Proposed fix:** Add `/costs` command that calls `get_total_api_cost` and `get_api_cost_summary` from `db.py` and formats a summary.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-L10. `/status` missing last successful poll timestamp
**Personas:** User
**Module:** `main.py`
**Lines:** 153-184

`/status` shows broker connectivity, LLM reachability, and pending count, but not when the last poll ran. If polling silently fails, the user has no way to know the bot stopped watching channels.

**Proposed fix:** Track `last_poll_at` and `last_poll_message_count` in a module-level variable updated at the end of `poll_channels`. Display both in `/status`.

**Owner observation:**
**Status:**
**Decision:**

---

### R2-L11. No feedback for unrecognized commands or non-reply messages
**Personas:** Product Owner, User
**Module:** `main.py`
**Lines:** 273-275

If the user sends a message that isn't a recognized command and isn't a reply to a trade card (e.g., typo "/statsu" or standalone "approve"), the bot silently ignores it. The user doesn't know if the bot is alive or if they mistyped.

**Proposed fix:** Add a fallback: for messages starting with "/" that don't match known commands, reply with `"Unknown command. Send /help for options."` For other non-reply messages, reply with `"Reply to a trade card with A/R, or send /help for commands."`

**Owner observation:**
**Status:**
**Decision:**

---

### R2-L12. `get_channels.py` utility script has no `.gitignore` protection
**Personas:** Architect
**Module:** `get_channels.py`

Beyond the credentials issue (R2-C1), this is a one-off utility script that creates a `stock_agent.session` Telegram session file in the project root. Session files are sensitive (they grant Telegram access). The script should either be excluded from git or documented as a setup-only tool.

**Proposed fix:** Add `get_channels.py` to `.gitignore` or move to `scripts/` and document in setup guide that it's a one-time utility.

**Owner observation:**
**Status:**
**Decision:**

---

## Positive Observations (New)

- Bot self-message filtering works correctly (line 256: skips bot's own messages)
- Reapproval card now includes targets, action, and source message (improvement from R1)
- Market hours enforcement correctly split: validation allows 24/7, execution blocks off-hours
- Morning notification at 9:15 IST for pending cards is a good UX pattern
- Graceful shutdown properly closes both httpx and aiosqlite connections
- URL sanitization applied to both initial and reapproval cards

---

## Cross-Reference with Round 1

Items from Round 1 still open (TODO/ENHANCEMENT/DISCUSSION) that are NOT re-listed above:
- C4: Stop-loss orders (broker API limitation)
- C5: Order fill verification loop
- H2: LLM prompt injection (needs discussion)
- H4: HOLD/partial profit booking (needs brainstorming)
- H5: SELL quantity from broker positions
- H8: Signal age at approval time
- H9: Exact match only for symbols
- H10: Aggregated P&L per stock
- H14: Broker retry backoff + LLM usage limits
- M5/M7: Sequential trade processing + idempotency
- M9: Session file security in Docker
- M11: /portfolio command
- M12: Daily morning report
- M13: Rate limiting
- L1-L10: Various low-priority fixes
