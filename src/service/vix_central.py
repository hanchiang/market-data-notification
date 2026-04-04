import asyncio
import copy
import datetime
import logging
import calendar
from typing import List
from src.config import config
from src.runtime.runtime_mode import DEFAULT_RUNTIME_MODE, RuntimeMode
from src.third_party_service.vix_central import ThirdPartyVixCentralService
from src.util import date_util

class VixFuturesValue:
    def __init__(self, contango_single_day_decrease_alert_ratio=None):
        if contango_single_day_decrease_alert_ratio is None:
            contango_single_day_decrease_alert_ratio = config.get_contango_single_day_decrease_threshold_ratio()
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
        # e.g. 0.012
        self.raw_contango_change_prev_day: float = None
        # e.g. 1.20%
        self.formatted_contango_change_prev_day: float = None
        self.is_contango_single_day_decrease_alert: bool = None
        self.contango_single_day_decrease_alert_ratio: float = contango_single_day_decrease_alert_ratio

class RecentVixFuturesValues:
    def __init__(self, decrease_past_n_days=None):
        if decrease_past_n_days is None:
            decrease_past_n_days = config.get_contango_decrease_past_n_days_threshold()
        # store values of the upcoming VIX futures(next month)
        # list of dict in reverse chronological order of current_date.
        self.vix_futures_values: List[VixFuturesValue] = []
        self.is_contango_decrease_for_past_n_days: bool = None
        self.contango_decrease_past_n_days_threshold = decrease_past_n_days
        self.actual_contango_decrease_past_n_days = None

    def clear_current_value(self):
        if len(self.vix_futures_values) > 1:
            self.vix_futures_values.pop(0)

logger = logging.getLogger('Vix central service')
class VixCentralService:
    def __init__(
        self,
        third_party_service=ThirdPartyVixCentralService,
        number_of_days_to_store=None,
        contango_decrease_past_n_days_threshold=None,
    ):
        if number_of_days_to_store is None:
            number_of_days_to_store = config.get_vix_central_number_of_days()
        if contango_decrease_past_n_days_threshold is None:
            contango_decrease_past_n_days_threshold = config.get_contango_decrease_past_n_days_threshold()
        self.number_of_days_to_store = number_of_days_to_store
        self.contango_decrease_past_n_days_threshold = contango_decrease_past_n_days_threshold
        self.third_party_service = third_party_service
        # most recent to least recent
        self.recent_values: RecentVixFuturesValues = RecentVixFuturesValues(self.contango_decrease_past_n_days_threshold)

    async def cleanup(self):
        await self.third_party_service.cleanup()

    def clear_current_values(self):
        self.recent_values.clear_current_value()

    # Stateful: Cache historical results. Retrieves current result on demand
    async def get_recent_values(
        self,
        runtime_mode: RuntimeMode | None = None,
    ) -> RecentVixFuturesValues:
        coros = []
        active_runtime_mode = (
            DEFAULT_RUNTIME_MODE if runtime_mode is None else runtime_mode
        )

        self.clear_current_values()

        current_date = date_util.get_most_recent_non_weekend_or_today(date_util.get_current_datetime())
        # historical data doesn't change, just need to fetch current data
        if len(self.recent_values.vix_futures_values) == self.number_of_days_to_store - 1:
            logger.info('Refreshing current vix central data')
            current = await self.third_party_service.get_current()
            # The live endpoint is the only place that exposes the provider's
            # front-month label directly, so use it as the anchor for the whole
            # recent series.
            current_contract_date = self._get_current_contract_date_from_provider(
                current[0][0],
                current_date,
            )
            self.recent_values.vix_futures_values.insert(
                0,
                self._current_to_vix_futures_value(
                    current,
                    current_date=current_date,
                    contract_date=current_contract_date,
                ),
            )
        else:
            logger.info('Retrieving current and historical vix central data')
            current = await self.third_party_service.get_current()
            # Historical rows do not include month labels, so they are mapped
            # relative to this current-contract anchor.
            current_contract_date = self._get_current_contract_date_from_provider(
                current[0][0],
                current_date,
            )
            self.recent_values.vix_futures_values.insert(
                0,
                self._current_to_vix_futures_value(
                    current,
                    current_date=current_date,
                    contract_date=current_contract_date,
                ),
            )
            historical_dates = []

            for _ in range(0, self.number_of_days_to_store - len(self.recent_values.vix_futures_values)):
                # subtract days from previously used historical date, or from current date
                reference_date = historical_dates[len(historical_dates) - 1] if len(
                    historical_dates) > 0 else current_date
                date = date_util.get_most_recent_non_weekend_or_today(reference_date - datetime.timedelta(days=1))

                historical_dates.append(date)
                coros.append(self.third_party_service.get_historical(date.strftime("%Y-%m-%d")))
            res = await asyncio.gather(*coros)

            for i in range(0, len(res)):
                self.recent_values.vix_futures_values.append(
                    self._historical_to_vix_futures_value(
                        historical=res[i],
                        current_date=historical_dates[i],
                        reference_current_date=current_date,
                        reference_current_contract_date=current_contract_date,
                    )
                )

        values_to_return = self.recent_values
        if active_runtime_mode.relax_thresholds:
            # Keep the cached series canonical so a test-mode call cannot leak
            # relaxed thresholds or replay shaping into later production calls.
            values_to_return = copy.deepcopy(self.recent_values)
            self._modify_contango_testing_mode(values_to_return)

        return self._compute_contango_alert_threshold(values_to_return)

    def _modify_contango_testing_mode(
        self,
        recent_values: RecentVixFuturesValues,
    ):
        next_month_futures_value = 50
        futures_value = 47
        diff = next_month_futures_value - futures_value
        threshold_ratio = config.get_contango_single_day_decrease_threshold_ratio(
            is_test_mode=True
        )
        for vix_futures_value in recent_values.vix_futures_values:
            vix_futures_value.futures_value = futures_value
            vix_futures_value.next_month_futures_value = next_month_futures_value
            vix_futures_value.raw_contango = self._calculate_contango(futures_value, next_month_futures_value)
            vix_futures_value.formatted_contango = f"{self._calculate_contango(futures_value, next_month_futures_value):.2%}"
            vix_futures_value.is_contango_single_day_decrease_alert = False
            vix_futures_value.contango_single_day_decrease_alert_ratio = threshold_ratio
            next_month_futures_value = futures_value
            futures_value -= diff

    # Compute single day contango decrease and past n days decreaase
    def _compute_contango_alert_threshold(self, recent_values: RecentVixFuturesValues):
        is_decrease_for_past_n_days = False
        decrease_counter = 0
        recent_values.actual_contango_decrease_past_n_days = None

        if len(recent_values.vix_futures_values) == 0:
            recent_values.is_contango_decrease_for_past_n_days = False
            return recent_values

        for i in range(0, len(recent_values.vix_futures_values) - 1):
            current_value = recent_values.vix_futures_values[i]
            previous_value = recent_values.vix_futures_values[i + 1]
            curr_contango = current_value.raw_contango
            prev_contango = previous_value.raw_contango

            if self._is_contract_roll_boundary(current_value, previous_value):
                # Do not compare contango across a front-month rollover. The
                # underlying contracts changed, so a day-over-day delta here
                # would be a false signal instead of a real contango move.
                current_value.raw_contango_change_prev_day = None
                current_value.formatted_contango_change_prev_day = None
                current_value.is_contango_single_day_decrease_alert = False
                if not is_decrease_for_past_n_days:
                    decrease_counter = 0
                continue

            delta_ratio = 0
            if prev_contango != 0:
                contango_change = (curr_contango - prev_contango) / abs(prev_contango)
                current_value.raw_contango_change_prev_day = contango_change
                current_value.formatted_contango_change_prev_day = f"{contango_change:.2%}"
                delta_ratio = (curr_contango - prev_contango) / prev_contango

            if delta_ratio < 0 and abs(delta_ratio) >= current_value.contango_single_day_decrease_alert_ratio:
                current_value.is_contango_single_day_decrease_alert = True
            else:
                current_value.is_contango_single_day_decrease_alert = False

            if curr_contango >= prev_contango:
                # set to a negative value because once the current value is more than the previous value,
                # is_decrease_for_past_n_days should not be True anymore
                if not is_decrease_for_past_n_days:
                    decrease_counter = 0
                continue
            else:
                decrease_counter += 1
                if decrease_counter >= recent_values.contango_decrease_past_n_days_threshold and not is_decrease_for_past_n_days:
                    is_decrease_for_past_n_days = True

        # For the last item, there is no previous item to compare to, so it is always false
        recent_values.vix_futures_values[len(recent_values.vix_futures_values) - 1].is_contango_single_day_decrease_alert = False

        recent_values.is_contango_decrease_for_past_n_days = is_decrease_for_past_n_days
        if is_decrease_for_past_n_days:
            recent_values.actual_contango_decrease_past_n_days = decrease_counter
        return recent_values

    def _current_to_vix_futures_value(
        self,
        current,
        current_date: datetime.datetime,
        contract_date: datetime.date,
    ) -> VixFuturesValue:
        # `ajax_update` is an untyped nested list. Index 2 holds the live last-price
        # strip, and the front-month plus next-month prices are the first two values.
        current_last_prices = current[2]
        contango = self._calculate_contango(current_last_prices[0], current_last_prices[1])

        ret_val = VixFuturesValue(
            contango_single_day_decrease_alert_ratio=config.get_contango_single_day_decrease_threshold_ratio(
                is_test_mode=DEFAULT_RUNTIME_MODE.relax_thresholds
            )
        )
        ret_val.current_date = current_date.strftime("%Y-%m-%d")
        ret_val.futures_date = self._format_futures_date(contract_date)
        ret_val.futures_value = current_last_prices[0]
        ret_val.next_month_futures_value = current_last_prices[1]
        ret_val.raw_contango = contango
        ret_val.formatted_contango = f"{contango:.2%}"
        ret_val.is_contango_single_day_decrease_alert = False
        return ret_val

    def _historical_to_vix_futures_value(
        self,
        historical,
        current_date: datetime.datetime,
        reference_current_date: datetime.datetime,
        reference_current_contract_date: datetime.date,
    ) -> VixFuturesValue:
        # `ajax_historical` returns only numeric contract values, so historical
        # contract identity has to be inferred from the current provider month anchor.
        contango = self._calculate_contango(historical[1], historical[2])
        contract_date = self._infer_historical_contract_date(
            observation_date=current_date,
            reference_current_date=reference_current_date,
            reference_current_contract_date=reference_current_contract_date,
        )

        ret_val = VixFuturesValue(
            contango_single_day_decrease_alert_ratio=config.get_contango_single_day_decrease_threshold_ratio(
                is_test_mode=DEFAULT_RUNTIME_MODE.relax_thresholds
            )
        )
        ret_val.current_date = current_date.strftime("%Y-%m-%d")
        ret_val.futures_date = self._format_futures_date(contract_date)
        ret_val.futures_value = historical[1]
        ret_val.next_month_futures_value = historical[2]
        ret_val.raw_contango = contango
        ret_val.formatted_contango = f"{contango:.2%}"
        ret_val.is_contango_single_day_decrease_alert = False
        return ret_val

    # the gradient of the next month futures compared to current
    def _calculate_contango(self, first: float, second: float) -> float:
        return (second / first) - 1

    def _is_contract_roll_boundary(
        self,
        current_value: VixFuturesValue,
        previous_value: VixFuturesValue,
    ) -> bool:
        # Message formatting stores contract identity as "YYYY Mon". Parse both
        # sides back into normalized month-start dates before comparing them.
        current_contract = self._parse_futures_date(current_value.futures_date)
        previous_contract = self._parse_futures_date(previous_value.futures_date)
        if current_contract is None or previous_contract is None:
            return False
        return current_contract != previous_contract

    def _get_current_contract_date_from_provider(
        self,
        provider_front_month: str,
        observation_date: datetime.date | datetime.datetime,
    ) -> datetime.date:
        if isinstance(observation_date, datetime.datetime):
            observation_date = observation_date.date()

        # The live endpoint exposes the current front-month label directly.
        # Only the year still needs to be resolved locally: if the provider label
        # is earlier than the observation month (for example Jan vs Dec), it
        # refers to the next calendar year. Example: observation date 2022-12-31
        # with provider month "Jan" maps to the contract date 2023-01-01.
        contract_month = self._month_abbr_to_number(provider_front_month)
        contract_year = observation_date.year
        if contract_month < observation_date.month:
            contract_year += 1
        return datetime.date(contract_year, contract_month, 1)

    def _infer_historical_contract_date(
        self,
        observation_date: datetime.date | datetime.datetime,
        reference_current_date: datetime.date | datetime.datetime,
        reference_current_contract_date: datetime.date,
    ) -> datetime.date:
        if isinstance(observation_date, datetime.datetime):
            observation_date = observation_date.date()
        if isinstance(reference_current_date, datetime.datetime):
            reference_current_date = reference_current_date.date()

        if observation_date >= reference_current_date:
            return reference_current_contract_date

        # Walk backward one front-month window at a time until the observation
        # date lands inside the matching historical contract window. Example:
        # if the current row is the May contract and the historical observation
        # is before May's front-month start date, step back to April and repeat.
        contract_date = reference_current_contract_date
        while observation_date < self._get_front_month_start_date(contract_date):
            contract_date = self._shift_contract_date(contract_date, -1)
        return contract_date

    def _get_front_month_start_date(
        self,
        contract_date: datetime.date,
    ) -> datetime.date:
        # A contract becomes the front month immediately after the previous
        # contract's VIX settlement date.
        previous_contract_date = self._shift_contract_date(contract_date, -1)
        return self._get_vix_futures_settlement_date(
            previous_contract_date.year,
            previous_contract_date.month,
        )

    def _get_vix_futures_settlement_date(
        self,
        contract_year: int,
        contract_month: int,
    ) -> datetime.date:
        # Cboe VIX futures settle 30 days before the third Friday of the
        # following month. That date is the roll boundary used for historical
        # contract inference.
        following_year, following_month = self._shift_month(contract_year, contract_month, 1)
        third_friday = self._get_nth_weekday_of_month(
            following_year,
            following_month,
            weekday=calendar.FRIDAY,
            occurrence=3,
        )
        return third_friday - datetime.timedelta(days=30)

    def _get_nth_weekday_of_month(
        self,
        year: int,
        month: int,
        weekday: int,
        occurrence: int,
    ) -> datetime.date:
        first_day = datetime.date(year, month, 1)
        days_until_weekday = (weekday - first_day.weekday()) % 7
        return first_day + datetime.timedelta(days=days_until_weekday + ((occurrence - 1) * 7))

    def _shift_month(
        self,
        year: int,
        month: int,
        months: int,
    ) -> tuple[int, int]:
        # Convert through a linear month index so both year rollover and
        # backward stepping use the same arithmetic.
        month_index = (year * 12) + (month - 1) + months
        shifted_year = month_index // 12
        shifted_month = (month_index % 12) + 1
        return shifted_year, shifted_month

    def _shift_contract_date(
        self,
        contract_date: datetime.date,
        months: int,
    ) -> datetime.date:
        # Contract identity is normalized to the first day of its month, so
        # shifting a contract means shifting that normalized month marker.
        shifted_year, shifted_month = self._shift_month(
            contract_date.year,
            contract_date.month,
            months,
        )
        return datetime.date(shifted_year, shifted_month, 1)

    def _month_abbr_to_number(
        self,
        month_abbr: str,
    ) -> int:
        try:
            return list(calendar.month_abbr).index(month_abbr)
        except ValueError as exc:
            raise ValueError(f"Unsupported VIX Central month label: {month_abbr}") from exc

    def _parse_futures_date(
        self,
        futures_date: str | None,
    ) -> datetime.date | None:
        if futures_date is None:
            return None

        # `futures_date` is stored in the user-facing "YYYY Mon" format, but
        # contract comparisons use normalized first-of-month dates.
        year_str, month_abbr = futures_date.split(" ", 1)
        return datetime.date(int(year_str), self._month_abbr_to_number(month_abbr), 1)

    def _format_futures_date(self, contract_date: datetime.date) -> str:
        return f"{contract_date.year} {calendar.month_abbr[contract_date.month]}"
