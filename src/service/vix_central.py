import datetime
import asyncio
from typing import List
from src.config import config
from src.third_party_service.vix_central import ThirdPartyVixCentralService
from src.util import date_util

class VixFuturesValue:
    def __init__(self, contango_single_day_decrease_alert_ratio=config.get_contango_single_day_decrease_threshold_ratio()):
        # "yyyy-mm-dd". 2022-12-30
        self.current_date: str = None
        # "yyyy mmm". e.g. 2022 Jan
        self.futures_date: str = None
        # vix futures value
        self.futures_value: float = None
        # just for completeness and verification sake
        self.next_month_futures_value: float = None
        # e.g. 23.1
        self.raw_contango: float = None
        # in %. e.g. 5.11%
        self.formatted_contango: str = None
        self.is_contango_single_day_decrease_alert: bool = None
        self.contango_single_day_decrease_alert_ratio: float = contango_single_day_decrease_alert_ratio

class RecentVixFuturesValues:
    def __init__(self, decrease_past_n_days = config.get_contango_decrease_past_n_days_threshold()):
        # store values of the upcoming VIX futures(next month)
        # list of dict in reverse chronological order of current_date.
        self.vix_futures_values: List[VixFuturesValue] = []
        self.is_contango_decrease_for_past_n_days: bool = None
        self.contango_decrease_past_n_days = decrease_past_n_days

    def clear_current_value(self):
        if len(self.vix_futures_values) > 1:
            self.vix_futures_values.pop(0)

# TODO: test
class VixCentralService:
    # month of the vix futures we are interested in. e.g. "Jan"
    MONTH_OF_INTEREST = None

    def __init__(self, third_party_service = ThirdPartyVixCentralService, number_of_days_to_store = config.get_vix_central_number_of_days(),
                 contango_decrease_past_n_days_threshold = config.get_contango_decrease_past_n_days_threshold()):
        self.number_of_days_to_store = number_of_days_to_store
        self.contango_decrease_past_n_days_threshold = contango_decrease_past_n_days_threshold
        self.third_party_service = third_party_service
        self.recent_values: RecentVixFuturesValues = RecentVixFuturesValues(self.contango_decrease_past_n_days_threshold)

    async def cleanup(self):
        await self.third_party_service.cleanup()

    def clear_current_values(self):
        self.recent_values.clear_current_value()

    # Stateful: Cache historical results. Retrieves current result on demand
    async def get_recent_values(self) -> RecentVixFuturesValues:
        coros = []

        self.clear_current_values()

        current_date = date_util.get_most_recent_non_weekend_or_today(date_util.get_current_datetime())
        if len(self.recent_values.vix_futures_values) == self.number_of_days_to_store - 1:
            print('Refreshing current vix central data')
            current = await self.third_party_service.get_current()
            self.recent_values.vix_futures_values.insert(0, self.current_to_vix_futures_value(current, current_date=current_date))
        else:
            print('Retrieving current and historical vix central data')
            current = await self.third_party_service.get_current()
            self.recent_values.vix_futures_values.insert(0, self.current_to_vix_futures_value(current, current_date=current_date))
            historical_dates = []

            for i in range(0, self.number_of_days_to_store - len(self.recent_values.vix_futures_values)):
                # subtract days from previously used historical date, or from current date
                reference_date = historical_dates[len(historical_dates) - 1] if len(
                    historical_dates) > 0 else current_date
                date = date_util.get_most_recent_non_weekend_or_today(reference_date - datetime.timedelta(days=1))

                historical_dates.append(date)
                coros.append(self.third_party_service.get_historical(date.strftime("%Y-%m-%d")))
            res = await asyncio.gather(*coros)

            for i in range(0, len(res)):
                self.recent_values.vix_futures_values.append(
                    self.historical_to_vix_futures_value(historical=res[i], current_date=historical_dates[i]))

        return self.compute_contango_alert_threshold(self.recent_values)

    # Compute single day contango decrease and past n days decreaase
    def compute_contango_alert_threshold(self, recent_values: RecentVixFuturesValues):
        is_decrease_for_past_n_days = False
        decrease_counter = 0

        if len(recent_values.vix_futures_values) == 0:
            recent_values.is_contango_decrease_for_past_n_days = False
            return recent_values

        for i in range(0, len(recent_values.vix_futures_values) - 1):
            curr_contango = recent_values.vix_futures_values[i].raw_contango
            prev_contango = recent_values.vix_futures_values[i + 1].raw_contango
            delta_ratio = (curr_contango - prev_contango) / prev_contango
            if delta_ratio < 0 and abs(delta_ratio) >= recent_values.vix_futures_values[i].contango_single_day_decrease_alert_ratio:
                recent_values.vix_futures_values[i].is_contango_single_day_decrease_alert = True
            else:
                recent_values.vix_futures_values[i].is_contango_single_day_decrease_alert = False

            if decrease_counter < recent_values.contango_decrease_past_n_days and curr_contango < prev_contango:
                decrease_counter += 1
                if decrease_counter == recent_values.contango_decrease_past_n_days:
                    is_decrease_for_past_n_days = True

        # No previous item to compare to, so it is always false
        recent_values.vix_futures_values[len(recent_values.vix_futures_values) - 1].is_contango_single_day_decrease_alert = False

        recent_values.is_contango_decrease_for_past_n_days = is_decrease_for_past_n_days
        return recent_values

    def current_to_vix_futures_value(self, current, current_date: datetime.datetime) -> VixFuturesValue:
        current_months = current[0]
        current_last_prices = current[2]
        if VixCentralService.MONTH_OF_INTEREST is None:
            VixCentralService.MONTH_OF_INTEREST = current_months[0]
        month_of_interest = current_months[0]
        contango = self.calculate_contango(current_last_prices[0], current_last_prices[1])

        ret_val = VixFuturesValue(contango_single_day_decrease_alert_ratio=config.get_contango_single_day_decrease_threshold_ratio())
        ret_val.current_date = current_date.strftime("%Y-%m-%d")
        ret_val.futures_date = self.format_futures_date(month_of_interest, current_date)
        ret_val.futures_value = current_last_prices[0]
        ret_val.next_month_futures_value = current_last_prices[1]
        ret_val.raw_contango = contango
        ret_val.formatted_contango = f"{contango:.2%}"
        ret_val.is_contango_single_day_decrease_alert = False
        return ret_val

    def historical_to_vix_futures_value(self, historical, current_date: datetime.datetime) -> VixFuturesValue:
        contango = self.calculate_contango(historical[1], historical[2])

        ret_val = VixFuturesValue(contango_single_day_decrease_alert_ratio=config.get_contango_single_day_decrease_threshold_ratio())
        ret_val.current_date = current_date.strftime("%Y-%m-%d")
        ret_val.futures_date = self.format_futures_date(VixCentralService.MONTH_OF_INTEREST, current_date)
        ret_val.futures_value = historical[1]
        ret_val.next_month_futures_value = historical[2]
        ret_val.raw_contango = contango
        ret_val.formatted_contango = f"{contango:.2%}"
        ret_val.is_contango_single_day_decrease_alert = False
        return ret_val

    # the gradient of the next month futures compared to current
    def calculate_contango(self, first: float, second: float) -> float:
        return (second / first) - 1

    # month: mmm. e.g. Jan
    def format_futures_date(self, month: str, current_date: datetime.date) -> str:
        year = current_date.year
        if current_date.month == 12:
            year += 1
        return f"{year} {month}"
