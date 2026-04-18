import calendar
import datetime
import logging
from statistics import mean
from typing import List, Optional

import src.util.date_util as date_util
from market_data_library.types import cnn_type

from src.data_source.market_data_library import get_tradfi_api
from src.type.sentiment import FearGreedAverage, FearGreedData, FearGreedResult

logger = logging.getLogger("Stocks sentiment service")


class StocksSentimentService:
    def __init__(self):
        tradfi_api = get_tradfi_api()
        cnn_api = tradfi_api.cnn
        self.cnn_service = cnn_api.cnn_service
        self.cnc_type = cnn_type

    async def get_stocks_fear_greed_index(self) -> Optional[FearGreedResult]:
        fear_greed_res: cnn_type.CnnFearGreedIndex = (
            await self.get_stocks_fear_greed_index_from_source()
        )

        if (
            fear_greed_res is None
            or fear_greed_res.fear_and_greed is None
            or fear_greed_res.fear_and_greed_historical is None
        ):
            return None

        historical_data = fear_greed_res.fear_and_greed_historical.data
        logger.info("Retrieved %s historical fear and greed data points", len(historical_data))

        data = self.transform_data(data=fear_greed_res)
        average = self.transform_average(data=fear_greed_res)
        return FearGreedResult(data=data, average=average)

    def transform_data(self, data: cnn_type.CnnFearGreedIndex) -> List[FearGreedData]:
        historical_data = data.fear_and_greed_historical.data
        if len(historical_data) == 0:
            return []

        anchor_entry = historical_data[-1]
        anchor_date = self._entry_datetime(anchor_entry).date()

        parse_params = [
            ("Previous close", anchor_entry),
            (
                "Last week",
                self._get_entry_at_or_before(
                    historical_data,
                    anchor_date - datetime.timedelta(days=7),
                ),
            ),
            (
                "Last month",
                self._get_entry_at_or_before(
                    historical_data,
                    self._subtract_months(anchor_date, 1),
                ),
            ),
            (
                "Last 3 months",
                self._get_entry_at_or_before(
                    historical_data,
                    self._subtract_months(anchor_date, 3),
                ),
            ),
            (
                "Last year",
                self._get_entry_at_or_before(
                    historical_data,
                    self._subtract_years(anchor_date, 1),
                ),
            ),
        ]

        result: List[FearGreedData] = []

        for label, entry in parse_params:
            if entry is None:
                continue

            value = entry.y
            sentiment = self.cnn_service.map_fear_greed_to_text(value=value)
            parsed = FearGreedData(
                relative_date_text=label,
                date=self._entry_datetime(entry),
                value=value,
            )
            if sentiment is not None:
                parsed.sentiment_text = sentiment["text"]
                parsed.emoji = sentiment["emoji"]

            result.append(parsed)
        return result

    def transform_average(
        self,
        data: cnn_type.CnnFearGreedIndex,
    ) -> List[FearGreedAverage]:
        historical_data = data.fear_and_greed_historical.data
        if len(historical_data) == 0:
            return []

        anchor_date = self._entry_datetime(historical_data[-1]).date()
        average_params = [
            ("1 week", anchor_date - datetime.timedelta(days=7)),
            ("1 month", self._subtract_months(anchor_date, 1)),
            ("3 months", self._subtract_months(anchor_date, 3)),
            ("1 year", self._subtract_years(anchor_date, 1)),
        ]

        result: List[FearGreedAverage] = []

        for timeframe, window_start in average_params:
            entries_in_window = self._get_entries_in_window(
                historical_data=historical_data,
                window_start=window_start,
                anchor_date=anchor_date,
            )
            if len(entries_in_window) == 0:
                continue

            average_value = mean(entry.y for entry in entries_in_window)
            sentiment = self.cnn_service.map_fear_greed_to_text(value=average_value)
            parsed = FearGreedAverage(
                timeframe=timeframe,
                value=average_value,
            )
            if sentiment is not None:
                parsed.sentiment_text = sentiment["text"]
                parsed.emoji = sentiment["emoji"]

            result.append(parsed)

        return result

    def _get_entry_at_or_before(
        self,
        historical_data: List[cnn_type.FearAndGreedHistoricalData],
        target_date: datetime.date,
    ) -> Optional[cnn_type.FearAndGreedHistoricalData]:
        for entry in reversed(historical_data):
            if self._entry_datetime(entry).date() <= target_date:
                return entry
        return None

    def _get_entries_in_window(
        self,
        historical_data: List[cnn_type.FearAndGreedHistoricalData],
        window_start: datetime.date,
        anchor_date: datetime.date,
    ) -> List[cnn_type.FearAndGreedHistoricalData]:
        return [
            entry
            for entry in historical_data
            if window_start < self._entry_datetime(entry).date() <= anchor_date
        ]

    def _entry_datetime(
        self,
        entry: cnn_type.FearAndGreedHistoricalData,
    ) -> datetime.datetime:
        return date_util.parse_timestamp(entry.x / 1000)

    def _subtract_months(
        self,
        reference_date: datetime.date,
        months: int,
    ) -> datetime.date:
        month_index = reference_date.month - months
        year = reference_date.year + (month_index - 1) // 12
        month = ((month_index - 1) % 12) + 1
        day = min(reference_date.day, calendar.monthrange(year, month)[1])
        return datetime.date(year, month, day)

    def _subtract_years(
        self,
        reference_date: datetime.date,
        years: int,
    ) -> datetime.date:
        year = reference_date.year - years
        day = min(reference_date.day, calendar.monthrange(year, reference_date.month)[1])
        return datetime.date(year, reference_date.month, day)

    async def get_stocks_fear_greed_index_from_source(self) -> cnn_type.CnnFearGreedIndex:
        fear_greed_res: cnn_type.CnnFearGreedIndex = (
            await self.cnn_service.get_fear_greed_index()
        )
        return fear_greed_res
