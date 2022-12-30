import datetime
import asyncio
from typing import List
from src.third_party_service.vix_central import ThirdPartyVixCentralService
import src.util.date_util as date_util


class VixFuturesValue:
    def __init__(self, contango_single_day_decrease_alert_ratio = 0.4):
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
    def __init__(self, decrease_past_n_days = 5):
        # store values of the upcoming VIX futures(next month)
        # list of dict in reverse chronological order of current_date.
        self.vix_futures_values: List[VixFuturesValue] = []
        self.is_contango_decrease_for_past_n_days: bool = None
        self.contango_decrease_past_n_days = decrease_past_n_days

# TODO: test
class VixCentralService:
    VALUE_CAPACITY = 5
    # month of the vix futures we are interested in. e.g. "Jan"
    MONTH_OF_INTEREST = None
    CONTANGO_SINGLE_DAY_DECREASE_ALERT_RATIO = 0.01
    CONTANGO_DECREASE_PAST_N_DAYS = 1

    def __init__(self, third_party_service = ThirdPartyVixCentralService):
        self.third_party_service = third_party_service
        # stateful, cache results. Should be called at the end of the market hours. However, service is called during market hours,
        # then subsequent calls during that day won't return the most recent data until the next day
        self.recent_values: RecentVixFuturesValues = RecentVixFuturesValues(VixCentralService.CONTANGO_DECREASE_PAST_N_DAYS)

    async def cleanup(self):
        await self.third_party_service.cleanup()

    def clear_values(self):
        self.recent_values: RecentVixFuturesValues = RecentVixFuturesValues(VixCentralService.CONTANGO_DECREASE_PAST_N_DAYS)

    async def get_recent_values(self) -> RecentVixFuturesValues:
        coros = []
        values_length = len(self.recent_values.vix_futures_values)

        if values_length < VixCentralService.VALUE_CAPACITY:
            # clear values and rebuild it for simplicity, instead of continuing from where it left off
            self.clear_values()

            current = await self.third_party_service.get_current()
            self.recent_values.vix_futures_values.insert(0, self.current_to_vix_futures_value(current))

            historical_dates = []
            for i in range(0, VixCentralService.VALUE_CAPACITY - len(self.recent_values.vix_futures_values)):
                # subtract days from previously used historical date, or from current date
                reference_date = historical_dates[len(historical_dates) - 1] if len(historical_dates) > 0 else date_util.get_current_datetime()
                date = date_util.get_most_recent_non_weekend_or_today(reference_date - datetime.timedelta(days=1))

                historical_dates.append(date)
                coros.append(self.third_party_service.get_historical(date.strftime("%Y-%m-%d")))
            res = await asyncio.gather(*coros)

            for i in range(0, len(res)):
                self.recent_values.vix_futures_values.append(self.historical_to_vix_futures_value(historical=res[i], current_date=historical_dates[i]))
        else:
            most_recent_date = date_util.get_most_recent_non_weekend_or_today(date_util.get_current_datetime())
            most_recent_date_yyyy_mm_dd = f"{most_recent_date.year}-{str(most_recent_date.month).ljust(2, '0')}-{str(most_recent_date.day).ljust(2, '0')}"
            if self.recent_values.vix_futures_values[0] and most_recent_date_yyyy_mm_dd == self.recent_values.vix_futures_values[0].current_date:
                print('Most recent VIX futures data is already fetched')
                return self.recent_values

            # remove oldest value, add current value
            self.recent_values.vix_futures_values.pop()
            current = await self.third_party_service.get_current()
            self.recent_values.vix_futures_values.insert(0, self.current_to_vix_futures_value(current))

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
                is_decrease_for_past_n_days = True
            decrease_counter += 1
        # No previous item to compare to, so it is always false
        recent_values.vix_futures_values[len(recent_values.vix_futures_values) - 1].is_contango_single_day_decrease_alert = False

        recent_values.is_contango_decrease_for_past_n_days = is_decrease_for_past_n_days
        return recent_values

    def current_to_vix_futures_value(self, current) -> VixFuturesValue:
        current_months = current[0]
        current_last_prices = current[2]
        if VixCentralService.MONTH_OF_INTEREST is None:
            VixCentralService.MONTH_OF_INTEREST = current_months[0]
        month_of_interest = current_months[0]
        contango = self.calculate_contango(current_last_prices[0], current_last_prices[1])

        ret_val = VixFuturesValue(contango_single_day_decrease_alert_ratio=VixCentralService.CONTANGO_SINGLE_DAY_DECREASE_ALERT_RATIO)
        ret_val.current_date = date_util.get_most_recent_non_weekend_or_today(date_util.get_current_datetime()).strftime("%Y-%m-%d")
        ret_val.futures_date = self.format_futures_date(month_of_interest)
        ret_val.futures_value = current_last_prices[0]
        ret_val.next_month_futures_value = current_last_prices[1]
        ret_val.raw_contango = contango
        ret_val.formatted_contango = f"{contango:.2%}"
        ret_val.is_contango_single_day_decrease_alert = False
        return ret_val

    def historical_to_vix_futures_value(self, historical, current_date: datetime.datetime) -> VixFuturesValue:
        contango = self.calculate_contango(historical[1], historical[2])

        ret_val = VixFuturesValue(contango_single_day_decrease_alert_ratio=VixCentralService.CONTANGO_SINGLE_DAY_DECREASE_ALERT_RATIO)
        ret_val.current_date = date_util.get_most_recent_non_weekend_or_today(current_date).strftime("%Y-%m-%d")
        ret_val.futures_date = self.format_futures_date(VixCentralService.MONTH_OF_INTEREST)
        ret_val.futures_value = historical[1]
        ret_val.next_month_futures_value = historical[2]
        ret_val.raw_contango = contango
        ret_val.formatted_contango = f"{contango:.2%}"
        ret_val.is_contango_single_day_decrease_alert = False
        return ret_val

    def calculate_contango(self, first: float, second: float) -> float:
        return (second / first) - 1

    # month: mmm. e.g. Jan
    def format_futures_date(self, month: str) -> str:
        current_date = date_util.get_current_datetime()
        year = current_date.year
        if current_date.month == 12:
            year += 1
        return f"{year} {month}"
