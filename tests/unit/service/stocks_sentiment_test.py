import datetime
from unittest.mock import AsyncMock, Mock

import pytest
from market_data_library.types import cnn_type

from src.service.stocks_sentiment import StocksSentimentService
from src.type.sentiment import FearGreedAverage, FearGreedData, FearGreedResult


class TestStocksSentimentService:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            (
                cnn_type.CnnFearGreedIndex(
                    fear_and_greed=cnn_type.FearAndGreed(
                        previous_1_month=79,
                        previous_1_week=77,
                        previous_1_year=60,
                        previous_close=73,
                        rating="greed",
                        score=68,
                        timestamp="2023-08-04T23:59:56+00:00",
                    ),
                    fear_and_greed_historical=cnn_type.FearAndGreedHistorical(
                        data=[
                            cnn_type.FearAndGreedHistoricalData(
                                rating="neutral",
                                x=1736726400000,  # 2025-01-13
                                y=10,
                            ),
                            cnn_type.FearAndGreedHistoricalData(
                                rating="neutral",
                                x=1736812800000,  # 2025-01-14
                                y=20,
                            ),
                            cnn_type.FearAndGreedHistoricalData(
                                rating="neutral",
                                x=1736899200000,  # 2025-01-15
                                y=30,
                            ),
                            cnn_type.FearAndGreedHistoricalData(
                                rating="neutral",
                                x=1736985600000,  # 2025-01-16
                                y=40,
                            ),
                            cnn_type.FearAndGreedHistoricalData(
                                rating="neutral",
                                x=1737072000000,  # 2025-01-17
                                y=50,
                            ),
                            cnn_type.FearAndGreedHistoricalData(
                                rating="greed",
                                x=1737417600000,  # 2025-01-21 after MLK Day
                                y=60,
                            ),
                        ],
                        rating="greed",
                        score=60,
                        timestamp=1737460800000,
                    ),
                ),
                FearGreedResult(
                    data=[
                        FearGreedData(
                            relative_date_text="Previous close",
                            date=datetime.datetime(
                                2025,
                                1,
                                21,
                                0,
                                0,
                                tzinfo=datetime.timezone.utc,
                            ),
                            value=60,
                            sentiment_text="Greed",
                            emoji="🤑",
                        ),
                        FearGreedData(
                            relative_date_text="Last week",
                            date=datetime.datetime(
                                2025,
                                1,
                                14,
                                0,
                                0,
                                tzinfo=datetime.timezone.utc,
                            ),
                            value=20,
                            sentiment_text="Fear",
                            emoji="😨",
                        ),
                    ],
                    average=[
                        FearGreedAverage(
                            timeframe="1 week",
                            value=45,
                            sentiment_text="Neutral",
                            emoji="😐",
                        ),
                        FearGreedAverage(
                            timeframe="1 month",
                            value=35,
                            sentiment_text="Neutral",
                            emoji="😐",
                        ),
                        FearGreedAverage(
                            timeframe="3 months",
                            value=35,
                            sentiment_text="Neutral",
                            emoji="😐",
                        ),
                        FearGreedAverage(
                            timeframe="1 year",
                            value=35,
                            sentiment_text="Neutral",
                            emoji="😐",
                        ),
                    ],
                ),
            )
        ],
    )
    @pytest.mark.asyncio
    async def test_get_stocks_fear_greed_index(self, data, expected):
        service = object.__new__(StocksSentimentService)
        service.cnn_service = Mock()
        service.cnc_type = cnn_type

        service.cnn_service.get_fear_greed_index = AsyncMock(return_value=data)
        service.cnn_service.map_fear_greed_to_text = Mock(
            side_effect=[
                {"text": "Greed", "emoji": "🤑"},
                {"text": "Fear", "emoji": "😨"},
                {"text": "Neutral", "emoji": "😐"},
                {"text": "Neutral", "emoji": "😐"},
                {"text": "Neutral", "emoji": "😐"},
                {"text": "Neutral", "emoji": "😐"},
            ]
        )

        res = await service.get_stocks_fear_greed_index()
        assert res == expected
