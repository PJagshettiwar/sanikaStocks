from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Quote:
    symbol: str
    exchange: str
    price: float
    volume: int
    day_high: float
    day_low: float


@dataclass
class Order:
    symbol: str
    exchange: str
    security_id: str
    txn_type: str  # BUY or SELL
    qty: int
    order_type: str  # LIMIT or MARKET
    limit_price: float | None
    product: str  # CNC, INTRADAY, MARGIN
    validity: str  # DAY or IOC


@dataclass
class OrderResult:
    order_id: str
    status: str


@dataclass
class Position:
    security_id: str
    symbol: str
    exchange: str
    net_qty: int
    avg_price: float


class BrokerInterface(ABC):
    @abstractmethod
    async def get_balance(self) -> float: ...

    @abstractmethod
    async def get_quote(self, symbol: str, exchange: str) -> Quote: ...

    @abstractmethod
    async def place_order(self, order: Order) -> OrderResult: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_instruments(self) -> dict[str, str]: ...
