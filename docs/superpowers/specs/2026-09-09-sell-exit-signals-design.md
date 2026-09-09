# Sell/Exit Signal Handling

Detect "exit" and "sell" messages from Telegram channels, extract partial sell percentages, validate against broker positions, show P&L on the approval card, and execute on approve.

## Current State

The pipeline already supports SELL as an action through tier2 extraction, risk engine validation, approval bot order placement, and trade closing with P&L. But tier1 rejects exit messages because its prompt requires entry price/SL/target, and there's no concept of partial sells.

## Changes

### 1. Tier1 Prompt (stock_agent.py:11-15)

Remove "book profits" from the NOT-a-tip list. Add exit/sell/book-profits with a stock name as valid tips.

Before:
> Messages saying "hold", "continue to hold", "book profits", "book partial profits", or "trail SL" are NOT new tips.

After:
> Messages saying "hold", "continue to hold", or "trail SL" are NOT new tips.
> Messages saying "exit", "sell", "book profits", or "book partial profits" with a stock name ARE tips. Return is_tip: true.

### 2. Tier2 Prompt (stock_agent.py:17-37)

Add `sell_pct` field to the extraction schema:

```json
{
  "symbol": "CAPLINPOINT",
  "exchange": "NSE",
  "action": "SELL",
  "sell_pct": 50,
  "entry_min": 0,
  "entry_max": 0,
  "stop_loss": null,
  "targets": [],
  "confidence": 0.9,
  "reasoning": "Exit 50% of Caplin Point position"
}
```

Rules added to prompt:
- `sell_pct`: 1-100, default 100 if not specified. Only used when action is SELL.
- For SELL: `entry_min` and `entry_max` should be 0, `stop_loss` null, `targets` empty

### 3. Tier2 Validation (stock_agent.py `extract_trade`, lines 156-174)

For SELL actions, skip entry price validation. Specifically:

- Line 158: `entry_min is None` check. For SELL, skip this (entry_min will be 0).
- Lines 166-171: `entry_min <= 0` and `entry_max <= 0` checks. For SELL, skip both.
- Lines 172-173: `entry_min > entry_max` swap. For SELL, skip.

Add: validate `sell_pct` is 1-100, default to 100 if missing or out of range. Set `sell_pct` on the result dict.

### 4. Risk Engine (risk_engine.py `validate_signal`)

The SELL branch needs to run **early**, before the BUY-only validations. Current code runs duplicate check (line 69), daily limit (line 72), and stop-loss default (line 59) before the action check at line 76. Restructure:

1. Symbol resolution and signal age check: keep for both BUY and SELL
2. After symbol resolution, check action. If SELL, branch immediately:
   - Fetch positions from `broker.get_positions()`
   - Find matching symbol position
   - Calculate sell quantity: `floor(held_qty * sell_pct / 100)`. If 0, send an informational card to the approval chat (symbol, sell_pct, held qty, why it's 0) and return invalid.
   - Store `avg_buy_price` and `held_qty` from Position on ValidationResult
   - Return early (skip duplicate check, daily limit, stop-loss default, entry price validation)
3. BUY path continues as before (duplicate, daily limit, stop-loss, etc.)

New fields on ValidationResult:
- `sell_pct: int = 100`
- `avg_buy_price: float = 0.0`
- `held_qty: int = 0`

### 5. Trade Card (approval_bot.py `format_trade_card`)

For SELL signals, show a different card layout:

```
--- SELL CANDIDATE ---

CAPLINPOINT (NSE) - SELL 50%

Avg Buy Price: 1,200.00
Current Price: 1,450.00
Unrealized P&L: +20.8% (+250.00/share)

Qty to sell: 5 of 10 held
Est. Proceeds: 7,250

Signal age: 3 min

Source:
"Exit 50% Caplin Point"

Reply to this message: A to approve, R to reject
---
```

The avg buy price and held quantity come from `broker.get_positions()` via ValidationResult fields set in the risk engine.

### 6. Approval Handler (approval_bot.py `handle_approval_reply`)

**This function needs a SELL-specific path.** Four issues in the current BUY-only flow:

1. **Balance check (line 283):** Skip for SELL. Selling doesn't require wallet balance.
2. **Quantity calculation (line 302):** For SELL, re-fetch positions from broker (position may have changed since the card was sent), apply `sell_pct` from candidate to compute partial quantity. Don't use `FIXED_ALLOCATION_AMOUNT / price`.
3. **Price-range reapproval (line 308):** Skip for SELL. Entry min/max are 0, so any price triggers reapproval.
4. **Limit price direction (line 353-356):** Already handled. BUY ceils at 1.002x, SELL floors at 0.998x.

Structure: after getting the quote, branch on `candidate["action"]`. SELL path: fetch positions, calculate qty from `sell_pct`, skip balance check and price-range check, then rejoin at the order placement.

### 7. Trade Candidate DB (db.py)

Add column `sell_pct INTEGER DEFAULT 100` to `trade_candidates` table (migration pattern: ALTER TABLE in `init_db` wrapped in try/except, same as `telegram_msg_id`).

Update `save_trade_candidate` to accept and INSERT `sell_pct`.

Update `get_all_pending_candidates` SELECT to include `sell_pct`.

Also persist `avg_buy_price` and `held_qty` on the candidate row so `/pending` resend can rebuild the SELL card without re-fetching positions. Add columns:
- `avg_buy_price REAL DEFAULT 0`
- `held_qty INTEGER DEFAULT 0`

### 8. Pending Resend Flow (main.py:143-164)

The `/pending` command rebuilds ValidationResult from DB rows and recalculates qty as `FIXED_ALLOCATION_AMOUNT / price`. For SELL candidates, it needs to:
- Use stored `sell_pct`, `avg_buy_price`, `held_qty` from the candidate row
- Calculate qty from `held_qty * sell_pct / 100` instead of allocation-based sizing

### 9. Partial Sell and Trade Closing

For partial sells, do NOT call `close_trade` on the open buy trade. A 50% sell leaves the other 50% still held. Instead:
- Record the SELL as its own row in `trades` with `side = 'SELL'`
- Only call `close_trade` when `sell_pct == 100` (full exit)
- For partial sells, just save the sell trade row. P&L for that portion is calculated from `(sell_price - avg_buy_price) * qty_sold`.

## Data Flow

```
"Exit 50% Caplin Point"
  -> tier1: {is_tip: true, confidence: 0.9}
  -> tier2: {symbol: "CAPLINPOINT", action: "SELL", sell_pct: 50, entry_min: 0, ...}
  -> risk_engine: broker.get_positions() -> 10 held @ avg 1200, sell 5
  -> save_trade_candidate: stores sell_pct=50, avg_buy_price=1200, held_qty=10
  -> approval card: shows avg buy price 1200, current 1450, P&L +20.8%, qty 5 of 10
  -> approve -> re-fetch positions, recalculate qty from sell_pct
  -> broker.place_order(SELL, 5) -> save sell trade row (no close_trade for partial)
```

## Testing

- Tier1: "Exit Caplin Point" -> is_tip: true (live test)
- Tier1: "Exit 50% Caplin Point" -> is_tip: true (live test)
- Tier2: extract sell_pct correctly for various phrasings (mocked)
- Tier2 validation: SELL with entry_min=0 passes, BUY with entry_min=0 still rejected (mocked)
- Risk engine: partial sell quantity calculation, reject when position is 0, SELL skips duplicate/daily-limit checks (mocked)
- Approval handler: SELL skips balance check, SELL skips price-range reapproval, SELL recalculates qty from positions (mocked)
- Trade card: SELL card format with P&L display (mocked)
- Partial sell: 50% sell does not close buy trade, 100% sell does close it (mocked)
- Pending resend: SELL candidate card rebuilt correctly from stored fields (mocked)
- Integration: full SELL flow with mocked broker (mocked)
