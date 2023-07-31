import datetime
from unittest.mock import patch, AsyncMock, Mock

import pytest

from src.job.stocks.tradingview_message_sender import TradingViewMessageSender
from src.service.barchart import BarchartService
from src.service.tradingview_service import TradingViewService
from src.type.trading_view import TradingViewRedisData, TradingViewData, TradingViewStocksData, TradingViewDataType


class TestTradingviewMessageSender:
    @patch('src.job.stocks.tradingview_message_sender.config.get_is_testing_telegram')
    @pytest.mark.parametrize(
        'data, num_days_range, expected',
        [
            (None, (5, 15), None),
            ([], (5, 15), None),
            ([10], (5, 15), None),
            # current day volume is the smallest
            ([1, 3], (2, 6), None),
            # current day volume needs to be greater than consecutive past days
            ([10, 3, 2, 11, 5, 7], (2, 6), 3),
            # data has more elements than min days
            ([10, 3, 5, 6, 7, 8, 2, 1, 13, 12], (2, 6), 6),
            # data has fewer elements than min days
            ([10, 3, 5, 6, 7, 8, 2, 1, 13, 12], (11, 15), None),
        ]
    )
    def test_get_current_data_highest_volume_info_prod_mode(self, is_testing_telegram, data, num_days_range, expected):
        is_testing_telegram.return_value = False
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
            # current day volume needs to be greater than consecutive past days
            ([10, 3, 2, 11, 5, 7], (2, 6), 3),
            # data has more elements than min days
            ([10, 3, 5, 6, 7, 8, 2, 1, 13, 12], (2, 6), 6),
            # data has fewer elements than min days
            ([10, 3, 5, 6, 7, 8, 2, 1, 13, 12], (11, 15), 8),
        ]
    )
    def test_get_current_data_highest_volume_info_test_mode(self, data, num_days_range, expected):
        tradingview_message_sender = TradingViewMessageSender()
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
    @patch('src.job.stocks.tradingview_message_sender.config.get_is_testing_telegram')
    async def test_format_tradingview_message(self, get_is_testing_telegram, get_barchart_service, get_tradingview_service, get_current_date_preserve_time, redis_score, curr_timestamp, is_empty):
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
        get_is_testing_telegram.return_value = False

        tradingview_message_sender = TradingViewMessageSender()
        tradingview_message_sender.tradingview_service.get_tradingview_daily_stocks_data = AsyncMock(side_effect=[stocks_data, economy_indicator_data])
        tradingview_message_sender.barchart_service.get_stock_price = AsyncMock(return_value=[
                {'symbol': 'SPY', 'date': '2023-06-09', 'open': 429.96, 'high': 431.99, 'low': 428.87, 'close': 429.9, 'volume': 85647200.0},
                {'symbol': 'SPY', 'date': '2023-06-08', 'open': 426.62, 'high': 429.6, 'low': 425.82, 'close': 429.13, 'volume': 61952800.0}
            ])
        res = await tradingview_message_sender.format_message()

        if is_empty:
            assert len(res) == 0
        else:
            assert len(res) > 0