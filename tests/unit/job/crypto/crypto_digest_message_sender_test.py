import copy
import dataclasses
import datetime
import json
import os
import typing
from unittest.mock import AsyncMock, Mock

import pytest
from dacite import from_dict
from market_data_library.types import cmc_type

from src.job.crypto.crypto_digest_message_sender import CryptoDigestMessageSender
from src.runtime.runtime_mode import DEFAULT_RUNTIME_MODE
from src.type.sentiment import FearGreedAverage, FearGreedData, FearGreedResult


class TestCryptoDigestMessageSender:
    @staticmethod
    def remove_unknown_fields(my_value, fields: list[dataclasses.Field]):
        field_by_name = {field.name: field for field in fields}

        if isinstance(my_value, (str, int, bool, float)):
            return

        for key, value in list(my_value.items()):
            if key not in field_by_name:
                del my_value[key]
                continue

            field = field_by_name[key]
            if isinstance(value, dict):
                TestCryptoDigestMessageSender.remove_unknown_fields(
                    value, dataclasses.fields(field.type)
                )
            elif isinstance(value, list):
                for item in value:
                    generic_type = typing.get_args(field.type)[0]
                    if generic_type != typing.Any and not isinstance(
                        generic_type(), (str, int, bool, float)
                    ):
                        TestCryptoDigestMessageSender.remove_unknown_fields(
                            item, dataclasses.fields(generic_type)
                        )

    def setup_method(self):
        self.load_sector_24h_change()
        self.load_spotlight()
        self.load_coin_detail()
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

    def load_coin_detail(self):
        dir_path = os.path.dirname(os.path.realpath(__file__))
        file_path = os.path.join(dir_path, '..', '..', '..', 'data', 'cmc', 'coin_detail.json')
        data = json.load(open(file_path))

        fields = dataclasses.fields(cmc_type.CoinDetail)
        self.remove_unknown_fields(data, fields)

        coin_detail = cmc_type.CoinDetail(**data)
        coin_detail.statistics = cmc_type.CoinDetailStatistics(
            **data.get('statistics', {})
        )
        coin_detail.relatedCoins = [
            cmc_type.RelatedCoin(**x) for x in data.get('relatedCoins', [])
        ]
        coin_detail.relatedExchanges = [
            cmc_type.RelatedExchange(**x) for x in data.get('relatedExchanges', [])
        ]
        coin_detail.wallets = [
            cmc_type.CoinDetailWallet(**x) for x in data.get('wallets', [])
        ]
        coin_detail.holders = cmc_type.CoinDetailHolder(
            **data.get('holders', {})
        )
        coin_detail.faqDescription = [
            cmc_type.FAQ(**x) for x in data.get('faqDescription', [])
        ]
        coin_detail.cryptoRating = [
            cmc_type.CryptoRating(**x) for x in data.get('cryptoRating', [])
        ]
        self.coin_detail = coin_detail

    def build_sector_detail(
        self,
        sector_id: str,
        title: str,
        changes: list[tuple],
    ) -> cmc_type.SectorDetail:
        coins = []
        for index, change_entry in enumerate(changes, start=1):
            symbol, change = change_entry[0], change_entry[1]
            volume_24h = change_entry[2] if len(change_entry) > 2 else 0.0
            coins.append(
                cmc_type.SectorCoin(
                    id=index,
                    name=symbol,
                    slug=symbol.lower(),
                    symbol=symbol,
                    quote={
                        'USD': cmc_type.SectorCoinQuote(
                            percent_change_24h=change,
                            volume_24h=volume_24h,
                        )
                    },
                )
            )
        return cmc_type.SectorDetail(
            sectorId=sector_id,
            title=title,
            coins=coins,
        )

    def build_message_sender(self):
        message_sender = object.__new__(CryptoDigestMessageSender)
        message_sender.cmc_service = AsyncMock()
        message_sender.sentiment_service = AsyncMock()
        message_sender.signal_repository = Mock()
        message_sender.tracked_universe_entries = [('BTC', 1), ('ETH', 1027), ('SOL', 5426)]
        message_sender.watchlist_entries = [('BTC', 1), ('ETH', 1027), ('SOL', 5426)]
        message_sender.runtime_mode = DEFAULT_RUNTIME_MODE
        return message_sender

    @pytest.mark.asyncio
    async def test_format_message_builds_single_digest(self):
        spotlight = copy.deepcopy(self.spotlight)
        spotlight.gainerList[0].priceChange.priceChange24h = 55.0

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
                changes=[
                    ('POP', 6.2, 12_500_000),
                    ('FLIXX', 5.4, 8_300_000),
                    ('MBL', -2.1, 3_100_000),
                    ('THETA', -4.7, 4_400_000),
                ],
            ),
            'defi': self.build_sector_detail(
                sector_id='defi',
                title='DeFi',
                changes=[
                    ('RAY', 4.2, 18_000_000),
                    ('JUP', 2.9, 14_500_000),
                    ('UNI', -6.1, 11_200_000),
                    ('AAVE', -4.4, 9_800_000),
                ],
            ),
        }

        message_sender = self.build_message_sender()
        message_sender.sentiment_service.get_crypto_fear_greed_index = AsyncMock(
            return_value=self.sentiment
        )
        message_sender.cmc_service.get_sectors_24h_change = AsyncMock(
            side_effect=[[strongest_sector], [weakest_sector]]
        )
        message_sender.cmc_service.get_spotlight = AsyncMock(return_value=spotlight)
        message_sender.cmc_service.get_coin_detail = AsyncMock(return_value=self.coin_detail)
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
        assert 'price 1\\.15' in message
        assert 'volume 1\\.26 B' in message
        assert 'volume change \\+161\\.37%' in message
        assert '• top gainer: *Cannation*' in message
        assert '24h \\+55\\.00% ❗' in message
        message_sender.signal_repository.save_snapshot.assert_called_once()
        standout_section = message.split('*Standout coins*', maxsplit=1)[1]
        assert '7d ' not in standout_section
        assert 'mcap ' not in standout_section

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
                changes=[
                    ('POP', 371.51836919, 42_000_000),
                    ('FLIXX', 5.4, 8_300_000),
                    ('THETA', -12.5, 7_600_000),
                    ('MBL', -3.3, 3_100_000),
                ],
            ),
            'memes': self.build_sector_detail(
                sector_id='memes',
                title='Memes',
                changes=[
                    ('DOGE', 11.2, 220_000_000),
                    ('FLOKI', 7.89, 86_000_000),
                    ('ARIAIP', -32.7, 2_100_000),
                    ('DMCC', -24.96, 1_500_000),
                ],
            ),
        }

        message_sender = self.build_message_sender()
        message_sender.sentiment_service.get_crypto_fear_greed_index = AsyncMock(
            return_value=self.sentiment
        )
        message_sender.cmc_service.get_sectors_24h_change = AsyncMock(
            side_effect=[[strongest_sector], [weakest_sector]]
        )
        message_sender.cmc_service.get_spotlight = AsyncMock(return_value=self.spotlight)
        message_sender.cmc_service.get_coin_detail = AsyncMock(return_value=self.coin_detail)
        message_sender.cmc_service.get_sector_detail = AsyncMock(
            side_effect=[
                sector_details['video'],
                sector_details['memes'],
            ]
        )

        messages = await message_sender.format_message()

        assert len(messages) == 1
        message = messages[0]
        assert 'leaders POP, FLIXX' in message
        assert 'losers THETA, MBL' in message
        assert 'leaders DOGE, FLOKI' in message
        assert 'losers ARIAIP, DMCC' in message
        assert '*Sector detail*' in message
        assert 'Strongest 24h: *Video*' in message
        assert 'Leaders:' in message
        assert '• *POP* \\+371\\.52%, vol 42 M, vol chg \\+161\\.37%' in message
        assert 'Losers:' in message
        assert '• *THETA* \\-12\\.50%, vol 7\\.6 M, vol chg \\+161\\.37%' in message
        assert 'Weakest 24h: *Memes*' in message
        assert '• *DOGE* \\+11\\.20%, vol 220' in message
        assert 'vol chg \\+161\\.37%' in message
        assert '• *ARIAIP* \\-32\\.70%, vol 2\\.1 M, vol chg \\+161\\.37%' in message
        assert message.index('*Sector detail*') < message.index('*Standout coins*')

    @pytest.mark.asyncio
    async def test_sector_detail_stays_in_sync_with_breadth_once_message_is_emitted(self):
        strongest_sector = copy.deepcopy(self.sector_24h_change[0])
        strongest_sector.sectorId = 'logistics'
        strongest_sector.title = 'Logistics'
        strongest_sector.avgPriceChange = 295.26
        strongest_sector.marketChange = -0.72
        strongest_sector.volumeChange = 103.28
        strongest_sector.gainersNum = '1'
        strongest_sector.losersNum = '3'

        weakest_sector = copy.deepcopy(self.sector_24h_change[0])
        weakest_sector.sectorId = 'binance-buildkey-tge'
        weakest_sector.title = 'Binance Buildkey TGE'
        weakest_sector.avgPriceChange = -8.04
        weakest_sector.marketChange = -8.27
        weakest_sector.volumeChange = -33.37
        weakest_sector.gainersNum = '0'
        weakest_sector.losersNum = '3'

        sector_details = {
            'logistics': self.build_sector_detail(
                sector_id='logistics',
                title='Logistics',
                changes=[
                    ('BLY', 428.02, 65_000_000),
                    ('CXO', 12.12, 15_000_000),
                    ('PPT', -3.31, 4_200_000),
                    ('VET', -2.44, 7_400_000),
                ],
            ),
            'binance-buildkey-tge': self.build_sector_detail(
                sector_id='binance-buildkey-tge',
                title='Binance Buildkey TGE',
                changes=[
                    ('RIVER', -7.25, 3_600_000),
                    ('FIGHT', -5.81, 2_200_000),
                ],
            ),
        }

        message_sender = self.build_message_sender()
        message_sender.sentiment_service.get_crypto_fear_greed_index = AsyncMock(
            return_value=self.sentiment
        )
        message_sender.cmc_service.get_sectors_24h_change = AsyncMock(
            side_effect=[[strongest_sector], [weakest_sector]]
        )
        message_sender.cmc_service.get_spotlight = AsyncMock(return_value=self.spotlight)
        message_sender.cmc_service.get_coin_detail = AsyncMock(return_value=self.coin_detail)
        message_sender.cmc_service.get_sector_detail = AsyncMock(
            side_effect=[
                sector_details['logistics'],
                sector_details['binance-buildkey-tge'],
            ]
        )

        messages = await message_sender.format_message()

        assert len(messages) == 1
        message = messages[0]
        assert 'leaders BLY, CXO' in message
        assert 'losers PPT, VET' in message
        assert 'losers RIVER, FIGHT' in message
        assert '• *BLY* \\+428\\.02%, vol 65 M, vol chg \\+161\\.37%' in message
        assert '• *PPT* \\-3\\.31%, vol 4\\.2 M, vol chg \\+161\\.37%' in message
        assert 'Weakest 24h: *Binance Buildkey TGE*' in message
        assert '• *RIVER* \\-7\\.25%, vol 3\\.6 M, vol chg \\+161\\.37%' in message

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
                changes=[
                    ('BLY', 391.81, 24_000_000),
                    ('CXO', 18.4, 12_000_000),
                    ('PPT', 0.24, 900_000),
                    ('TRAC', 0.11, 700_000),
                ],
            ),
            'music': self.build_sector_detail(
                sector_id='music',
                title='Music',
                changes=[
                    ('ARIAIP', 30.17, 1_500_000),
                    ('VOISE', 15.84, 1_200_000),
                    ('ARTX', -13.75, 980_000),
                    ('DMCC', 15.36, 850_000),
                ],
            ),
        }

        message_sender = self.build_message_sender()
        message_sender.sentiment_service.get_crypto_fear_greed_index = AsyncMock(
            return_value=self.sentiment
        )
        message_sender.cmc_service.get_sectors_24h_change = AsyncMock(
            side_effect=[[strongest_sector], [weakest_sector]]
        )
        message_sender.cmc_service.get_spotlight = AsyncMock(return_value=self.spotlight)
        message_sender.cmc_service.get_coin_detail = AsyncMock(return_value=self.coin_detail)
        message_sender.cmc_service.get_sector_detail = AsyncMock(
            side_effect=[
                sector_details['logistics'],
                sector_details['music'],
            ]
        )

        messages = await message_sender.format_message()

        assert len(messages) == 1
        message = messages[0]
        assert 'leaders BLY, CXO' in message
        assert 'losers PPT, TRAC' not in message
        assert 'leaders ARIAIP, VOISE' in message
        assert 'losers ARTX' in message
        assert 'DMCC' not in message
        assert 'Losers BLY' not in message
        assert message.index('• *ARTX* \\-13\\.75%') < message.index(
            '• *ARIAIP* \\+30\\.17%'
        )
        assert '• *ARTX* \\-13\\.75%' in message
        assert 'vol chg \\+161\\.37%' in message

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

        message_sender = self.build_message_sender()
        message_sender.sentiment_service.get_crypto_fear_greed_index = AsyncMock(
            return_value=self.sentiment
        )
        message_sender.cmc_service.get_sectors_24h_change = AsyncMock(
            side_effect=[[strongest_sector], [weakest_sector]]
        )
        message_sender.cmc_service.get_spotlight = AsyncMock(return_value=self.spotlight)
        message_sender.cmc_service.get_coin_detail = AsyncMock(
            side_effect=RuntimeError('coin detail unavailable')
        )
        message_sender.cmc_service.get_sector_detail = AsyncMock(
            side_effect=RuntimeError('403 Forbidden')
        )

        messages = await message_sender.format_message()

        assert len(messages) == 1
        message = messages[0]
        assert 'Strongest 24h: *Video*' in message
        assert '; leaders ' not in message
        assert '; losers ' not in message
        assert '*Hifi Finance*' in message
        assert 'volume 1\\.26 B' in message
        assert 'volume change ' not in message
        assert '*Sector detail*' not in message

    @pytest.mark.asyncio
    async def test_format_message_uses_clean_decimal_price_formatting(self):
        spotlight = copy.deepcopy(self.spotlight)
        spotlight.trendingList[1].priceChange.price = 0.14879999999999999
        spotlight.gainerList[1].priceChange.price = 0.0569
        spotlight.loserList[1].priceChange.price = 0.12529999999999999

        strongest_sector = copy.deepcopy(self.sector_24h_change[0])
        strongest_sector.sectorId = 'video'
        strongest_sector.title = 'Video'

        weakest_sector = copy.deepcopy(self.sector_24h_change[0])
        weakest_sector.sectorId = 'music'
        weakest_sector.title = 'Music'
        weakest_sector.avgPriceChange = -7.4

        message_sender = self.build_message_sender()
        message_sender.sentiment_service.get_crypto_fear_greed_index = AsyncMock(
            return_value=self.sentiment
        )
        message_sender.cmc_service.get_sectors_24h_change = AsyncMock(
            side_effect=[[strongest_sector], [weakest_sector]]
        )
        message_sender.cmc_service.get_spotlight = AsyncMock(return_value=spotlight)
        message_sender.cmc_service.get_coin_detail = AsyncMock(return_value=self.coin_detail)
        message_sender.cmc_service.get_sector_detail = AsyncMock(
            side_effect=RuntimeError('403 Forbidden')
        )

        messages = await message_sender.format_message()

        assert len(messages) == 1
        message = messages[0]
        assert 'price 0\\.1488' in message
        assert 'price 0\\.0569' in message
        assert 'price 0\\.1253' in message
        assert '999999' not in message

    @pytest.mark.asyncio
    async def test_format_message_alerts_admin_when_snapshot_persistence_fails(
        self,
        monkeypatch,
    ):
        strongest_sector = copy.deepcopy(self.sector_24h_change[0])
        strongest_sector.sectorId = 'video'

        weakest_sector = copy.deepcopy(self.sector_24h_change[0])
        weakest_sector.sectorId = 'defi'
        weakest_sector.title = 'DeFi'
        weakest_sector.avgPriceChange = -4.8

        send_message_to_admin = AsyncMock()
        monkeypatch.setattr(
            'src.job.crypto.crypto_digest_message_sender.send_message_to_admin',
            send_message_to_admin,
        )

        message_sender = self.build_message_sender()
        message_sender.signal_repository.save_snapshot.side_effect = RuntimeError(
            'sqlite unavailable'
        )
        message_sender.sentiment_service.get_crypto_fear_greed_index = AsyncMock(
            return_value=self.sentiment
        )
        message_sender.cmc_service.get_sectors_24h_change = AsyncMock(
            side_effect=[[strongest_sector], [weakest_sector]]
        )
        message_sender.cmc_service.get_spotlight = AsyncMock(return_value=self.spotlight)
        message_sender.cmc_service.get_coin_detail = AsyncMock(return_value=self.coin_detail)
        message_sender.cmc_service.get_sector_detail = AsyncMock(
            side_effect=RuntimeError('403 Forbidden')
        )

        messages = await message_sender.format_message()

        assert len(messages) == 1
        send_message_to_admin.assert_awaited_once()
        assert 'sqlite unavailable' in send_message_to_admin.await_args.kwargs['message']
