import asyncio
import datetime
import json
import os
from unittest.mock import AsyncMock

import pytest
from dacite import from_dict
from market_data_library.types import cmc_type

from src.dependencies import Dependencies
from src.job.crypto.crypto_digest_message_sender import CryptoDigestMessageSender
from src.type.sentiment import FearGreedAverage, FearGreedData, FearGreedResult


class TestCryptoDigestMessageSender:
    def setup_method(self):
        self.load_sector_24h_change()
        self.load_spotlight()
        self.sentiment = FearGreedResult(
            data=[
                FearGreedData(
                    relative_date_text='Now',
                    date=datetime.datetime(2026, 3, 29, tzinfo=datetime.timezone.utc),
                    value=65,
                    sentiment_text='Greed',
                    emoji='🙂',
                ),
                FearGreedData(
                    relative_date_text='Yesterday',
                    date=datetime.datetime(2026, 3, 28, tzinfo=datetime.timezone.utc),
                    value=58,
                    sentiment_text='Greed',
                    emoji='🙂',
                ),
                FearGreedData(
                    relative_date_text='Last week',
                    date=datetime.datetime(2026, 3, 22, tzinfo=datetime.timezone.utc),
                    value=49,
                    sentiment_text='Neutral',
                    emoji='😐',
                ),
            ],
            average=[
                FearGreedAverage(
                    timeframe='7d',
                    value=57.2,
                    sentiment_text='Greed',
                    emoji='🙂',
                ),
                FearGreedAverage(
                    timeframe='30d',
                    value=44.1,
                    sentiment_text='Fear',
                    emoji='🥵',
                )
            ],
        )

    def load_sector_24h_change(self):
        dir_path = os.path.dirname(os.path.realpath(__file__))
        file_path = os.path.join(dir_path, '..', '..', '..', 'data', 'cmc', 'sector_24h_change.json')
        data = json.load(open(file_path))
        self.sector_24h_change = [
            from_dict(data_class=cmc_type.Sector24hChange, data=x) for x in data
        ]

    def load_spotlight(self):
        dir_path = os.path.dirname(os.path.realpath(__file__))
        file_path = os.path.join(dir_path, '..', '..', '..', 'data', 'cmc', 'cmc_spotlight.json')
        data = json.load(open(file_path))
        self.spotlight = from_dict(data_class=cmc_type.Spotlight, data=data)

    @classmethod
    def setup_class(cls):
        asyncio.run(Dependencies.build())

    @classmethod
    def teardown_class(cls):
        asyncio.run(Dependencies.cleanup())

    @pytest.mark.asyncio
    async def test_format_message_builds_single_digest(self):
        message_sender = CryptoDigestMessageSender()
        message_sender.sentiment_service.get_crypto_fear_greed_index = AsyncMock(
            return_value=self.sentiment
        )
        message_sender.cmc_service.get_sectors_24h_change = AsyncMock(
            side_effect=[self.sector_24h_change, list(reversed(self.sector_24h_change))]
        )
        message_sender.cmc_service.get_spotlight = AsyncMock(return_value=self.spotlight)

        messages = await message_sender.format_message()

        assert len(messages) == 1
        message = messages[0]
        assert '*Crypto market digest*' in message
        assert '*Sentiment*' in message
        assert 'Now: Greed' in message
        assert 'Averages:' in message
        assert '7d avg: Greed' in message
        assert '30d avg: Fear' in message
        assert '*Sector breadth*' in message
        assert 'Strongest 24h: *Video*' in message
        assert '*Standout coins*' in message
        assert '*Hifi Finance*' in message
        assert 'trending, top loser' in message
        assert '*Cannation*' in message
        assert 'top gainer' in message
