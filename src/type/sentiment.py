import datetime
from dataclasses import dataclass, field
from typing import List


@dataclass
class FearGreedData:
    relative_date_text: str = field(default_factory=str)
    date: datetime.datetime = field(default_factory=datetime.datetime)
    value: int = field(default_factory=int)
    sentiment_text: str = field(default_factory=str)
    emoji: str = field(default_factory=str)

@dataclass
class FearGreedAverage:
    timeframe: str = field(default_factory=str)
    value: float = field(default_factory=float)
    sentiment_text: str = field(default_factory=str)
    emoji: str = field(default_factory=str)

@dataclass
class FearGreedResult:
    data: [FearGreedData] = field(default_factory=List)
    average: [FearGreedAverage] = field(default_factory=List)
