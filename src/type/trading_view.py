import enum
from dataclasses import dataclass
from typing import List

class TradingViewDataType(enum.Enum):
    STOCKS = 'stocks'
    ECONOMY_INDICATOR = 'economy_indicator'

@dataclass
class TradingViewData:
    symbol: str # SPY
    timeframe: str  # 1D
    close_prices: List[float]
    ema20s: List[float]
    volumes: List[float]
    type: TradingViewDataType

