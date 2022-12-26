import datetime
import asyncio
from typing import List, TypedDict
from src.third_party_service.vix_central import ThirdPartyVixCentralService
import src.util.date_util as date_util

class VixFuturesValue(TypedDict):
    # "yyyy-mm-dd". 2022-12-30
    current_date: str
    # "yyyy mmm". e.g. 2022 Jan
    futures_date: str
    # vix futures value
    futures_value: float
    # just for completeness and verification sake
    next_month_futures_value: float
    # e.g. 23.1
    raw_contango: float
    # in %. e.g. 5.11%
    formatted_contango: str

# TODO: test
class VixCentralService:
    VALUE_CAPACITY = 5
    MONTH_OF_INTEREST = None

    def __init__(self):
        self.third_party_service = ThirdPartyVixCentralService()
        # store value of the upcoming VIX futures(next month)
        # list of dict in reverse chronological order of current_date.
        self.values: List[VixFuturesValue] = []

    def clear_values(self):
        self.values: List[VixFuturesValue] = []

    async def get_recent_values(self):
        coros = []
        values_length = len(self.values)

        if values_length < VixCentralService.VALUE_CAPACITY:
            # clear values and rebuild it for simplicity, instead of continuing from where it left off
            self.clear_values()

            current = await self.third_party_service.get_current()
            self.values.insert(0, self.current_to_vix_futures_value(current))

            historical_dates = []
            for i in range(0, VixCentralService.VALUE_CAPACITY - len(self.values)):
                # subtract days from the previous used historical date, or from current date
                reference_date = historical_dates[len(historical_dates) - 1] if len(historical_dates) > 0 else date_util.get_current_datetime()
                date = date_util.get_most_recent_non_weekend_or_today(reference_date - datetime.timedelta(days=1))

                historical_dates.append(date)
                coros.append(self.third_party_service.get_historical(date.strftime("%Y-%m-%d")))
            res = await asyncio.gather(*coros)

            for i in range(0, len(res)):
                self.values.append(self.historical_to_vix_futures_value(historical=res[i], current_date=historical_dates[i]))

            return self.values
        else:
            most_recent_date = date_util.get_most_recent_non_weekend_or_today(date_util.get_current_datetime())
            most_recent_date_yyyy_mm_dd = f"{most_recent_date.year}-{str(most_recent_date.month).ljust(2, '0')}-{str(most_recent_date.day).ljust(2, '0')}"
            if most_recent_date_yyyy_mm_dd == self.values[0]['current_date']:
                print('Most recent VIX futures data is already fetched')
                return self.values

            # remove oldest value, add current value
            self.values.pop()
            current = await self.third_party_service.get_current()
            self.values.insert(0, self.current_to_vix_futures_value(current))
            return self.values

    def current_to_vix_futures_value(self, current):
        current_months = current[0]
        current_last_prices = current[2]
        if VixCentralService.MONTH_OF_INTEREST is None:
            VixCentralService.MONTH_OF_INTEREST = current_months[0]
        month_of_interest = current_months[0]
        contango = self.calculate_contango(current_last_prices[0], current_last_prices[1])
        return {
            "current_date": date_util.get_most_recent_non_weekend_or_today(date_util.get_current_datetime()).strftime("%Y-%m-%d"),
            "futures_date": self.format_futures_date(month_of_interest),
            "futures_value": current_last_prices[0],
            "next_month_futures_value": current_last_prices[1],
            "raw_contango": contango,
            "formatted_contango": f"{contango:.2%}"
        }

    def historical_to_vix_futures_value(self, historical, current_date: datetime.datetime):
        contango = self.calculate_contango(historical[1], historical[2])
        return {
            "current_date": date_util.get_most_recent_non_weekend_or_today(current_date).strftime("%Y-%m-%d"),
            "futures_date": self.format_futures_date(VixCentralService.MONTH_OF_INTEREST),
            "futures_value": historical[1],
            "next_month_futures_value": historical[2],
            "raw_contango": contango,
            "formatted_contango": f"{contango:.2%}"
        }

    def calculate_contango(self, val1: float, val2: float) -> float:
        return (val2 / val1) - 1

    # month: mmm. e.g. Jan
    def format_futures_date(self, month: str) -> str:
        current_date = date_util.get_current_datetime()
        year = current_date.year
        if current_date.month == 12:
            year += 1
        return f"{year} {month}"
