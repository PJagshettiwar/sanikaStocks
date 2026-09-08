import asyncio
import yfinance as yf
from brokers.base import BrokerInterface, Quote

NSE_SUFFIX = ".NS"


async def get_quote(symbol: str, exchange: str, broker: BrokerInterface | None = None) -> Quote:
    if broker:
        try:
            return await broker.get_quote(symbol, exchange)
        except Exception:
            pass
    return await _yfinance_quote(symbol, exchange)


async def _yfinance_quote(symbol: str, exchange: str) -> Quote:
    ticker_symbol = f"{symbol}{NSE_SUFFIX}"

    def _fetch():
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.fast_info
        return Quote(
            symbol=symbol,
            exchange="NSE",
            price=float(info.last_price),
            volume=int(info.last_volume or 0),
            day_high=float(info.day_high or 0),
            day_low=float(info.day_low or 0),
        )

    return await asyncio.to_thread(_fetch)
