import enum
from dataclasses import dataclass, field
from typing import List

class TradingViewDataType(enum.Enum):
    STOCKS = 'stocks'
    ECONOMY_INDICATOR = 'economy_indicator'

@dataclass
class TradingViewStocksData:
    symbol: str # SPY
    timeframe: str  # 1D
    close_prices: List[float]
    ema20s: List[float] = field(default_factory=list)
    volumes: List[float] = field(default_factory=list)

@dataclass
class TradingViewData:
    type: TradingViewDataType
    data: List[TradingViewStocksData]
    unix_ms: int

@dataclass
class TradingViewRedisData:
    key: str
    score: int
    data: TradingViewData