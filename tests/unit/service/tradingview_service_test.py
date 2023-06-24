import asyncio
from unittest.mock import Mock, patch, AsyncMock

import pytest

from src.dependencies import Dependencies
from src.service.tradingview_service import TradingViewService
from src.type.trading_view import TradingViewDataType, TradingViewData, TradingViewStocksData, TradingViewRedisData


class TestTradingViewService:
    def setup_method(self):
        self.tradingview_service = TradingViewService()
    @classmethod
    def setup_class(cls):
        asyncio.run(Dependencies.build())

    @classmethod
    def teardown_class(cls):
        asyncio.run(Dependencies.cleanup())

    @pytest.mark.parametrize(
        'redis_data, expected',
        [
            (
                    [['{"type": "stocks", "unix_ms": 1, "data": [{"symbol": "SPY", "timeframe": "1D", "close_prices": [100, 200], "ema20s": [100, 200], "volumes": [1, 2] }] }', 1234567890]],
                    TradingViewRedisData(key='key', score=1234567890, data=TradingViewData(
                        type=TradingViewDataType.STOCKS,
                        unix_ms=1,
                        data=[
                            TradingViewStocksData(symbol='SPY', timeframe='1D', close_prices=[100, 200], ema20s=[100, 200], volumes=[1, 2])
                        ]
                    ))
            ),
            ([], TradingViewRedisData(key='key', score=None, data=None)
             ),
            (None, None)
        ]
    )
    @patch("src.service.tradingview_service.Redis")
    @pytest.mark.asyncio
    async def test_get_tradingview_daily_stocks_data(self, redis_mock, redis_data, expected):
        self.tradingview_service.get_redis_key_for_stocks = Mock(return_value='key')
        redis_mock.get_client.return_value.zrange = AsyncMock(return_value=redis_data)

        res = await self.tradingview_service.get_tradingview_daily_stocks_data(type=TradingViewDataType.STOCKS)
        assert res == expected


    @pytest.mark.parametrize(
        'redis_data, add_res, num_elements, remove_res, input_data, test_mode, expected',
        [
            ([[]], 1, 1, 0, 'data', False, [0, 0]),
            ([], 1, 1, 0, 'data', False, [1, 0]),
            ([], 1, 31, 1, 'data', False, [1, 1]),
            ([[]], 1, 1, 0, 'data', True, [1, 0]),
            ([[]], 1, 31, 1, 'data', True, [1, 1]),
        ]
    )
    @patch("src.service.tradingview_service.Redis")
    @pytest.mark.asyncio
    async def test_save_tradingview_data(self, redis_mock, redis_data, add_res, num_elements, remove_res, input_data, test_mode, expected):
        redis_mock.get_client.return_value.zrange = AsyncMock(return_value=redis_data)
        redis_mock.get_client.return_value.zadd = AsyncMock(return_value=add_res)
        redis_mock.get_client.return_value.zcard = AsyncMock(return_value=num_elements)
        redis_mock.get_client.return_value.zremrangebyrank = AsyncMock(return_value=remove_res)

        res = await self.tradingview_service.save_tradingview_data(data=input_data, key='key', score=1, test_mode=test_mode)

        assert res == expected

    @pytest.mark.parametrize(
        'data, expected',
        [
            ({ 'type': 'stocks', 'unix_ms': 1, 'data': [{'symbol': 'SPY', 'timeframe': '1d', 'close_prices': [100, 200], 'ema20s': [1, 2], 'volumes': [1, 2]}] },
             TradingViewData(type=TradingViewDataType.STOCKS, unix_ms=1, data=[TradingViewStocksData(symbol='SPY', timeframe='1d', close_prices=[100, 200], ema20s=[1, 2], volumes=[1, 2])])
             )
        ]
    )
    def test_hydrate_data_list(self, data, expected):
        res = self.tradingview_service.hydrate_tradingview_data(data=data)
        assert res == expected

    @pytest.mark.parametrize(
        'is_testing_telegram, type, expected',
        [
            (True, TradingViewDataType.STOCKS, 'tradingview-stocks-dev'),
            (True, TradingViewDataType.ECONOMY_INDICATOR, 'tradingview-economy_indicator-dev'),
            (False, TradingViewDataType.STOCKS, 'tradingview-stocks'),
            (False, TradingViewDataType.ECONOMY_INDICATOR, 'tradingview-economy_indicator'),
        ]
    )
    @patch('src.service.tradingview_service.config')
    def test_get_redis_key_for_stocks(self, mock_config, is_testing_telegram, type, expected):
        mock_config.get_is_testing_telegram.return_value = is_testing_telegram
        res = self.tradingview_service.get_redis_key_for_stocks(type=type)

        assert res == expected

    @pytest.mark.parametrize(
        'is_testing_telegram, expected',
        [
            (True, 'tradingview-crypto-dev'),
            (False, 'tradingview-crypto'),
        ]
    )
    @patch('src.service.tradingview_service.config')
    def test_get_redis_key_for_crypto(self, mock_config, is_testing_telegram, expected):
        mock_config.get_is_testing_telegram.return_value = is_testing_telegram
        res = self.tradingview_service.get_redis_key_for_crypto()

        assert res == expected