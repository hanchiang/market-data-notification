import asyncio
import json
import os
from unittest.mock import Mock, AsyncMock

import pytest

from src.dependencies import Dependencies
from src.service.messari import MessariService


class TestMessariService:
    def setup_method(self):
        self.messari_service = MessariService(third_party_service=Dependencies.get_thirdparty_messari_service())
        dir_path = os.path.dirname(os.path.realpath(__file__))
        file_path = os.path.join(dir_path, '..',  '..', 'data', 'messari', 'asset_metrics.json')
        self.asset_metrics = json.load(open(file_path))

    @classmethod
    def setup_class(cls):
        asyncio.run(Dependencies.build())

    @classmethod
    def teardown_class(cls):
        asyncio.run(Dependencies.cleanup())

    @pytest.mark.asyncio
    async def test_get_asset_metrics_transform_and_sort(self):
        self.messari_service.third_party_service.get_metrics = AsyncMock(return_value=self.asset_metrics)

        metrics = await self.messari_service.get_asset_metrics(symbol='BTC')

        expected_exchange_supply = {
            'Total': {
                'usd': 20518558275.015102,
                'quantity': 768160.97457554
            },
            'Binance': {
                'usd': 13690505582.36799,
                'quantity': 512581.1628213
            },
            'Bitfinex': {
                'usd': 6828052692.647112,
                'quantity': 255579.81175424
            }
        }

        expected_exchange_netflow = {
            'Total': {
                'usd': -8454983.40114464,
                'quantity': -314.84372045
            },
            'Binance': {
                'usd': -9598667.656584863,
                'quantity': -357.43183552
            },
            'Bitfinex': {
                'usd': 1143684.2554402228,
                'quantity': 42.58811507
            }
        }

        assert metrics.symbol == 'BTC'
        assert metrics.slug == 'bitcoin'
        assert metrics.price_usd == 27211.96317732537
        assert metrics.exchange_supply == expected_exchange_supply
        assert metrics.exchange_net_flows == expected_exchange_netflow


