from unittest.mock import patch, AsyncMock, Mock

import pytest

from src.job.stocks.tradingview_message_sender import TradingViewMessageSender
from src.service.barchart import BarchartService
from src.service.tradingview_service import TradingViewService


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
            # current day volume no need to be greater than consecutive past days
            ([10, 3, 2, 11, 5, 7], (2, 6), 5),
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
    @patch('src.job.stocks.tradingview_message_sender.Dependencies.get_tradingview_service')
    @patch('src.job.stocks.tradingview_message_sender.Dependencies.get_barchart_service')
    async def test_format_tradingview_message(self, get_barchart_service_mock, get_tradingview_service_mock):
        stocks_data = {
            'data': {
                'data': [
                    { 'symbol': 'SPY', 'close_prices': [10, 11, 12], 'ema20s': [10, 11, 12], 'volumes': [100, 200, 300] },
                    { 'symbol': 'AMD', 'close_prices': [11, 12, 13], 'ema20s': [11, 12, 13], 'volumes': [100, 200, 300] }
                ],
            },
            'score': 1686392956
        }
        economy_indicator_data = {
            'data': {
                'data': [
                    {'symbol': 'VIX', 'close_prices': [25, 18, 20]},
                    {'symbol': 'SKEW', 'close_prices': [11, 12, 13]}
                ]
            },
            'score': 1686392956
        }
        # get_tradingview_service_mock.return_value = TradingViewService()
        # get_barchart_service_mock.return_value = BarchartService()

        tradingview_message_sender = TradingViewMessageSender()
        tradingview_message_sender.tradingview_service.get_tradingview_daily_stocks_data = AsyncMock(side_effect=[stocks_data, economy_indicator_data])
        tradingview_message_sender.barchart_service.get_stock_price = AsyncMock(return_value='''SPY,2023-06-09,429.96,431.99,428.87,429.9,85647200
SPY,2023-06-08,426.62,429.6,425.82,429.13,61952800
SPY,2023-06-07,428.44,429.62,426.11,426.55,85373200
SPY,2023-06-06,426.67,428.5772,425.99,428.03,64022100
SPY,2023-06-05,428.28,429.62,426.37,427.1,65460100
''')
        res = await tradingview_message_sender.format_message()

        assert res is not None and len(res) > 0