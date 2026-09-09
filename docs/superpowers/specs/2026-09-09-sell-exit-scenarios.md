# Sell/Exit Signal Scenarios

Real examples of what happens when someone posts a sell message in the Telegram channel.

---

## Scenario 1: Full exit

**Channel message:** "Exit Caplin Point"

- Bot detects this as a sell tip
- Extracts: CAPLINPOINT, SELL, 100%
- Checks broker: you hold 10 shares, avg buy price 1,200
- Sends you a card:
  ```
  CAPLINPOINT (NSE) - SELL 100%
  Avg Buy Price: 1,200.00
  Current Price: 1,450.00
  P&L: +20.8% (+250/share)
  Qty to sell: 10 of 10 held
  Est. Proceeds: 14,500
  ```
- You reply A to approve
- Bot sells all 10 shares at market
- Buy trade is closed in DB with final P&L

---

## Scenario 2: Partial exit (50%)

**Channel message:** "Exit 50% Caplin Point"

- Same detection and extraction, but sell_pct = 50
- Checks broker: you hold 10 shares, avg buy price 1,200
- Calculates: sell 5 shares (floor of 10 * 50%)
- Sends you a card:
  ```
  CAPLINPOINT (NSE) - SELL 50%
  Avg Buy Price: 1,200.00
  Current Price: 1,450.00
  P&L: +20.8% (+250/share)
  Qty to sell: 5 of 10 held
  Est. Proceeds: 7,250
  ```
- You reply A, bot sells 5 shares
- Buy trade stays open (you still hold 5)

---

## Scenario 3: Partial exit followed by full exit

**First message:** "Exit 50% Caplin Point"
- Sells 5 of 10 shares (as above)

**Second message (next day):** "Exit Caplin Point"
- Checks broker: you now hold 5 shares
- Calculates: sell 5 (100% of remaining)
- Card shows qty 5 of 5 held
- On approve, sells all 5
- Buy trade is now closed with P&L

---

## Scenario 4: Small partial exit rounds down

**Channel message:** "Exit 30% Reliance"

- You hold 3 shares
- 30% of 3 = 0.9, rounds down to 0
- Bot sends a rejection card:
  ```
  RELIANCE (NSE) - SELL 30%
  Cannot sell: 30% of 3 held = 0 shares
  Source: "Exit 30% Reliance"
  ```
- No approval prompt, just informational

---

## Scenario 5: Stock not in portfolio

**Channel message:** "Exit TCS"

- Checks broker: you don't hold TCS
- Bot rejects: "No position held for TCS"
- No card sent

---

## Scenario 6: "Book profits" phrasing

**Channel message:** "Book profits in Infosys"

- Bot treats this the same as "Exit Infosys"
- Extracts: INFY, SELL, 100%
- Normal sell flow from there

---

## Scenario 7: Partial book profits

**Channel message:** "Book 50% profits in Infosys"

- Same as "Exit 50% Infosys"
- Extracts: INFY, SELL, 50%

---

## Scenario 8: Price moves between card and approval

**Channel message:** "Exit Caplin Point"

- Card sent showing price 1,450
- You wait 10 minutes, reply A
- Bot re-fetches position from broker (still 10 shares)
- Current price is now 1,480
- Sells at 1,480, not 1,450
- No reapproval prompt (price-range check is skipped for sells)

---

## Scenario 9: Position changed between card and approval

**Channel message:** "Exit 50% Caplin Point"

- Card sent showing 5 of 10 held
- You manually sell 4 shares on the broker app before approving
- You reply A
- Bot re-fetches position: now only 6 shares held
- Recalculates: 50% of 6 = 3 shares
- Sells 3 (not the original 5)

---

## Scenario 10: Low wallet balance doesn't block sells

**Channel message:** "Exit Reliance"

- Your wallet has only 200 (not enough to buy anything)
- Doesn't matter for selling, bot skips balance check
- Proceeds with the sell normally

---

## What still gets ignored

These messages are NOT treated as sell tips:

- "Hold Caplin Point" (hold = not a tip)
- "Trail SL to 1400" (trailing stop loss = not a tip)
- "Continue to hold Infosys" (hold = not a tip)
- "Markets looking bearish today" (commentary = not a tip)
