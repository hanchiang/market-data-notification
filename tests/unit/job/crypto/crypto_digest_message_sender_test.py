import asyncio
import copy
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

    def build_sector_detail(
        self,
        sector_id: str,
        title: str,
        changes: list[tuple[str, float]],
    ) -> cmc_type.SectorDetail:
        coins = []
        for index, (symbol, change) in enumerate(changes, start=1):
            coins.append(
                cmc_type.SectorCoin(
                    id=index,
                    name=symbol,
                    slug=symbol.lower(),
                    symbol=symbol,
                    quote={
                        'USD': cmc_type.SectorCoinQuote(percent_change_24h=change)
                    },
                )
            )
        return cmc_type.SectorDetail(
            sectorId=sector_id,
            title=title,
            coins=coins,
        )

    @classmethod
    def setup_class(cls):
        asyncio.run(Dependencies.build())

    @classmethod
    def teardown_class(cls):
        asyncio.run(Dependencies.cleanup())

    @pytest.mark.asyncio
    async def test_format_message_builds_single_digest(self):
        strongest_sector = copy.deepcopy(self.sector_24h_change[0])
        strongest_sector.sectorId = 'video'
        strongest_sector.topCoins[0].symbol = 'POP'
        strongest_sector.topCoins[0].percentageChangePriceUsd = 6.2
        strongest_sector.topCoins[1].symbol = 'FLIXX'
        strongest_sector.topCoins[1].percentageChangePriceUsd = 5.4

        weakest_sector = copy.deepcopy(self.sector_24h_change[0])
        weakest_sector.sectorId = 'defi'
        weakest_sector.title = 'DeFi'
        weakest_sector.avgPriceChange = -4.8
        weakest_sector.marketChange = -3.2
        weakest_sector.volumeChange = -8.1
        weakest_sector.gainersNum = '2'
        weakest_sector.losersNum = '11'
        weakest_sector.topCoins[0].symbol = 'UNI'
        weakest_sector.topCoins[0].percentageChangePriceUsd = -6.1
        weakest_sector.topCoins[1].symbol = 'AAVE'
        weakest_sector.topCoins[1].percentageChangePriceUsd = -4.4

        sector_details = {
            'video': self.build_sector_detail(
                sector_id='video',
                title='Video',
                changes=[('POP', 6.2), ('FLIXX', 5.4), ('MBL', -2.1), ('THETA', -4.7)],
            ),
            'defi': self.build_sector_detail(
                sector_id='defi',
                title='DeFi',
                changes=[('RAY', 4.2), ('JUP', 2.9), ('UNI', -6.1), ('AAVE', -4.4)],
            ),
        }

        message_sender = CryptoDigestMessageSender()
        message_sender.sentiment_service.get_crypto_fear_greed_index = AsyncMock(
            return_value=self.sentiment
        )
        message_sender.cmc_service.get_sectors_24h_change = AsyncMock(
            side_effect=[[strongest_sector], [weakest_sector]]
        )
        message_sender.cmc_service.get_spotlight = AsyncMock(return_value=self.spotlight)
        message_sender.cmc_service.get_sector_detail = AsyncMock(
            side_effect=[
                sector_details['video'],
                sector_details['defi'],
            ]
        )

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
        assert 'leaders POP, FLIXX' in message
        assert 'losers THETA, MBL' in message
        assert 'Weakest 24h: *DeFi*' in message
        assert 'leaders RAY, JUP' in message
        assert 'losers UNI, AAVE' in message
        assert '*Standout coins*' in message
        assert 'gainers 2, losers 11' in message
        assert 'top coins' not in message
        assert '• trending, top loser: *Hifi Finance*' in message
        assert '• top gainer: *Cannation*' in message

    @pytest.mark.asyncio
    async def test_format_message_adds_threshold_gated_sector_detail(self):
        strongest_sector = copy.deepcopy(self.sector_24h_change[0])
        strongest_sector.sectorId = 'video'
        strongest_sector.topCoins[0].symbol = 'POP'
        strongest_sector.topCoins[0].percentageChangePriceUsd = 371.51836919
        strongest_sector.topCoins[1].symbol = 'FLIXX'
        strongest_sector.topCoins[1].percentageChangePriceUsd = 5.4

        weakest_sector = copy.deepcopy(self.sector_24h_change[0])
        weakest_sector.sectorId = 'memes'
        weakest_sector.title = 'Memes'
        weakest_sector.avgPriceChange = -7.4
        weakest_sector.marketChange = -5.6
        weakest_sector.volumeChange = -12.3
        weakest_sector.gainersNum = '1'
        weakest_sector.losersNum = '14'
        weakest_sector.topCoins[0].symbol = 'DOGE'
        weakest_sector.topCoins[0].percentageChangePriceUsd = -12.34
        weakest_sector.topCoins[1].symbol = 'FLOKI'
        weakest_sector.topCoins[1].percentageChangePriceUsd = -7.89

        sector_details = {
            'video': self.build_sector_detail(
                sector_id='video',
                title='Video',
                changes=[('POP', 371.51836919), ('FLIXX', 5.4), ('THETA', -12.5), ('MBL', -3.3)],
            ),
            'memes': self.build_sector_detail(
                sector_id='memes',
                title='Memes',
                changes=[('DOGE', 11.2), ('FLOKI', 7.89), ('ARIAIP', -32.7), ('DMCC', -24.96)],
            ),
        }

        message_sender = CryptoDigestMessageSender()
        message_sender.sentiment_service.get_crypto_fear_greed_index = AsyncMock(
            return_value=self.sentiment
        )
        message_sender.cmc_service.get_sectors_24h_change = AsyncMock(
            side_effect=[[strongest_sector], [weakest_sector]]
        )
        message_sender.cmc_service.get_spotlight = AsyncMock(return_value=self.spotlight)
        message_sender.cmc_service.get_sector_detail = AsyncMock(
            side_effect=[
                sector_details['video'],
                sector_details['memes'],
            ]
        )

        messages = await message_sender.format_message()

        assert len(messages) == 2
        digest_message, detail_message = messages
        assert 'leaders POP, FLIXX' in digest_message
        assert 'losers THETA, MBL' in digest_message
        assert 'leaders DOGE, FLOKI' in digest_message
        assert 'losers ARIAIP, DMCC' in digest_message
        assert '*Sector detail*' in detail_message
        assert 'Strongest 24h: *Video*' in detail_message
        assert 'Leaders POP \\+371\\.52%' in detail_message
        assert 'Losers THETA \\-12\\.50%' in detail_message
        assert 'Weakest 24h: *Memes*' in detail_message
        assert 'Leaders DOGE \\+11\\.20%' in detail_message
        assert 'Losers ARIAIP \\-32\\.70%, DMCC \\-24\\.96%' in detail_message

    @pytest.mark.asyncio
    async def test_format_message_omits_sector_side_when_no_matching_sign_exists(self):
        strongest_sector = copy.deepcopy(self.sector_24h_change[0])
        strongest_sector.sectorId = 'logistics'
        strongest_sector.title = 'Logistics'

        weakest_sector = copy.deepcopy(self.sector_24h_change[0])
        weakest_sector.sectorId = 'music'
        weakest_sector.title = 'Music'
        weakest_sector.avgPriceChange = -7.4
        weakest_sector.marketChange = -5.6
        weakest_sector.volumeChange = -12.3
        weakest_sector.gainersNum = '1'
        weakest_sector.losersNum = '14'

        sector_details = {
            'logistics': self.build_sector_detail(
                sector_id='logistics',
                title='Logistics',
                changes=[('BLY', 391.81), ('CXO', 18.4), ('PPT', 0.24), ('TRAC', 0.11)],
            ),
            'music': self.build_sector_detail(
                sector_id='music',
                title='Music',
                changes=[('ARIAIP', 30.17), ('VOISE', 15.84), ('ARTX', -13.75), ('DMCC', 15.36)],
            ),
        }

        message_sender = CryptoDigestMessageSender()
        message_sender.sentiment_service.get_crypto_fear_greed_index = AsyncMock(
            return_value=self.sentiment
        )
        message_sender.cmc_service.get_sectors_24h_change = AsyncMock(
            side_effect=[[strongest_sector], [weakest_sector]]
        )
        message_sender.cmc_service.get_spotlight = AsyncMock(return_value=self.spotlight)
        message_sender.cmc_service.get_sector_detail = AsyncMock(
            side_effect=[
                sector_details['logistics'],
                sector_details['music'],
            ]
        )

        messages = await message_sender.format_message()

        assert len(messages) == 2
        digest_message, detail_message = messages
        assert 'leaders BLY, CXO' in digest_message
        assert 'losers PPT, TRAC' not in digest_message
        assert 'leaders ARIAIP, VOISE' in digest_message
        assert 'losers ARTX' in digest_message
        assert 'DMCC' not in detail_message
        assert 'Losers BLY' not in detail_message
        assert 'Losers ARTX \\-13\\.75%' in detail_message

    @pytest.mark.asyncio
    async def test_format_message_falls_back_when_sector_detail_fetch_fails(self):
        strongest_sector = copy.deepcopy(self.sector_24h_change[0])
        strongest_sector.sectorId = 'video'
        strongest_sector.title = 'Video'

        weakest_sector = copy.deepcopy(self.sector_24h_change[0])
        weakest_sector.sectorId = 'music'
        weakest_sector.title = 'Music'
        weakest_sector.avgPriceChange = -7.4
        weakest_sector.marketChange = -5.6
        weakest_sector.volumeChange = -12.3
        weakest_sector.gainersNum = '1'
        weakest_sector.losersNum = '14'

        message_sender = CryptoDigestMessageSender()
        message_sender.sentiment_service.get_crypto_fear_greed_index = AsyncMock(
            return_value=self.sentiment
        )
        message_sender.cmc_service.get_sectors_24h_change = AsyncMock(
            side_effect=[[strongest_sector], [weakest_sector]]
        )
        message_sender.cmc_service.get_spotlight = AsyncMock(return_value=self.spotlight)
        message_sender.cmc_service.get_sector_detail = AsyncMock(
            side_effect=RuntimeError('403 Forbidden')
        )

        messages = await message_sender.format_message()

        assert len(messages) == 1
        message = messages[0]
        assert 'Strongest 24h: *Video*' in message
        assert '; leaders ' not in message
        assert '; losers ' not in message
        assert '*Sector detail*' not in message
