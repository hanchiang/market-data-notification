import asyncio
import datetime
from unittest.mock import Mock, patch, AsyncMock

import pytest

from src.dependencies import Dependencies
from src.service.vix_central import VixCentralService, RecentVixFuturesValues, VixFuturesValue

class TestVixCentralService:
    CONTANGO_SINGLE_DAY_DECREASE_ALERT_RATIO = 0.2
    VIX_CENTRAL_NUMBER_OF_DAYS = 2

    current_vix_futures = [
        [
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug"
        ],
        [
            " ",
            " ",
            " ",
            " ",
            " ",
            " ",
            " ",
            " "
        ],
        [
            23.35,
            24.6,
            25.4,
            25.95,
            26.3,
            26.5,
            26.9,
            26.8
        ],
        [
            23.15,
            24.46,
            25.3,
            25.85,
            26.23,
            26.43,
            26.85,
            0.0
        ],
        [
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0
        ],
        [
            23.4,
            24.65,
            25.41,
            25.95,
            26.3,
            26.5,
            26.9,
            0.0
        ],
        [
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0
        ],
        [
            23.06,
            24.42,
            25.26,
            25.81,
            26.22,
            26.41,
            26.85,
            0.0
        ],
        [
            21.91,
            21.91,
            21.91,
            21.91,
            21.91,
            21.91,
            21.91,
            21.91
        ],
        [
            23.95,
            23.95,
            23.95,
            23.95,
            23.95,
            23.95,
            23.95,
            23.95
        ],
        [
            20.49,
            20.49,
            20.49,
            20.49,
            20.49,
            20.49,
            20.49,
            20.49
        ],
        [
            25.99,
            25.99,
            25.99,
            25.99,
            25.99,
            25.99,
            25.99,
            25.99
        ],
        [
            20.49,
            21.91,
            23.95,
            25.99
        ],
        [
            21.95,
            21.95,
            21.95,
            21.95,
            21.95,
            21.95,
            21.95,
            21.95
        ],
        []
    ]

    historical_vix_futures = [
        12,
        22.3075,
        24.62,
        25.4344,
        25.9043,
        26.342,
        26.4071,
        26.5481,
        27.0,
        26.925
    ]

    def setup_method(self):
        self.vix_central_service = VixCentralService(number_of_days_to_store=TestVixCentralService.VIX_CENTRAL_NUMBER_OF_DAYS)

    @classmethod
    def setup_class(cls):
        asyncio.run(Dependencies.build())

    @classmethod
    def teardown_class(cls):
        asyncio.run(Dependencies.cleanup())

    def test_compute_contango_alert_threshold_no_vix_futures_values(self):
        recent_vix_futures_values = RecentVixFuturesValues(decrease_past_n_days=2)

        recent_vix_futures_values.vix_futures_values = []

        result = self.vix_central_service._compute_contango_alert_threshold(recent_vix_futures_values)
        assert result.is_contango_decrease_for_past_n_days == False

    def test_compute_contango_alert_threshold_correct_decrease_past_n_days_and_single_day_decrease(self):
        recent_vix_futures_values = RecentVixFuturesValues(decrease_past_n_days=2)

        vix_futures_values = []

        v1 = VixFuturesValue(contango_single_day_decrease_alert_ratio=TestVixCentralService.CONTANGO_SINGLE_DAY_DECREASE_ALERT_RATIO)
        v1.current_date = '2020-12-31'
        v1.futures_date = '2022 Jan'
        v1.futures_value = 23
        v1.next_month_futures_value = 24
        # 0.04347826086956519
        v1.raw_contango = (v1.next_month_futures_value / v1.futures_value) - 1
        v1.formatted_contango = f"{v1.raw_contango:.2%}"

        v2 = VixFuturesValue(contango_single_day_decrease_alert_ratio=TestVixCentralService.CONTANGO_SINGLE_DAY_DECREASE_ALERT_RATIO)
        v2.current_date = '2020-12-30'
        v2.futures_date = '2022 Jan'
        v2.futures_value = 15
        v2.next_month_futures_value = 16
        # 0.06666666666666665
        v2.raw_contango = (v2.next_month_futures_value / v2.futures_value) - 1
        v2.formatted_contango = f"{v2.raw_contango:.2%}"

        v3 = VixFuturesValue(contango_single_day_decrease_alert_ratio=TestVixCentralService.CONTANGO_SINGLE_DAY_DECREASE_ALERT_RATIO)
        v3.current_date = '2020-12-29'
        v3.futures_date = '2022 Jan'
        v3.futures_value = 13
        v3.next_month_futures_value = 14
        # 0.07692307692307687
        v3.raw_contango = (v3.next_month_futures_value / v3.futures_value) - 1
        v3.formatted_contango = f"{v3.raw_contango:.2%}"

        vix_futures_values.append(v1)
        vix_futures_values.append(v2)
        vix_futures_values.append(v3)

        recent_vix_futures_values.vix_futures_values = vix_futures_values

        result = self.vix_central_service._compute_contango_alert_threshold(recent_vix_futures_values)
        
        assert result.vix_futures_values[0].is_contango_single_day_decrease_alert == True
        assert result.vix_futures_values[1].is_contango_single_day_decrease_alert == False
        assert result.vix_futures_values[2].is_contango_single_day_decrease_alert == False
        assert result.is_contango_decrease_for_past_n_days == True

    def test_compute_contango_negative_value(self):
        recent_vix_futures_values = RecentVixFuturesValues(decrease_past_n_days=2)

        v1 = VixFuturesValue(contango_single_day_decrease_alert_ratio=TestVixCentralService.CONTANGO_SINGLE_DAY_DECREASE_ALERT_RATIO)
        v1.raw_contango = -5
        v1.formatted_contango = "-50%"

        v2 = VixFuturesValue(contango_single_day_decrease_alert_ratio=TestVixCentralService.CONTANGO_SINGLE_DAY_DECREASE_ALERT_RATIO)
        v2.raw_contango = -10
        v2.formatted_contango = "-100%"

        vix_futures_values = [v1, v2]
        recent_vix_futures_values.vix_futures_values = vix_futures_values

        # Call the method
        result = self.vix_central_service._compute_contango_alert_threshold(recent_vix_futures_values)

        # Verify the fix
        assert result.vix_futures_values[0].raw_contango_change_prev_day == 0.5, "Expected raw_contango_change_prev_day to be 0.5"

    @pytest.mark.asyncio
    @patch("asyncio.gather", new_callable=AsyncMock)
    async def test_get_recent_values_empty_state(self, asyncio_gather_mock: AsyncMock):
        thirdparty_vix_central_service = Dependencies.get_thirdparty_vix_central_service()
        thirdparty_vix_central_service.get_current = AsyncMock(return_value=self.current_vix_futures)
        thirdparty_vix_central_service.get_historical = Mock(return_value=self.historical_vix_futures)
        vix_central_service = VixCentralService(third_party_service=thirdparty_vix_central_service, number_of_days_to_store=TestVixCentralService.VIX_CENTRAL_NUMBER_OF_DAYS)

        vix_futures_values = VixFuturesValue()
        vix_futures_values.futures_value = self.historical_vix_futures[1]
        vix_futures_values.current_date = '2022-12-31'
        vix_futures_values.next_month_futures_value = self.historical_vix_futures[2]
        vix_futures_values.raw_contango = self.historical_vix_futures[2] / self.historical_vix_futures[1] - 1
        asyncio_gather_mock.return_value = [self.historical_vix_futures]

        result = await vix_central_service.get_recent_values()

        asyncio_gather_mock.assert_awaited()
        thirdparty_vix_central_service.get_current.assert_called()
        assert len(result.vix_futures_values) == 2

    @pytest.mark.asyncio
    @patch("asyncio.gather", new_callable=AsyncMock)
    @patch("src.service.vix_central.date_util.get_most_recent_non_weekend_or_today")
    async def test_get_recent_values_full_state_most_recent_value_already_retrieved(self, get_most_recent_non_weekend_or_today: Mock, asyncio_gather_mock: AsyncMock):
        thirdparty_vix_central_service = Dependencies.get_thirdparty_vix_central_service()

        thirdparty_vix_central_service.get_current = AsyncMock(return_value=self.current_vix_futures)
        thirdparty_vix_central_service.get_historical = Mock(return_value=self.historical_vix_futures)
        vix_central_service = VixCentralService(third_party_service=thirdparty_vix_central_service,
                                                number_of_days_to_store=TestVixCentralService.VIX_CENTRAL_NUMBER_OF_DAYS)

        vix_futures_values = VixFuturesValue()
        vix_futures_values.futures_value = self.historical_vix_futures[1]
        vix_futures_values.current_date = '2022-12-31'
        vix_futures_values.next_month_futures_value = self.historical_vix_futures[2]
        vix_futures_values.raw_contango = self.historical_vix_futures[2] / self.historical_vix_futures[1] - 1
        vix_central_service.recent_values.vix_futures_values = [{}, vix_futures_values]

        get_most_recent_non_weekend_or_today.return_value = datetime.datetime(2022, 12, 31)

        result = await vix_central_service.get_recent_values()

        asyncio_gather_mock.assert_not_awaited()
        thirdparty_vix_central_service.get_historical.assert_not_called()
        thirdparty_vix_central_service.get_current.assert_called_once()

        assert len(result.vix_futures_values) == 2

    def test_calculate_contango(self):
        assert self.vix_central_service._calculate_contango(23, 24) == 0.04347826086956519

    def test_clear_historical_values(self):
        recent_vix_futures_values = RecentVixFuturesValues()

        v1 = VixFuturesValue(
            contango_single_day_decrease_alert_ratio=TestVixCentralService.CONTANGO_SINGLE_DAY_DECREASE_ALERT_RATIO)
        v1.current_date = '2020-12-31'
        v1.futures_date = '2022 Jan'
        v1.futures_value = 23
        v1.next_month_futures_value = 24
        # 0.04347826086956519
        v1.raw_contango = (v1.next_month_futures_value / v1.futures_value) - 1
        v1.formatted_contango = f"{v1.raw_contango:.2%}"

        v2 = VixFuturesValue(
            contango_single_day_decrease_alert_ratio=TestVixCentralService.CONTANGO_SINGLE_DAY_DECREASE_ALERT_RATIO)
        v2.current_date = '2020-12-30'
        v2.futures_date = '2022 Jan'
        v2.futures_value = 15
        v2.next_month_futures_value = 16
        # 0.06666666666666665
        v2.raw_contango = (v2.next_month_futures_value / v2.futures_value) - 1
        v2.formatted_contango = f"{v2.raw_contango:.2%}"

        recent_vix_futures_values.vix_futures_values.append(v1)
        recent_vix_futures_values.vix_futures_values.append(v2)

        recent_vix_futures_values.clear_current_value()

        assert recent_vix_futures_values.vix_futures_values == [v2]
