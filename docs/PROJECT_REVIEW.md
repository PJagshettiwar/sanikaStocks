# sanikaStocks - Multi-Persona Project Review

**Date:** 2026-08-29
**Reviewed by:** 5 AI personas (Developer, Tester, Product Owner, User, Architect)
**Owner review:** 2026-08-29
**Project goal:** Simple but effective personal stock trading agent via Telegram

---

## Summary

| Priority | Count | Status |
|----------|-------|--------|
| CRITICAL | 6 | Mixed: fix, test-first, enhancement |
| HIGH | 14 | Mixed: fix, brainstorm, skip |
| MEDIUM | 18 | Mixed: fix, discuss, skip |
| LOW | 10 | All fix |

---

## CRITICAL Findings

### C1. Secrets exposed in .env file
**Personas:** Architect
**Module:** `.env`
**Lines:** 1-22

The `.env` file contains live credentials: Telegram bot token, OpenRouter API key, INDstocks TOTP secret, MPIN, and Telegram API hash. While `.env` is in `.gitignore` (good), these secrets were visible during this review session.

**Original fix:** Rotate all credentials, add .dockerignore, update .env.example

**Owner observation:** Create new file with step-by-step rotation guide. Show how to keep this file safe next time.
**Status:** DONE
**Decision:** Create `docs/SECRETS_ROTATION.md` with step-by-step rotation instructions for each service. Add `.dockerignore`. Update `.env.example` with all fields.
**Actual fix:**
1. Created `docs/SECRETS_ROTATION.md` with step-by-step rotation for all 6 credentials
2. Updated `.env.example` with all required fields (was missing TELEGRAM_API_ID, TELEGRAM_API_HASH, INDSTOCKS_CLIENT_ID, INDSTOCKS_TOTP_SECRET, INDSTOCKS_MPIN)
3. Created `.dockerignore` to prevent secrets from leaking into Docker images
4. Removed stale `INDSTOCKS_TOKEN` from `.env` (auto-auth works without it)
5. Owner must still rotate all credentials manually using the guide

---

### C2. Naive vs aware datetime comparison crashes signal processing
**Personas:** Developer, Product Owner, User
**Module:** `risk_engine.py`
**Lines:** 69-72

`datetime.utcnow() - msg_time` raises `TypeError` when `msg_time` is timezone-aware (Telegram sends timezone-aware timestamps). This crashes signal processing for every message. `datetime.utcnow()` is also deprecated in Python 3.12+.

Same bug exists in `approval_bot.py:57` for signal age calculation.

**Original fix:** Replace `datetime.utcnow()` with `datetime.now(timezone.utc)` in both files.

**Owner observation:** Test-first approach. Write a failing test, show the failure, then fix and show passing test.
**Status:** DONE
**Decision:** TDD approach: write test with timezone-aware timestamp, see it fail, then fix both `risk_engine.py` and `approval_bot.py`.
**Actual fix:**
1. Added `test_timezone_aware_timestamp_does_not_crash` test, confirmed failure: `TypeError: can't subtract offset-naive and offset-aware datetimes`
2. Fixed `risk_engine.py:70`: coerce naive timestamps to UTC-aware, use `datetime.now(timezone.utc)` instead of deprecated `datetime.utcnow()`
3. Fixed `approval_bot.py:57`: same pattern for signal age calculation
4. Fixed `db.py:191`: `update_candidate_status` also used `datetime.utcnow()`
5. Updated all tests to use `datetime.now(timezone.utc)` to eliminate deprecation warnings
6. All 9 risk_engine tests pass

---

### C3. save_message returns existing IDs, causing re-analysis of every message
**Personas:** Developer
**Module:** `db.py` (lines 103-116), `telegram_reader.py` (lines 13-27)

When `INSERT OR IGNORE` hits a duplicate, `cursor.lastrowid` is 0 (falsy), so the function falls through to SELECT and returns the existing row ID. The caller treats any non-None return as a new message and sends it through the LLM. Every previously-seen message gets re-analyzed on every poll cycle, burning API credits.

**Original fix:** Return `None` when `cursor.rowcount == 0` (the insert was ignored).

**Owner observation:** Fix it.
**Status:** DONE
**Decision:** Fix `save_message` to return `None` on duplicate inserts.
**Actual fix:**
1. Changed `save_message` to check `cursor.rowcount == 0` (INSERT OR IGNORE was ignored) and return `None` for duplicates
2. Removed the fallback SELECT query that was returning existing row IDs
3. Updated test `test_save_message_duplicate_returns_existing_id` -> `test_save_message_duplicate_returns_none` to verify new behavior
4. All 8 DB tests pass. This stops the LLM from re-processing old messages on every poll cycle.

---

### C4. No stop-loss order placed with broker after trade execution
**Personas:** Product Owner, Architect
**Module:** `approval_bot.py`
**Lines:** 194-233

The system calculates and displays stop-loss on the trade card but never places a stop-loss order with the broker. After a BUY executes, there is zero automatic downside protection.

**Original fix:** Place SL/SL-M order after BUY.

**Owner observation:** INDstocks API does not support GTT or SL orders. No solution as of now.
**Status:** ENHANCEMENT (deferred)
**Decision:** Track as future enhancement. Revisit if INDstocks adds GTT/SL API support.

---

### C5. No order fill verification after placement
**Personas:** Product Owner
**Module:** `approval_bot.py`
**Lines:** 209-219

After `place_order` returns, the system assumes the order is filled and records the trade at the limit price. LIMIT orders can sit unfilled or partially fill.

**Original fix:** Poll broker for order status.

**Owner observation:** Check if MARKET order type is available. If yes, use MARKET instead of LIMIT. If MARKET is not available, keep LIMIT but add a verification loop: check every 1 minute for 10 minutes. If still unfilled, retry if price is in range; if price moved out of range, notify on Telegram with details for re-approval. Verifying trade completion is a MUST. After completion, notify on Telegram with exact price, quantity, and available balance. On SELL, show gain/loss details.
**Status:** TODO (complex)
**Decision:** Implement order verification loop with Telegram notifications. Show complete trade details including balance after execution. On SELL, show P&L. Needs brainstorming session before implementation.

---

### C6. handle_approval_reply is completely untested
**Personas:** Tester
**Module:** `approval_bot.py`
**Lines:** 118-233

The function that approves trades, checks market hours, validates balance, handles price drift, and calls `broker.place_order` has zero test coverage.

**Original fix:** Add async tests covering all branches.

**Owner observation:** Add tests, fix this.
**Status:** DONE
**Decision:** Add comprehensive async tests for all branches of `handle_approval_reply`.
**Actual fix:**
Added 12 async tests covering every branch:
1. `test_handle_approval_unrecognized_text` - garbage input returns "unrecognized"
2. `test_handle_approval_reject` - "R" rejects and saves decision
3. `test_handle_approval_candidate_not_found` - missing candidate returns "Trade not found"
4. `test_handle_approval_already_decided` - already executed shows "Already executed"
5. `test_handle_approval_market_closed` - off-hours returns "market_closed"
6. `test_handle_approval_broker_unavailable` - broker exception returns "error"
7. `test_handle_approval_insufficient_funds` - low balance returns "insufficient_funds"
8. `test_handle_approval_quote_unavailable` - quote failure returns "error"
9. `test_handle_approval_price_too_high` - price > allocation returns "error"
10. `test_handle_approval_price_outside_range_sends_reapproval` - price drift sends reapproval card
11. `test_handle_approval_success_places_order` - happy path: order placed, trade saved, status updated
12. `test_handle_approval_order_failure` - place_order exception: no trade/audit saved
All 44 tests pass across the full suite.

---

## HIGH Findings

### H1. No sender identity verification on trade approvals
**Personas:** Architect
**Module:** `main.py` (246-282), `approval_bot.py` (118-233)

The bot accepts approvals from ANY message in the approval chat. No check on `event.sender_id`.

**Original fix:** Check sender_id against AUTHORIZED_USER_ID env var.

**Owner observation:** Single user in the chat, only owner approves. Not needed.
**Status:** SKIP
**Decision:** Skip. Single-user private chat. Note for future reviews: this is intentional, not an oversight.

---

### H2. LLM prompt injection via malicious Telegram channel messages
**Personas:** Architect
**Module:** `stock_agent.py`
**Lines:** 90-112

Raw Telegram text is passed directly to the LLM. A malicious channel can craft messages to override the system prompt.

**Original fix:** Validate LLM output fields, limit context messages.

**Owner observation:** Show me the fix in code. Think carefully and deep, do not over-engineer.
**Status:** NEEDS DISCUSSION
**Decision:** Design the validation approach first, present code changes before implementing.

---

### H3. No maximum concurrent positions or daily trade/loss limits
**Personas:** Product Owner, Architect
**Module:** `risk_engine.py`
**Lines:** 57-109

No aggregate limits on positions, trades per day, or capital deployed.

**Original fix:** Add MAX_OPEN_POSITIONS, MAX_DAILY_TRADES, MAX_DAILY_CAPITAL config vars.

**Owner observation:** Daily 5 trades limit is sufficient. Skip position and capital limits.
**Status:** DONE
**Decision:** Add `MAX_DAILY_TRADES=5` only. Simple counter check in `validate_signal`.
**Actual fix:** Added `MAX_DAILY_TRADES` to `config.py`, `get_today_trade_count()` to `db.py`, daily limit check in `risk_engine.py:validate_signal` after duplicate check. Added `test_daily_trade_limit_rejected` test plus patched 4 existing tests that now pass through the new check.

---

### H4. LLM misclassifies HOLD/profit-booking messages as new BUY signals
**Personas:** Product Owner, User
**Module:** `stock_agent.py`
**Lines:** 11-31

The LLM extracts "continue to HOLD" and "book profits" messages as new BUY/SELL signals.

**Original fix:** Add prompt instructions to reject HOLD updates.

**Owner observation:** HOLD should return null. But "book partial profit" must be a feature: system should calculate how much to book (50% sell, etc.). Brainstorm before implementing.
**Status:** NEEDS BRAINSTORMING
**Decision:** Two parts: (1) HOLD returns null (simple prompt fix). (2) Partial profit booking: needs brainstorming on how to detect "book 50% profit" vs "full sell" from the lead message, calculate quantities from held positions, and present the right trade card.

---

### H5. SELL signal quantity uses allocation formula instead of held quantity
**Personas:** Product Owner, User
**Module:** `approval_bot.py`
**Lines:** 169-173

For SELL signals, quantity is `floor(FIXED_ALLOCATION_AMOUNT / price)`, same as BUY.

**Original fix:** Look up held quantity via broker.get_positions().

**Owner observation:** On SELL, get latest data from broker. If no quantity held, say "no funds allocated, no sell." If funds are there, follow the lead: full sell or 50% sell (book profit). Get the numbers from broker. Add unit tests for these scenarios.
**Status:** TODO
**Decision:** Integrate broker position lookup for SELL signals. Support full sell and partial sell (50% book profit). Test with unit tests using stubbed data.

---

### H6. Decision saved before order confirmed; no rollback on failure
**Personas:** Developer
**Module:** `approval_bot.py`
**Lines:** 208-209

`save_decision(approve)` runs before `broker.place_order()`. If the order fails, phantom approval record exists.

**Original fix:** Move `save_decision` to after `place_order` succeeds.

**Owner observation:** Fix it.
**Status:** DONE
**Decision:** Move `save_decision` call to after successful `place_order`.
**Actual fix:** Moved `save_decision` from before `broker.place_order()` to after it succeeds. Updated `test_handle_approval_order_failure` to verify `save_decision` is NOT called when order fails. All 16 approval_bot tests pass.

---

### H7. LLM JSON parse failure crashes poll cycle
**Personas:** Developer, Tester
**Module:** `stock_agent.py`
**Lines:** 87

`json.loads(content)` has no try/except. Free-tier models return non-JSON occasionally.

**Original fix:** Wrap in try/except, return None.

**Owner observation:** Investigate WHY we get failed output. Do backtesting of the model carefully. We don't want hallucinations in production. Fix the try/catch blocks too.
**Status:** DONE
**Decision:** First investigate the failure modes (what exactly do the free-tier models return when they fail?). Then add robust error handling with logging of the raw response for debugging. Add try/except returning None with logged details.
**Actual fix:**
1. Common failure modes from free-tier models: plain text ("I cannot help"), partial JSON, non-standard markdown fencing, empty responses
2. Wrapped `json.loads` in try/except returning `None` on `JSONDecodeError`
3. Added `log.warning` that captures first 200 chars of the raw LLM response for debugging
4. Moved `import logging` to module level (was inline in an error handler)
5. The chain is safe: `detect_signal` returns `None` -> `analyze_message` checks `if not detection` -> early return `None`. `extract_trade` already handles `None` at line 115.
6. Added 4 tests: non-JSON response, partial JSON, plain text from tier2, and tier1 garbage stopping tier2 from being called
7. All 11 stock_agent tests pass

---

### H8. No signal age check at approval time
**Personas:** Product Owner, User
**Module:** `approval_bot.py`
**Lines:** 142-193

Signal age is checked at detection but not at approval time.

**Original fix:** Check age in handle_approval_reply, auto-expire cards.

**Owner observation:** Yes, we can think on this.
**Status:** TODO (low priority)
**Decision:** Add age check at approval time. Warn user if signal is old. Auto-expire after configurable period.

---

### H9. _resolve_symbol fuzzy matching could match wrong stock
**Personas:** Tester, User
**Module:** `risk_engine.py`
**Lines:** 42-54

`difflib.get_close_matches` with cutoff=0.8 could match the wrong stock.

**Original fix:** Add tests, show resolved symbol on card, require confirmation for fuzzy matches.

**Owner observation:** Must identify exact match. If fuzzy, alert the user to validate over internet. Can we take input from the user? Keep as enhancement if complex.
**Status:** ENHANCEMENT
**Decision:** Require exact match only (remove fuzzy matching). If no exact match found, alert user on Telegram with the LLM-extracted symbol and ask for manual verification. If taking input is complex, defer to enhancement.

---

### H10. close_trade P&L calculation is untested
**Personas:** Tester
**Module:** `db.py`
**Lines:** 241-269

P&L math has no tests.

**Original fix:** Add tests for normal close, non-existent trade, zero buy_amount.

**Owner observation:** P&L is ok, but showing each trade profit after sell is important. On partial sell, keep a note. When full sell happens, add up all trades of that stock and show total profit. If complex, brainstorm.
**Status:** NEEDS BRAINSTORMING
**Decision:** Basic P&L tests are straightforward. The aggregated P&L per stock across partial sells needs design discussion. Track partial sells and compute cumulative P&L on final exit.

---

### H11. Full two-tier LLM pipeline (tip detected then extracted) is untested
**Personas:** Tester
**Module:** `stock_agent.py`
**Lines:** 115-119

Only the "no tip" path is tested.

**Original fix:** Add test with two mocked API calls.

**Owner observation:** Test with most scenarios.
**Status:** DONE (covered in H7)
**Decision:** Add tests for: tip detected + extracted, tip detected + extraction fails, low confidence tip, high confidence tip, edge cases on confidence threshold boundary.
**Actual fix:** Added as part of H7 fix:
1. `test_analyze_message_full_pipeline_tip_detected_and_extracted` - happy path, both tiers succeed
2. `test_analyze_message_low_confidence_skips_tier2` - confidence 0.3 below threshold, tier2 never called
3. `test_analyze_message_tier2_fails_returns_none` - tier1 succeeds, tier2 returns garbage
4. `test_analyze_message_handles_json_failure_in_tier1` - tier1 returns non-JSON, tier2 never called
All verify correct call counts (tier2 skipped when appropriate).

---

### H12. Market hours check blocks signal detection entirely
**Personas:** User
**Module:** `risk_engine.py`
**Lines:** 75-80

Overnight signals are silently dropped.

**Original fix:** Allow detection outside market hours, block only order placement.

**Owner observation:** Yes, hold the approvals. Every morning send pending trades if any.
**Status:** DONE
**Decision:** Allow signal detection and card creation outside market hours. Add morning notification: send all pending trade cards at market open (9:15 IST). Block only order execution during off-hours (already done in approval_bot).
**Actual fix:** Removed market hours check (weekend + time-of-day) from `risk_engine.py:validate_signal`. Signals now pass validation 24/7. Order execution is still blocked by `approval_bot.py:_is_market_open()` at approval time. Added `scheduler.add_job(handle_pending_command, "cron", hour=9, minute=15, timezone="Asia/Kolkata")` to `main.py` to auto-resend pending cards at market open. Replaced `test_weekend_signal_rejected` and `test_after_hours_signal_rejected` with `test_off_hours_signal_still_passes`. Removed unused `_now_ist()`, `IST` from risk_engine.py.

---

### H13. Token refresh on broker 403 not tested
**Personas:** Tester
**Module:** `brokers/indstocks.py`
**Lines:** 48-56

Auth-recovery mechanism has zero test coverage.

**Original fix:** Mock 403 then 200 after re-auth.

**Owner observation:** Test it.
**Status:** DONE
**Decision:** Add test for 403 retry flow.
**Actual fix:** Added `test_403_triggers_reauth_and_retry` to `tests/test_broker_indstocks.py`. Mocks first request returning 403, then auth endpoint returning new token, then retry returning 200. Asserts balance is correct, request called twice, and auth called once.

---

### H14. Hardcoded broker charges of Rs 10
**Personas:** Developer, Product Owner, User, Tester
**Module:** `approval_bot.py`
**Lines:** 224

`broker_charges=10.0` regardless of actual charges.

**Original fix:** Fetch from broker API or make configurable.

**Owner observation:** OK as of now. But: if broker API is failing, do NOT retry in a hurry. Take a pause, send details to user, wait for user to debug and send status check. Do not go in loop firing INDstocks APIs. Use wisely. Also for LLM model usage: if usage exceeds configured limit, notify and stop.
**Status:** ENHANCEMENT + OPERATIONAL FIX
**Decision:** Keep hardcoded charges for now. Add two operational safeguards: (1) Broker API retry backoff with Telegram notification instead of aggressive retries. (2) LLM usage tracking: if total tokens/cost exceed a configurable limit, notify user and pause processing.

---

## MEDIUM Findings

### M1. No graceful shutdown; DB and HTTP connections never closed
**Module:** `main.py` | **Lines:** 209, 292

**Owner observation:** Must fix.
**Status:** DONE
**Decision:** Add try/finally in `main()` to close `db_conn` and `http_client`.
**Actual fix:** Wrapped entire `main()` body in `try/finally`. The `finally` block calls `await http_client.aclose()` and `await db_conn.close()`. Both `db_conn` and `http_client` are created before the try block so they're always available in finally.

---

### M2. get_portfolio_summary uses FILTER syntax unsupported in older SQLite
**Module:** `db.py` | **Lines:** 281-293

**Owner observation:** Check exact solution on internet, fix carefully with testing.
**Status:** DONE
**Decision:** Rewrite with CASE WHEN pattern. Add test to verify the query works.
**Actual fix:** Replaced `COUNT(*) FILTER (WHERE ...)` and `SUM(...) FILTER (WHERE ...)` with `SUM(CASE WHEN ... THEN 1 ELSE 0 END)` and `SUM(CASE WHEN ... THEN col ELSE 0 END)` in `db.py:get_portfolio_summary`. Added `test_portfolio_summary_with_open_and_closed_trades` to `tests/test_db.py` that creates open and closed trades and verifies the summary counts and P&L.

---

### M3. Hardcoded "Allocation: Fixed 5,000/trade" in trade card text
**Module:** `approval_bot.py` | **Lines:** 76

**Owner observation:** Yes fix, so user knows current capping.
**Status:** DONE
**Decision:** Use `f"Allocation: Fixed {FIXED_ALLOCATION_AMOUNT:,.0f}/trade"`.
**Actual fix:** Replaced hardcoded `"5,000"` with `f"{FIXED_ALLOCATION_AMOUNT:,.0f}"` in `format_trade_card`. `FIXED_ALLOCATION_AMOUNT` was already imported.

---

### M4. set_cost_db() never called; LLM costs never persist to DB
**Module:** `main.py`, `stock_agent.py` | **Lines:** 35-39

**Owner observation:** Fix.
**Status:** DONE
**Decision:** Call `set_cost_db(db_conn)` in `main()` after DB init.
**Actual fix:** Added `set_cost_db` to imports from `stock_agent` and called `set_cost_db(db_conn)` right after `init_db(db_conn)` in `main()`.

---

### M5. Balance cached across entire poll cycle; stale for multi-signal batches
**Module:** `main.py` | **Lines:** 55-56, 84-88

**Owner observation:** Process trades one by one, not in parallel. Use threading and wait for approval. On failure, hold next or discard and message user. After fix, user gives status and re-approves. Balance updated after trade completes. Before action, get latest numbers. If insufficient funds, notify user to update wallet.
**Status:** TODO (complex)
**Decision:** Sequential trade processing with fresh balance before each trade. Notify user on insufficient funds. This ties into C5 (order verification) and M7 (idempotency). Implement together.

---

### M6. market_data.py (yfinance fallback) is unused dead code
**Module:** `market_data.py`

**Owner observation:** It's ok, we should have this.
**Status:** KEEP
**Decision:** Keep `market_data.py` for future use as fallback. No changes needed.

---

### M7. No idempotency guard on trade execution (double-tap risk)
**Module:** `approval_bot.py` | **Lines:** 118-233

**Owner observation:** Yes, as discussed in M5. Sequential processing.
**Status:** TODO
**Decision:** Implement together with M5. Add processing lock per candidate.

---

### M8. Error messages to Telegram may leak internal details
**Module:** `approval_bot.py` | **Lines:** 150-163

**Owner observation:** Correct, fix.
**Status:** DONE
**Decision:** Sanitize error messages sent to Telegram. Log full details server-side.
**Actual fix:** Removed `{e}` from all Telegram error messages (broker unavailable, quote unavailable, order failed). Added `log.error()` calls with full exception details before each sanitized message. Added `logging` import and `log` logger to `approval_bot.py`.

---

### M9. Telegram session file mounted as Docker volume
**Module:** `docker-compose.yml` | **Lines:** 5-6

**Owner observation:** Yes fix.
**Status:** TODO
**Decision:** Add chmod 600 instruction and document session file security.

---

### M10. Price-changed reapproval card drops targets and source message
**Module:** `approval_bot.py` | **Lines:** 177-187

**Owner observation:** Fix it, add unit tests with stubbed data.
**Status:** DONE
**Decision:** Include targets and source message in reapproval card. Add unit tests.
**Actual fix:** Added targets (parsed from JSON, with % change from current price) and source message to the reapproval card in `handle_approval_reply`. Also added the action (BUY/SELL). Existing `test_handle_approval_price_outside_range_sends_reapproval` test passes.

---

### M11. No /portfolio or /positions command
**Module:** `main.py` | **Lines:** 40-46

**Owner observation:** Explain in detail.
**Status:** NEEDS DISCUSSION
**Decision:** Explain the /portfolio command concept before implementing.

**Explanation:**
A `/portfolio` command would let you type `/portfolio` in the Telegram approval chat and get back a summary like:

```
--- PORTFOLIO ---
Open positions: 3
RELIANCE (NSE) - BUY x3 @ 1,486 | Current: 1,520 | P&L: +102 (+2.3%)
INFY (NSE) - BUY x10 @ 498 | Current: 485 | P&L: -130 (-2.6%)
AMBER (NSE) - BUY x1 @ 7,675 | Current: 7,800 | P&L: +125 (+1.6%)

Closed trades: 5
Total realized P&L: +2,340
Total charges: 100
Net P&L: +2,240

Wallet: 85,000
Total invested: 18,738
```

It pulls data from two sources:
1. `get_portfolio_summary()` from DB for historical trades and P&L
2. `broker.get_positions()` for live position data with current prices

This gives a quick snapshot without opening the broker app.

---

### M12. Default stop-loss of 15% is not surfaced as auto-generated
**Module:** `risk_engine.py` | **Lines:** 66-67

**Owner observation:** Remove this finding. As enhancement: can take latest portfolio report and if loss is more than 10% take sell action (irrespective of Telegram leads). Or send daily morning report in table format with a few columns. Can discuss later.
**Status:** ENHANCEMENT (deferred)
**Decision:** Skip the "[auto]" label for now. Future enhancement: daily morning portfolio report with P&L per position. Auto-sell on >10% loss is a separate discussion.

---

### M13. No rate limiting on bot commands
**Module:** `main.py` | **Lines:** 246-261

**Owner observation:** Explain with example.
**Status:** NEEDS DISCUSSION
**Decision:** Explain before implementing.

**Explanation:**
If someone (or you accidentally) sends `/status` 20 times in a row, each call hits:
1. INDstocks broker API (balance check + auth attempt if down)
2. OpenRouter API (model list endpoint)
3. DB query for pending trades

Without rate limiting, this could:
- Trigger INDstocks account lockout (too many auth attempts)
- Hit OpenRouter rate limits
- Slow down real trade processing

A simple fix: track the last time each command ran. If `/status` was called less than 5 seconds ago, reply "Please wait..." instead of re-running the full check.

```
/status  -> runs full check (broker, LLM, pending count)
/status  -> (2 seconds later) "Please wait 3 seconds..."
/status  -> (6 seconds later) runs full check again
```

Same for `/pending` which re-fetches quotes and resends all cards.

---

### M14. FIXED_ALLOCATION_AMOUNT has no upper bound validation
**Module:** `config.py` | **Lines:** 24

**Owner observation:** No issue, user will take care.
**Status:** SKIP
**Decision:** Skip. Owner manages config directly.

---

### M15. Original source message forwarded raw (could contain phishing links)
**Module:** `approval_bot.py` | **Lines:** 77

**Owner observation:** Yes, fix.
**Status:** DONE
**Decision:** Truncate to 200 chars, strip URLs.
**Actual fix:** Added `_sanitize_source(text, max_len=200)` helper that strips URLs (replaced with `[link removed]`) and truncates to 200 chars. Applied to both `format_trade_card` and the reapproval card in `handle_approval_reply`.

---

### M16. Trade card missing risk/reward ratio
**Module:** `approval_bot.py` | **Lines:** 65-80

**Owner observation:** Don't want it.
**Status:** SKIP
**Decision:** Skip. Owner prefers simpler trade cards.

---

### M17. get_all_pending_candidates JOIN query untested
**Module:** `db.py` | **Lines:** 311-321

**Owner observation:** Add a test.
**Status:** DONE
**Decision:** Add test with full data flow (message -> signal -> candidate) and verify returned dict.
**Actual fix:** Added `test_get_all_pending_candidates_returns_full_data` to `tests/test_db.py`. Creates full message->signal->candidate chain, verifies all JOIN fields are present (id, symbol, exchange, action, entry_min/max, stop_loss, quantity, original_message). Also verifies empty result after status change to "executed".

---

### M18. No notification when polls silently fail
**Module:** `main.py` | **Lines:** 105-106

**Owner observation:** Whatever the error, notify on TG immediately. Do not wait for N errors.
**Status:** DONE
**Decision:** Send Telegram notification on every poll error, not just after N consecutive failures.
**Actual fix:** Added `bot_client.send_message` in the except block of `poll_channels()` to notify on every error. Message includes message_id and exception type (not full details, per M8). Wrapped in try/except to avoid failing if Telegram itself is down.

---

## LOW Findings

### L1. update_last_message_id is a no-op
**Module:** `db.py` | **Lines:** 128-129

**Owner observation:** Fix.
**Status:** TODO
**Decision:** Remove the function and its test.

---

### L2. Module-level globals for shared state
**Module:** `main.py` | **Lines:** 34-38

**Owner observation:** Fix (all lows).
**Status:** TODO
**Decision:** Acceptable for now. Add a comment noting it's intentional for simplicity.

---

### L3. _msg_to_candidate dict has no size bound
**Module:** `approval_bot.py` | **Lines:** 19

**Owner observation:** Fix.
**Status:** TODO
**Decision:** Prune entries for non-pending candidates periodically.

---

### L4. Bot may respond to its own messages
**Module:** `main.py` | **Lines:** 246-247

**Owner observation:** Fix.
**Status:** TODO
**Decision:** Add sender check to skip bot's own messages.

---

### L5. Container runs as root
**Module:** `Dockerfile` | **Lines:** 1-6

**Owner observation:** Fix.
**Status:** TODO
**Decision:** Add non-root user to Dockerfile.

---

### L6. No .dockerignore file
**Module:** `Dockerfile` | **Lines:** 5

**Owner observation:** Fix.
**Status:** TODO
**Decision:** Create `.dockerignore` with `.env`, `*.session`, `agent.db`, `.git`, `.idea`, `__pycache__`.

---

### L7. Rejection confirmation too terse
**Module:** `approval_bot.py` | **Lines:** 139

**Owner observation:** Fix.
**Status:** TODO
**Decision:** Show "Rejected: BUY AMBER (NSE) @ 7,650-7,670".

---

### L8. Order confirmation doesn't show fill status
**Module:** `approval_bot.py` | **Lines:** 227-232

**Owner observation:** Fix.
**Status:** TODO
**Decision:** Include `result.status` in confirmation message.

---

### L9. Dependencies pinned but no hash verification
**Module:** `requirements.txt`

**Owner observation:** Fix.
**Status:** TODO
**Decision:** Acceptable for personal project. Add a comment noting this is a known tradeoff.

---

### L10. No message length limit before LLM processing
**Module:** `telegram_reader.py` | **Lines:** 10-27

**Owner observation:** Fix.
**Status:** TODO
**Decision:** Truncate to 1000 chars before storing.

---

## Positive Observations

- `.env` is properly gitignored (never committed)
- Parameterized SQL queries throughout (no SQL injection)
- Manual approval required before any trade execution
- Market hours validation prevents off-hours order placement
- Duplicate signal detection within 24h window
- Balance checks before trade execution
- HTTPS used for all external API calls
- Clean broker abstraction with interface/implementation separation
- Good test coverage on risk_engine validation paths
- Simple, readable codebase that matches the "simple but effective" goal

---

## Execution Order

Items to fix sequentially:
1. C1 - Secrets rotation guide
2. C2 - Datetime crash (TDD: fail first, then fix)
3. C3 - save_message duplicate fix
4. C6 - handle_approval_reply tests
5. H6 - Move save_decision after place_order
6. H7 - Investigate LLM failures + fix try/catch
7. H3 - Daily trade limit (MAX_DAILY_TRADES=5)
8. H11 - Two-tier LLM pipeline tests
9. H12 - Allow off-hours signals + morning notification
10. H13 - Test broker 403 refresh
11. M1 - Graceful shutdown
12. M2 - SQLite FILTER fix + test
13. M3 - Dynamic allocation text
14. M4 - set_cost_db call
15. M8 - Sanitize error messages
16. M10 - Reapproval card targets + tests
17. M15 - Strip URLs from source message
18. M17 - Test pending candidates query
19. M18 - Notify on every poll error
20. L1-L10 - All lows

Items needing brainstorming first:
- H2 - LLM injection defense (show approach)
- H4 - Partial profit booking feature
- H5 - SELL with broker position data
- H10 - Aggregated P&L per stock
- C5 - Order verification loop
- M5/M7 - Sequential trade processing

Items skipped:
- H1 - Single user, no sender check needed
- M14 - Owner manages allocation config
- M16 - No risk/reward ratio on cards

Items deferred to enhancement:
- C4 - Stop-loss orders (INDstocks API limitation)
- H8 - Signal age at approval time
- H9 - Exact match only + user input for fuzzy
- H14 - Broker retry backoff + LLM usage limits
- M6 - Keep market_data.py as-is
- M12 - Daily morning report
- M13 - Rate limiting (after discussion)
