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
        'redis_data, add_res, num_elements, trim_remove_res, input_data, test_mode, expected, use_pipeline',
        [
            ([[]], 1, 1, 0, 'data', False, [1, 0], True),
            ([], 1, 1, 0, 'data', False, [1, 0], False),
            ([], 1, 31, 1, 'data', False, [1, 1], False),
            ([[]], 1, 1, 0, 'data', True, [1, 0], False),
            ([[]], 1, 31, 1, 'data', True, [1, 1], False),
        ]
    )
    @patch("src.service.tradingview_service.Redis")
    @pytest.mark.asyncio
    async def test_save_tradingview_data(self, redis_mock, redis_data, add_res, num_elements, trim_remove_res, input_data, test_mode, expected, use_pipeline):
        pipeline = Mock()
        pipeline.zremrangebyscore = Mock(return_value=pipeline)
        pipeline.zadd = Mock(return_value=pipeline)
        pipeline.execute = AsyncMock(return_value=[1, add_res])

        redis_client = redis_mock.get_client.return_value
        redis_client.zrange = AsyncMock(return_value=redis_data)
        redis_client.zadd = AsyncMock(return_value=add_res)
        redis_client.zcard = AsyncMock(return_value=num_elements)
        redis_client.zremrangebyrank = AsyncMock(return_value=trim_remove_res)
        redis_client.pipeline = Mock(return_value=pipeline)

        res = await self.tradingview_service.save_tradingview_data(data=input_data, key='key', score=1, test_mode=test_mode)

        assert res == expected
        if use_pipeline:
            redis_client.pipeline.assert_called_once_with(transaction=True)
            pipeline.zremrangebyscore.assert_called_once_with('key', min=1, max=1)
            pipeline.zadd.assert_called_once_with('key', {'data': 1})
            pipeline.execute.assert_awaited_once()
            redis_client.zadd.assert_not_awaited()
        else:
            redis_client.pipeline.assert_not_called()
            redis_client.zadd.assert_awaited_once_with('key', {'data': 1})

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
        'type, expected',
        [
            (TradingViewDataType.STOCKS, 'tradingview-stocks'),
            (TradingViewDataType.ECONOMY_INDICATOR, 'tradingview-economy_indicator'),
        ]
    )
    def test_get_redis_key_for_stocks(self, type, expected):
        res = self.tradingview_service.get_redis_key_for_stocks(type=type)

        assert res == expected

    @pytest.mark.parametrize(
        'expected',
        [
            'tradingview-crypto',
        ]
    )
    def test_get_redis_key_for_crypto(self, expected):
        res = self.tradingview_service.get_redis_key_for_crypto()

        assert res == expected
