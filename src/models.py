from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Quote:
    code: str
    name: str
    price: float
    change: float
    change_pct: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: float = 0.0
    amount: float = 0.0
    turnover: float = 0.0
    amplitude: float = 0.0
    pe: float | None = None
    pb: float | None = None
    total_market_cap: float | None = None
    float_market_cap: float | None = None
    industry: str = "--"
    updated_at: datetime = field(default_factory=datetime.now)
    source: str = "东方财富免费行情"
    is_demo: bool = False


@dataclass(slots=True)
class TrendPoint:
    time: datetime
    price: float
    high: float
    low: float
    volume: float
    amount: float
    avg_price: float


@dataclass(slots=True)
class DailyBar:
    date: datetime
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount: float
    change_pct: float = 0.0
    turnover: float = 0.0


@dataclass(slots=True)
class MarketIndex:
    code: str
    name: str
    price: float
    change: float
    change_pct: float


@dataclass(slots=True)
class Board:
    code: str
    name: str
    price: float
    change: float
    change_pct: float
    turnover: float = 0.0
    main_net_inflow: float = 0.0
    board_type: str = "行业"


@dataclass(slots=True)
class DataBundle:
    quote: Quote
    trends: list[TrendPoint]
    daily: list[DailyBar]
    markets: list[MarketIndex]
    boards: list[Board]
    received_at: datetime = field(default_factory=datetime.now)
    errors: list[str] = field(default_factory=list)


def dataclass_to_dict(value: Any) -> dict[str, Any]:
    """Convert one of the simple models to JSON-serialisable values."""
    result: dict[str, Any] = {}
    for key in value.__dataclass_fields__:
        item = getattr(value, key)
        result[key] = item.isoformat() if isinstance(item, datetime) else item
    return result
