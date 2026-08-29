# Telegram Stock Agent - Task List

## Tasks

- [x] 1. Connect with Telegram from local (Telethon user client auth)
- [x] 2. Read 10 messages from each group, save to a single MD file
- [x] 3. Copy those messages into the database
- [x] 4. Apply LLM to analyse messages and extract content for search
- [x] 5. Filter tip and information messages (9 signals from 84 msgs)
- [x] 6. Test the logic for ticker search (all 9 symbols resolve against 18,736 instruments)
- [x] 7. Draft an approval message (format_trade_card in approval_bot.py)
- [x] 8. Take approval from local terminal, then take dummy action (scripts/test_approval_local.py)
- [x] 9. Post the message in Telegram bot
- [x] 10. Test the broker connection (TOTP auto-login working, balance + instruments verified)
- [x] 11. Read the approval from Telegram (reply-based tracking)
- [ ] 12. Take action on broker API
- [ ] 13. On success, send success message on Telegram with all details

## What Works (Verified 2026-08-29)

### Telegram Connection
- Telethon user client authenticated with existing session
- Monitoring RangaOne Premium channel only (swing trades)
- Bot: @sanika_stocks_update_bot posts cards and receives replies

### Telegram Bot Commands
- /status - health check (broker, LLM, Telegram) with auto-heal
- /pending - resend all pending cards with live prices
- /cancel SYMBOL - cancel a pending trade
- /help - list commands

### Trade Card Format
- Symbol/action headline, live current price from broker
- Targets with % profit (T1: 8,100 (+5.5%))
- SL with % shown only when provided by channel
- Fixed 5,000/trade allocation, wallet balance displayed
- Full original message preserved
- Reply-based approval (reply A or R to the card message)
- SELL signals show "No action - not in portfolio" when stock not held

### Approval Flow (reply-based)
- Bot tracks message_id -> candidate_id in DB (survives restart)
- On restart: notifies about pending count, /pending to resend
- Market closed: keeps card pending, reply again during hours
- Insufficient funds: keeps card pending, reply A after adding funds
- Broker/quote error: keeps card pending, reply A to retry
- Already processed: shows "Already executed/rejected"
- Price drift: re-approval card with new price and qty

### LLM Pipeline
- Tier 1 (detection): identifies tip vs non-tip messages
- Tier 2 (extraction): extracts symbol, entry range, SL, targets
- 11 signals extracted, all symbols resolve (JYOTI CNC -> JYOTICNC auto-corrected)

### Ticker Resolution
- INDstocks instrument list: 18,736 symbols loaded
- Fuzzy matching + space/hyphen stripping for LLM symbol names
- 0.5s delay between quote calls to avoid rate limiting

### Broker Connection (INDstocks)
- TOTP auto-login implemented (no manual token refresh needed)
- Token valid 24 hours, auto-retry on 403
- Balance, instruments, quotes, order placement all wired up
- Rate limit: 1 token request per 60 seconds

### Trade & Cost Tracking
- api_costs table: every LLM call logged with tokens, cost, model, timestamp
- trades table: buy/sell with P&L tracking (price, charges, pnl, pnl_pct)
- Portfolio summary function for reporting

## Notes

- Free LLM model shows $0 cost; will show real cost when switched to paid
- INDstocks brokerage: Rs 10 flat per order
- Static IP required for order placement (plan: Oracle Cloud Free Tier)
- Wallet balance is currently 0 (no funds deposited)
- LLM false positives: "hold" updates and "book profits" messages sometimes pass Tier 1 filter
- INDstocks quotes API returns 400 for some tickers when market is closed
- Scripts in scripts/ for manual testing each step

## Next: Tasks 12-13

- Task 12: Place actual order on broker API when approved
- Task 13: Send success/failure confirmation on Telegram after order
- Both are already wired in approval_bot.py handle_approval_reply, need live testing with funds + market open
