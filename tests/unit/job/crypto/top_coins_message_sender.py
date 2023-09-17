import asyncio
import dataclasses
import json
import os
from typing import List
from unittest.mock import Mock, AsyncMock

import typing

import pytest
from dacite import from_dict
from market_data_library.crypto.cmc.type import Sector24hChange, CoinDetail, CoinDetailStatistics, RelatedCoin, \
    RelatedExchange, CoinDetailWallet, CoinDetailHolder, FAQ, CryptoRating, Spotlight

from src.dependencies import Dependencies
from src.job.crypto.top_coins_message_sender import TopCoinsMessageSender
from src.job.crypto.top_sectors_message_sender import TopSectorsMessageSender
from src.service.crypto.crypto_stats import CryptoStatsService
from src.type.cmc import CMCSpotlightType


def remove_unknown_fields(my_value, fields: List[dataclasses.Field]):
    field_by_name = {field.name: field for field in fields}


    if isinstance(my_value, (str, int, bool, float)):
        return

    for key, value in list(my_value.items()):
        if key not in field_by_name:
            del my_value[key]
            continue

        field: dataclasses.Field = field_by_name[key]
        if isinstance(value, dict):
            remove_unknown_fields(value, dataclasses.fields(field.type))
        elif isinstance(value, list):
            for v in value:
                generic_type = typing.get_args(field.type)[0]
                if generic_type != typing.Any and not isinstance(generic_type(), (str, int, bool, float)):
                    remove_unknown_fields(v, dataclasses.fields(typing.get_args(field.type)[0]))

class TestTopSectorsMessageSender:
    def load_spotlight(self):
        dir_path = os.path.dirname(os.path.realpath(__file__))
        file_path = os.path.join(dir_path, '..', '..', '..', 'data', 'cmc', 'cmc_spotlight.json')
        data = json.load(open(file_path))

        self.spotlight = from_dict(data_class=Spotlight, data=data)

    def load_coin_detail(self):
        dir_path = os.path.dirname(os.path.realpath(__file__))
        file_path = os.path.join(dir_path, '..', '..', '..', 'data', 'cmc', 'coin_detail.json')
        data = json.load(open(file_path))

        fields = dataclasses.fields(CoinDetail)
        remove_unknown_fields(data, fields)

        coin_detail = CoinDetail(**data)

        coin_detail.statistics = CoinDetailStatistics(**data.get('statistics', {}))
        coin_detail.relatedCoins = list(map(
            lambda x: RelatedCoin(**x), data.get('relatedCoins', [])))
        coin_detail.relatedExchanges = list(map(lambda x: RelatedExchange(**x), data.get('relatedExchanges', [])))
        coin_detail.wallets = list(map(lambda x: CoinDetailWallet(**x), data.get('wallets', [])))
        coin_detail.holders = CoinDetailHolder(**data.get('holders', {}))
        coin_detail.faqDescription = list(map(lambda x: FAQ(**x), data.get('faqDescription', [])))
        coin_detail.cryptoRating = list(map(lambda x: CryptoRating(**x), data.get('cryptoRating', [])))

        self.coin_detail = coin_detail

    def setup_method(self):
        self.load_spotlight()
        self.load_coin_detail()

    @classmethod
    def setup_class(cls):
        asyncio.run(Dependencies.build())

    @classmethod
    def teardown_class(cls):
        asyncio.run(Dependencies.cleanup())

    @pytest.mark.asyncio
    @pytest.mark.parametrize('spotlight_type', [
        (CMCSpotlightType.TRENDING),
        (CMCSpotlightType.MOST_VISITED),
        (CMCSpotlightType.GAINER_LIST),
        (CMCSpotlightType.LOSER_LIST)
    ])
    async def test_format_message(self, spotlight_type):
        self.message_sender = TopCoinsMessageSender(spotlight_type=spotlight_type)

        get_spotlight = AsyncMock()
        get_spotlight.return_value = self.spotlight
        self.message_sender.cmc_service.get_spotlight = get_spotlight

        get_coin_detail = AsyncMock()
        get_coin_detail.return_value = self.coin_detail
        self.message_sender.cmc_service.get_coin_detail = get_coin_detail

        res = await self.message_sender.format_message()
        assert len(res) > 0

