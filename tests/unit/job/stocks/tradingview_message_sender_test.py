import datetime
from unittest.mock import patch, AsyncMock
from market_data_library.types import barchart_type

import pytest

from src.job.stocks.tradingview_message_sender import TradingViewMessageSender
from src.runtime.runtime_mode import RuntimeMode
from src.type.trading_view import TradingViewRedisData, TradingViewData, TradingViewStocksData, TradingViewDataType


class TestTradingviewMessageSender:
    @pytest.mark.parametrize(
        'data, num_days_range, expected',
        [
            (None, (5, 15), None),
            ([], (5, 15), None),
            ([10], (5, 15), None),
            # current day volume is the smallest
            ([1, 3], (2, 6), None),
            # current day volume greater than a few consecutive days
            ([10, 3, 2, 11, 5, 7], (2, 6), 3),
            # data has more elements than min days
            ([10, 3, 5, 6, 7, 8, 2, 1, 13, 12], (2, 6), 6),
            # data has fewer elements than min days
            ([10, 3, 5, 6, 7, 8, 2, 1, 13, 12], (11, 15), None),
        ]
    )
    def test_get_current_data_highest_volume_info_prod_mode(self, data, num_days_range, expected):
        tradingview_message_sender = TradingViewMessageSender()
        res = tradingview_message_sender.get_current_data_highest_volume_info(data_by_date=data, num_days_range=num_days_range)

        assert res == expected

    @pytest.mark.parametrize(
        'data, num_days_range, expected',
        [
            (None, (5, 15), None),
            ([], (5, 15), None),
            ([10], (5, 15), None),
            # current day volume is the smallest
            ([1, 3], (2, 6), 1),
            # current day volume is the smallest
            ([1, 3], (3, 6), None),
            # current day volume needs to be greater than consecutive past days
            ([10, 3, 2, 11, 5, 7], (2, 6), 3),
            # data has more elements than min days
            ([10, 3, 5, 6, 7, 8, 2, 1, 13, 12], (2, 6), 6),
            # data has fewer elements than min days
            ([10, 3, 5, 6, 7, 8, 2, 1, 13, 12], (11, 15), None),
        ]
    )
    def test_get_current_data_highest_volume_info_test_mode(self, data, num_days_range, expected):
        tradingview_message_sender = TradingViewMessageSender(
            runtime_mode=RuntimeMode.from_test_mode(True)
        )
        res = tradingview_message_sender.get_current_data_highest_volume_info(data_by_date=data,
                                                                              num_days_range=num_days_range)

        assert res == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        'redis_score, curr_timestamp, is_empty',
        # 1689685800: tuesday 13:10 UTC
        # 1689945000: friday 13:10 UTC
        [
            (
                    1689685800, 1689685800, False
            ),
            (
                    1689685800, 1689685800 + 86400, False
            ),
            # data saved on friday, try to retrieve on next monday
            (
                    1689945000, 1689945000 + 86400 + 86400 + 86400, False
            ),
            # data saved on friday, try to retrieve on sunday
            (
                    1689945000, 1689945000 + 86400 + 86400, False
            ),
            (
                    1689685800, 1689685800 + 86400 + 86401, True
            ),
        ]
    )
    @patch('src.job.stocks.tradingview_message_sender.get_current_date_preserve_time')
    @patch('src.job.stocks.tradingview_message_sender.Dependencies.get_tradingview_service')
    @patch('src.job.stocks.tradingview_message_sender.Dependencies.get_barchart_service')
    async def test_format_tradingview_message(self, get_barchart_service, get_tradingview_service, get_current_date_preserve_time, redis_score, curr_timestamp, is_empty):
        stocks_data = TradingViewRedisData(key='key', score=redis_score, data=TradingViewData(
                type=TradingViewDataType.STOCKS,
                unix_ms=1,
                data=[
                    TradingViewStocksData(symbol='SPY', timeframe='1D', close_prices=[10, 11, 12], ema20s=[10, 11, 12], volumes=[100, 200, 300]),
                    TradingViewStocksData(symbol='AMD', timeframe='1D', close_prices=[11, 12, 13], ema20s=[11, 12, 13],
                                          volumes=[100, 200, 300])
                ]
            ))
        economy_indicator_data = TradingViewRedisData(key='key', score=redis_score, data=TradingViewData(
                type=TradingViewDataType.ECONOMY_INDICATOR,
                unix_ms=1,
                data=[
                    TradingViewStocksData(symbol='VIX', timeframe='1D', close_prices=[25, 18, 20]),
                    TradingViewStocksData(symbol='SKEW', timeframe='1D', close_prices=[11, 12, 13])
                ]
            ))

        get_current_date_preserve_time.return_value = datetime.datetime.fromtimestamp(curr_timestamp, tz=datetime.timezone.utc)

        tradingview_message_sender = TradingViewMessageSender()
        tradingview_message_sender.tradingview_service.get_tradingview_daily_stocks_data = AsyncMock(side_effect=[stocks_data, economy_indicator_data])
        tradingview_message_sender.barchart_service.get_stock_price = AsyncMock(return_value=[
            barchart_type.StockPrice(symbol='SPY', date='2023-06-09', open_price=429.96, high_price=431.99, low_price=428.87, close_price=429.9, volume=85647200.0),
            barchart_type.StockPrice(symbol='SPY', date='2023-06-08', open_price=426.62, high_price=429.6, low_price=425.82, close_price=429.13, volume=61952800.0)
            ])
        res = await tradingview_message_sender.format_message()

        if is_empty:
            assert len(res) == 0
        else:
            assert len(res) > 0

    @pytest.mark.asyncio
    @patch('src.job.stocks.tradingview_message_sender.Dependencies.get_tradingview_service')
    @patch('src.job.stocks.tradingview_message_sender.Dependencies.get_barchart_service')
    async def test_format_tradingview_message_returns_empty_when_stocks_data_missing(self, get_barchart_service, get_tradingview_service):
        tradingview_message_sender = TradingViewMessageSender(
            runtime_mode=RuntimeMode.from_test_mode(True)
        )
        tradingview_message_sender.tradingview_service.get_tradingview_daily_stocks_data = AsyncMock(return_value=None)

        res = await tradingview_message_sender.format_message()

        assert res == []

    @pytest.mark.asyncio
    @patch('src.job.stocks.tradingview_message_sender.get_current_date_preserve_time')
    @patch('src.job.stocks.tradingview_message_sender.Dependencies.get_tradingview_service')
    @patch('src.job.stocks.tradingview_message_sender.Dependencies.get_barchart_service')
    async def test_format_tradingview_message_without_economy_indicator_data(
        self,
        get_barchart_service,
        get_tradingview_service,
        get_current_date_preserve_time,
    ):
        redis_score = 1689685800
        stocks_data = TradingViewRedisData(
            key='key',
            score=redis_score,
            data=TradingViewData(
                type=TradingViewDataType.STOCKS,
                unix_ms=1,
                data=[
                    TradingViewStocksData(
                        symbol='SPY',
                        timeframe='1D',
                        close_prices=[10, 11, 12],
                        ema20s=[10, 11, 12],
                        volumes=[100, 200, 300],
                    )
                ],
            ),
        )

        get_current_date_preserve_time.return_value = datetime.datetime.fromtimestamp(
            redis_score,
            tz=datetime.timezone.utc,
        )

        tradingview_message_sender = TradingViewMessageSender(
            runtime_mode=RuntimeMode.from_test_mode(True)
        )
        tradingview_message_sender.tradingview_service.get_tradingview_daily_stocks_data = AsyncMock(
            side_effect=[stocks_data, TradingViewRedisData(key='key', score=None, data=None)]
        )
        tradingview_message_sender.barchart_service.get_stock_price = AsyncMock(
            return_value=[
                barchart_type.StockPrice(
                    symbol='SPY',
                    date='2023-06-09',
                    open_price=429.96,
                    high_price=431.99,
                    low_price=428.87,
                    close_price=429.9,
                    volume=85647200.0,
                ),
                barchart_type.StockPrice(
                    symbol='SPY',
                    date='2023-06-08',
                    open_price=426.62,
                    high_price=429.6,
                    low_price=425.82,
                    close_price=429.13,
                    volume=61952800.0,
                ),
            ]
        )

        res = await tradingview_message_sender.format_message()

        assert len(res) == 1
        assert 'Trading view market data' in res[0]
        assert 'VIX' not in res[0]

    @pytest.mark.asyncio
    @patch('src.job.stocks.tradingview_message_sender.get_current_date_preserve_time')
    @patch('src.job.stocks.tradingview_message_sender.Dependencies.get_tradingview_service')
    @patch('src.job.stocks.tradingview_message_sender.Dependencies.get_barchart_service')
    async def test_format_tradingview_message_sections_stocks_and_economy_indicators(
        self,
        get_barchart_service,
        get_tradingview_service,
        get_current_date_preserve_time,
        monkeypatch,
    ):
        redis_score = 1689685800
        stocks_data = TradingViewRedisData(
            key='key',
            score=redis_score,
            data=TradingViewData(
                type=TradingViewDataType.STOCKS,
                unix_ms=1,
                data=[
                    TradingViewStocksData(
                        symbol='SPY',
                        timeframe='1D',
                        close_prices=[10],
                        ema20s=[11],
                        volumes=[100],
                    ),
                    TradingViewStocksData(
                        symbol='QQQ',
                        timeframe='1D',
                        close_prices=[12],
                        ema20s=[12.5],
                        volumes=[110],
                    ),
                    TradingViewStocksData(
                        symbol='AMD',
                        timeframe='1D',
                        close_prices=[20],
                        ema20s=[18],
                        volumes=[120],
                    ),
                    TradingViewStocksData(
                        symbol='AMZN',
                        timeframe='1D',
                        close_prices=[30],
                        ema20s=[29.7],
                        volumes=[130],
                    ),
                    TradingViewStocksData(
                        symbol='META',
                        timeframe='1D',
                        close_prices=[40],
                        ema20s=[38],
                        volumes=[140],
                    ),
                    TradingViewStocksData(
                        symbol='TSLA',
                        timeframe='1D',
                        close_prices=[50],
                        ema20s=[49.5],
                        volumes=[150],
                    ),
                ],
            ),
        )
        economy_indicator_data = TradingViewRedisData(
            key='key',
            score=redis_score,
            data=TradingViewData(
                type=TradingViewDataType.ECONOMY_INDICATOR,
                unix_ms=1,
                data=[
                    TradingViewStocksData(symbol='VIX', timeframe='1D', close_prices=[25]),
                    TradingViewStocksData(symbol='SKEW', timeframe='1D', close_prices=[142]),
                ],
            ),
        )

        get_current_date_preserve_time.return_value = datetime.datetime.fromtimestamp(
            redis_score,
            tz=datetime.timezone.utc,
        )
        monkeypatch.setattr(
            'src.job.stocks.tradingview_message_sender.config.get_should_compare_stocks_volume_rank',
            lambda: False,
        )

        tradingview_message_sender = TradingViewMessageSender(
            runtime_mode=RuntimeMode.from_test_mode(True)
        )
        tradingview_message_sender.tradingview_service.get_tradingview_daily_stocks_data = AsyncMock(
            side_effect=[stocks_data, economy_indicator_data]
        )

        res = await tradingview_message_sender.format_message()

        assert len(res) == 1
        assert '*Indices*' in res[0]
        assert '*Other tracked names*' in res[0]
        assert '*Economy indicators*' in res[0]
        assert '\n\n*Economy indicators*' in res[0]
        assert 'Below ema20' in res[0] or 'At/above ema20' in res[0]

    @pytest.mark.asyncio
    @patch('src.job.stocks.tradingview_message_sender.get_current_date_preserve_time')
    @patch('src.job.stocks.tradingview_message_sender.Dependencies.get_tradingview_service')
    @patch('src.job.stocks.tradingview_message_sender.Dependencies.get_barchart_service')
    async def test_format_tradingview_message_uses_lower_volume_threshold_in_test_mode(
        self,
        get_barchart_service,
        get_tradingview_service,
        get_current_date_preserve_time,
    ):
        redis_score = 1689685800
        stocks_data = TradingViewRedisData(
            key='key',
            score=redis_score,
            data=TradingViewData(
                type=TradingViewDataType.STOCKS,
                unix_ms=1,
                data=[
                    TradingViewStocksData(
                        symbol='SPY',
                        timeframe='1D',
                        close_prices=[10],
                        ema20s=[10],
                        volumes=[100],
                    ),
                    TradingViewStocksData(
                        symbol='AMD',
                        timeframe='1D',
                        close_prices=[20],
                        ema20s=[20],
                        volumes=[110],
                    ),
                ],
            ),
        )
        economy_indicator_data = TradingViewRedisData(
            key='key',
            score=redis_score,
            data=TradingViewData(
                type=TradingViewDataType.ECONOMY_INDICATOR,
                unix_ms=1,
                data=[],
            ),
        )

        get_current_date_preserve_time.return_value = datetime.datetime.fromtimestamp(
            redis_score,
            tz=datetime.timezone.utc,
        )

        tradingview_message_sender = TradingViewMessageSender(
            runtime_mode=RuntimeMode.from_test_mode(True)
        )
        tradingview_message_sender.tradingview_service.get_tradingview_daily_stocks_data = AsyncMock(
            side_effect=[stocks_data, economy_indicator_data]
        )
        tradingview_message_sender.barchart_service.get_stock_price = AsyncMock(
            side_effect=[
                [
                    barchart_type.StockPrice(
                        symbol='SPY',
                        date='2023-06-09',
                        open_price=1,
                        high_price=1,
                        low_price=1,
                        close_price=1,
                        volume=100,
                    ),
                    barchart_type.StockPrice(
                        symbol='SPY',
                        date='2023-06-08',
                        open_price=1,
                        high_price=1,
                        low_price=1,
                        close_price=1,
                        volume=95,
                    ),
                ],
                [
                    barchart_type.StockPrice(
                        symbol='AMD',
                        date='2023-06-09',
                        open_price=1,
                        high_price=1,
                        low_price=1,
                        close_price=1,
                        volume=110,
                    ),
                    barchart_type.StockPrice(
                        symbol='AMD',
                        date='2023-06-08',
                        open_price=1,
                        high_price=1,
                        low_price=1,
                        close_price=1,
                        volume=100,
                    ),
                ],
            ]
        )

        res = await tradingview_message_sender.format_message()

        assert len(res) == 1
        assert 'Highest\\(' in res[0]
