import sys
import types

import pytest
from risk_engine import ValidationResult

# yfinance isn't installable in this sandboxed/corporate-network environment
# (PyPI access is blocked). Stub it so approval_bot's transitive
# `import yfinance` (via market_data) succeeds.
if "yfinance" not in sys.modules:
    _yf_stub = types.ModuleType("yfinance")

    class _FakeFastInfo:
        last_price = 1490.0
        last_volume = 1000000
        day_high = 1495.0
        day_low = 1480.0

    class _FakeTicker:
        def __init__(self, ticker_symbol):
            self.ticker_symbol = ticker_symbol

        @property
        def fast_info(self):
            return _FakeFastInfo()

    _yf_stub.Ticker = _FakeTicker
    sys.modules["yfinance"] = _yf_stub

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
    assert "A to approve" in card
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
