import sys
import types

import pytest
from unittest.mock import AsyncMock
from brokers.base import Quote

# yfinance isn't installable in this sandboxed/corporate-network environment
# (PyPI access is blocked). Stub it so market_data's top-level `import
# yfinance` succeeds, and the fallback test exercises real Quote-building
# logic against deterministic fake data instead of a live network call.
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
