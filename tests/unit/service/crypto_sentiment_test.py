import asyncio
import datetime
from unittest.mock import Mock, AsyncMock

import pytest
from market_data_library.types import alternativeme_type

from src.dependencies import Dependencies
from src.service.crypto.crypto_sentiment import CryptoSentimentService
from src.type.sentiment import FearGreedResult, FearGreedData


class TestCryptoSentimentService:

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
                    alternativeme_type.AlternativeMeFearGreedIndex(
                    data=alternativeme_type.FearGreedIndex(
                        datasets=[alternativeme_type.FearGreedIndexData(data=[1, 2, 3])],
                        labels=['30 Jul, 2022', '31 Jul, 2022', '1 Aug, 2022']
                    ),
                ),
                    FearGreedResult(data=[
                    FearGreedData(
                        relative_date_text='Now',
                        date=datetime.datetime(2022, 8, 1, 0, 0, 0, tzinfo=datetime.timezone.utc),
                        value=3,
                        sentiment_text='Extreme fear',
                        emoji='🥵🥵'
                    ),
                    FearGreedData(
                        relative_date_text='Yesterday',
                        date=datetime.datetime(2022, 7, 31, 0, 0, 0, tzinfo=datetime.timezone.utc),
                        value=2,
                        sentiment_text='Extreme fear',
                        emoji="🥵🥵"
                    )
                ], average=[])
            )
        ]
    )
    @pytest.mark.asyncio
    async def test_get_crypto_fear_greed_index(self, data, expected):
        service = CryptoSentimentService()
        service.alternativeme_service = Mock()

        service.alternativeme_service.get_fear_greed_index = AsyncMock(return_value=data)
        service.alternativeme_service.map_fear_greed_to_text = Mock(return_value={ 'text': 'Extreme fear', 'emoji': '🥵🥵' })

        res = await service.get_crypto_fear_greed_index()
        assert res == expected

