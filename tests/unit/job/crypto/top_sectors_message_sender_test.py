import asyncio
import dataclasses
import json
import os
from typing import List
from unittest.mock import AsyncMock

import typing

import pytest
from dacite import from_dict
from market_data_library.types import cmc_type

from src.dependencies import Dependencies
from src.job.crypto.top_sectors_message_sender import TopSectorsMessageSender



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
    def load_sector_24h_change(self):
        dir_path = os.path.dirname(os.path.realpath(__file__))
        file_path = os.path.join(dir_path, '..', '..', '..', 'data', 'cmc', 'sector_24h_change.json')
        data = json.load(open(file_path))

        sectors_24h_change = list(map(lambda x: from_dict(data_class=cmc_type.Sector24hChange, data=x), data))
        self.sector_24h_change = sectors_24h_change

    def load_coin_detail(self):
        dir_path = os.path.dirname(os.path.realpath(__file__))
        file_path = os.path.join(dir_path, '..', '..', '..', 'data', 'cmc', 'coin_detail.json')
        data = json.load(open(file_path))

        fields = dataclasses.fields(cmc_type.CoinDetail)
        remove_unknown_fields(data, fields)

        coin_detail = cmc_type.CoinDetail(**data)

        coin_detail.statistics = cmc_type.CoinDetailStatistics(**data.get('statistics', {}))
        coin_detail.relatedCoins = list(map(
            lambda x: cmc_type.RelatedCoin(**x), data.get('relatedCoins', [])))
        coin_detail.relatedExchanges = list(map(lambda x: cmc_type.RelatedExchange(**x), data.get('relatedExchanges', [])))
        coin_detail.wallets = list(map(lambda x: cmc_type.CoinDetailWallet(**x), data.get('wallets', [])))
        coin_detail.holders = cmc_type.CoinDetailHolder(**data.get('holders', {}))
        coin_detail.faqDescription = list(map(lambda x: cmc_type.FAQ(**x), data.get('faqDescription', [])))
        coin_detail.cryptoRating = list(map(lambda x: cmc_type.CryptoRating(**x), data.get('cryptoRating', [])))

        self.coin_detail = coin_detail

    def setup_method(self):
        self.load_sector_24h_change()
        self.load_coin_detail()

    @classmethod
    def setup_class(cls):
        asyncio.run(Dependencies.build())

    @classmethod
    def teardown_class(cls):
        asyncio.run(Dependencies.cleanup())

    @pytest.mark.asyncio
    async def test_format_message(self):
        self.message_sender = TopSectorsMessageSender()

        get_sectors_24h_change = AsyncMock()
        get_sectors_24h_change.return_value = self.sector_24h_change
        self.message_sender.cmc_service.get_sectors_24h_change = get_sectors_24h_change

        get_coin_detail = AsyncMock()
        get_coin_detail.return_value = self.coin_detail
        self.message_sender.cmc_service.get_coin_detail = get_coin_detail

        res = await self.message_sender.format_message()
        assert len(res) > 0

