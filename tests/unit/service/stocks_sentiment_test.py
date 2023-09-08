import asyncio
import datetime
from unittest.mock import Mock, AsyncMock

import pytest
from market_data_library.stocks.cnn_fear_greed.type import CnnFearGreedIndex, FearAndGreed, FearAndGreedHistorical, \
    FearAndGreedHistoricalData

from src.dependencies import Dependencies
from src.service.stocks_sentiment import StocksSentimentService
from src.type.sentiment import FearGreedResult, FearGreedData


class TestStocksSentimentService:

    @classmethod
    def setup_class(cls):
        asyncio.run(Dependencies.build())

    @classmethod
    def teardown_class(cls):
        asyncio.run(Dependencies.cleanup())

    @pytest.mark.parametrize(
        'data, expected',
        [
            (
                    CnnFearGreedIndex(
                        fear_and_greed=FearAndGreed(
                            previous_1_month=79,
                            previous_1_week=77,
                            previous_1_year=60,
                            previous_close=73,
                            rating='greed',
                            score=68,
                            timestamp='2023-08-04T23:59:56+00:00',
                        ),
                        fear_and_greed_historical=FearAndGreedHistorical(
                            data=[
                                FearAndGreedHistoricalData(
                                    rating='greed',
                                    x=1691107200000,
                                    y=63
                                ),
                                FearAndGreedHistoricalData(
                                    rating='greed',
                                    x=1691193596000,
                                    y=65
                                )
                            ],
                            rating='greed',
                            score=65,
                            timestamp=1691193596000
                        )
                    ),
                    FearGreedResult(data=[
                        FearGreedData(
                            relative_date_text='Previous close',
                            date=datetime.datetime(2023, 8, 4, 23, 59, 56, tzinfo=datetime.timezone.utc),
                            value=65,
                            sentiment_text='Greed',
                            emoji='🤑'
                        )],
                        average=[]
                    )
            )
        ]
    )
    @pytest.mark.asyncio
    async def test_get_stocks_fear_greed_index(self, data, expected):
        service = StocksSentimentService()
        service.cnn_service = Mock()

        service.cnn_service.get_fear_greed_index = AsyncMock(return_value=data)
        service.cnn_service.map_fear_greed_to_text = Mock(return_value={ 'text': 'Greed', 'emoji': '🤑' })

        res = await service.get_stocks_fear_greed_index()
        assert res == expected

