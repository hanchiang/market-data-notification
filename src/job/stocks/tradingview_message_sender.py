import datetime
import logging
from dataclasses import dataclass
from typing import List, Optional

from src.dependencies import Dependencies
from src.config import config
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.type.trading_view import TradingViewDataType, TradingViewData, TradingViewStocksData
from src.util.date_util import get_datetime_from_timestamp, get_most_recent_non_weekend_or_today, \
    get_current_date_preserve_time
from src.util.my_telegram import escape_markdown, exclamation_mark
from src.type.market_data_type import MarketDataType
from src.util.number import friendly_number
from src.util.sleep import sleep


logger = logging.getLogger('Trading view message sender')


@dataclass
class StocksMessageEntry:
    # Normalize the raw TradingView plus Barchart-derived fields into one rendering
    # shape before we decide which names deserve detailed treatment in the digest.
    symbol: str
    close: float
    ema20: float
    close_ema20_delta_ratio: float
    volume_text: Optional[str]
    volume_alert: Optional[str]
    overextended_alert: Optional[str]


class TradingViewMessageSender(MessageSenderWrapper):
    @property
    def data_source(self):
        return "TradingView"

    @property
    def market_data_type(self):
        return MarketDataType.STOCKS

    def __init__(self, runtime_mode=None):
        super().__init__(runtime_mode=runtime_mode)
        self.tradingview_service = Dependencies.get_tradingview_service()
        self.barchart_service = Dependencies.get_barchart_service()
        self.market_indices = ['SPY', 'QQQ', 'IWM', 'DIA']
        self.market_indices = sorted(self.market_indices)

        self.market_indices_order_map = {}
        for i in range(len(self.market_indices)):
            self.market_indices_order_map[self.market_indices[i]] = i + 1

    async def format_message(self):
        messages = []
        tradingview_stocks_data = await self.tradingview_service.get_tradingview_daily_stocks_data(type=TradingViewDataType.STOCKS)
        if tradingview_stocks_data is None or tradingview_stocks_data.data is None or tradingview_stocks_data.score is None:
            logger.info('No TradingView stocks data found in Redis. Skip formatting message')
            return messages

        now = get_current_date_preserve_time()
        most_recent_day = get_most_recent_non_weekend_or_today(now - datetime.timedelta(days=1))
        time_diff = abs(int(most_recent_day.timestamp()) - tradingview_stocks_data.score)
        if not self.runtime_mode.allow_stale_replay and time_diff > 86400:
            logger.info(
                'Skip formatting message. runtime test mode: %s, time difference: %s',
                self.runtime_mode.is_test_mode,
                time_diff,
            )
            return messages

        tradingview_economy_indicator_data = await self.tradingview_service.get_tradingview_daily_stocks_data(
            type=TradingViewDataType.ECONOMY_INDICATOR)

        economy_indicator_payload = None if tradingview_economy_indicator_data is None else tradingview_economy_indicator_data.data

        tradingview_message = await self._format_tradingview_message(
            stocks_payload=tradingview_stocks_data.data,
            economy_indicator_payload=economy_indicator_payload,
        )
        if tradingview_message is not None:
            tradingview_date = get_datetime_from_timestamp(tradingview_stocks_data.score).strftime("%Y-%m-%d")
            tradingview_message = (
                f"*Trading view market data at {escape_markdown(tradingview_date)}:*"
                f"\n\n{tradingview_message}"
            )
            messages.append(tradingview_message)

        return messages

    # TODO: type
    async def _format_tradingview_message(self, stocks_payload: TradingViewData, economy_indicator_payload: TradingViewData):
        if stocks_payload is None:
            return None

        stocks_list = stocks_payload.data or []
        economy_indicator_list = []
        if economy_indicator_payload is not None and economy_indicator_payload.data is not None:
            economy_indicator_list = economy_indicator_payload.data

        if len(stocks_list) == 0 and len(economy_indicator_list) == 0:
            return None

        sorted_stocks = sorted(stocks_list, key=lambda x: self._payload_sorter(x, stocks_payload.type))
        sorted_economy_indicators = []
        if economy_indicator_payload is not None:
            sorted_economy_indicators = sorted(
                economy_indicator_list,
                key=lambda x: self._payload_sorter(x, economy_indicator_payload.type),
            )

        messages = []
        if len(sorted_stocks) > 0:
            messages.append(await self._format_message_for_stocks(sorted_stocks))
        if len(sorted_economy_indicators) > 0:
            messages.append(self._format_message_for_economy_indicators(sorted_economy_indicators))

        return '\n\n'.join(messages)

    # TODO: refactor this with _format_message_for_economy_indicators
    async def _format_message_for_stocks(self, sorted_payload: List[TradingViewStocksData]):
        entries = [
            await self._build_stocks_message_entry(payload)
            for payload in sorted_payload
        ]

        index_entries = [
            entry for entry in entries if entry.symbol in self.market_indices_order_map
        ]
        non_index_entries = [
            entry for entry in entries if entry.symbol not in self.market_indices_order_map
        ]

        blocks = []
        # Keep the first message scan-friendly: lead with market indices, then only a
        # small highlighted subset of tracked names, and collapse the rest into a
        # compact summary instead of one long ticker-by-ticker dump.
        blocks.extend(
            self._format_detailed_stocks_entries(
                entries=index_entries,
                title='Indices',
            )
        )

        highlighted_entries, remaining_entries = self._split_highlighted_stock_entries(
            entries=non_index_entries,
        )
        blocks.extend(
            self._format_detailed_stocks_entries(
                entries=highlighted_entries,
                title='Notable tracked names',
            )
        )
        blocks.extend(self._format_compact_stock_summary(entries=remaining_entries))

        return '\n\n'.join(blocks)

    async def _build_stocks_message_entry(
        self,
        payload: TradingViewStocksData,
    ) -> StocksMessageEntry:
        symbol = payload.symbol.upper()
        close = payload.close_prices[0]
        ema20 = payload.ema20s[0]
        close_ema20_delta_ratio = (
            (close - ema20) / ema20
            if close > ema20
            else -(ema20 - close) / ema20
        )

        volume_text = None
        volume_alert = None
        volumes = payload.volumes
        if volumes is not None and isinstance(volumes, list) and len(volumes) > 0:
            volume_text = friendly_number(volumes[0], decimal_places=2)
            if config.get_should_compare_stocks_volume_rank():
                stock_prices = await self.barchart_service.get_stock_price(
                    symbol=symbol,
                    num_days=30,
                )
                await sleep(max_sec=0.2)
                historical_volumes = [x.volume for x in stock_prices]
                max_days_to_compare = self.get_current_data_highest_volume_info(
                    historical_volumes
                )
                if max_days_to_compare is not None and len(historical_volumes) > 1:
                    # Require both conditions: today's volume must remain the highest
                    # over the accepted lookback window, and the jump versus the prior
                    # day must still clear a ratio threshold worth calling out.
                    volume_ratio_diff = abs(
                        (historical_volumes[0] - historical_volumes[1])
                        / historical_volumes[1]
                    )
                    if (
                        volume_ratio_diff
                        > config.get_stocks_volume_alert_ratio_threshold(
                            is_test_mode=self.runtime_mode.relax_thresholds
                        )
                    ):
                        volume_alert = (
                            f'Highest(+{volume_ratio_diff:.2%} vs previous day) volume for the '
                            f'past {max_days_to_compare} days {exclamation_mark()}'
                        )

        overextended_alert = None
        potential_overextended_by_symbol = config.get_potential_overextended_by_symbol(
            is_test_mode=self.runtime_mode.relax_thresholds
        )
        close_ema20_direction = 'above' if close > ema20 else 'below'
        symbol_thresholds = potential_overextended_by_symbol.get(symbol, {})
        overextended_threshold = symbol_thresholds.get(close_ema20_direction)
        if overextended_threshold is not None and abs(close_ema20_delta_ratio) > abs(
            overextended_threshold
        ):
            # The thresholds are directional per symbol, so only compare against the
            # side of ema20 the current close is actually on.
            overextended_alert = (
                f'% diff from ema is greater than the threshold '
                f'{overextended_threshold:.2%} when it is {close_ema20_direction} '
                f'ema20, watch for potential reversal ‼️'
            )

        return StocksMessageEntry(
            symbol=symbol,
            close=close,
            ema20=ema20,
            close_ema20_delta_ratio=close_ema20_delta_ratio,
            volume_text=volume_text,
            volume_alert=volume_alert,
            overextended_alert=overextended_alert,
        )

    def _split_highlighted_stock_entries(
        self,
        entries: List[StocksMessageEntry],
    ) -> tuple[List[StocksMessageEntry], List[StocksMessageEntry]]:
        highlighted_entries = []
        remaining_entries = []

        sorted_entries = sorted(
            entries,
            key=lambda entry: (
                0 if entry.overextended_alert else 1,
                0 if entry.volume_alert else 1,
                -abs(entry.close_ema20_delta_ratio),
                entry.symbol,
            ),
        )

        # Keep the detailed portion intentionally small so the TradingView anchor still
        # has room for economy indicators and does not regress into a wall of text.
        for entry in sorted_entries:
            should_highlight = (
                entry.overextended_alert is not None
                or entry.volume_alert is not None
                or abs(entry.close_ema20_delta_ratio) >= 0.04
            )
            if should_highlight and len(highlighted_entries) < 4:
                highlighted_entries.append(entry)
            else:
                remaining_entries.append(entry)

        remaining_entries = sorted(remaining_entries, key=lambda entry: entry.symbol)
        return highlighted_entries, remaining_entries

    def _format_detailed_stocks_entries(
        self,
        entries: List[StocksMessageEntry],
        title: str,
    ) -> List[str]:
        if len(entries) == 0:
            return []

        lines = [f'*{title}*']
        for entry in entries:
            lines.append(self._format_detailed_stock_entry(entry))
        return lines

    def _format_detailed_stock_entry(self, entry: StocksMessageEntry) -> str:
        parts = [
            f'*{entry.symbol}*',
            f'close {escape_markdown(str(entry.close))}',
            f'{escape_markdown("ema20(1D)")} {escape_markdown(f"{entry.ema20:.2f}")}',
            f'{escape_markdown(entry.close_ema20_delta_ratio.__format__(".2%"))} vs ema20',
        ]
        if entry.volume_text is not None:
            parts.append(f'vol {escape_markdown(entry.volume_text)}')

        line = ', '.join(parts)
        detail_lines = [line]
        if entry.volume_alert is not None:
            detail_lines.append(escape_markdown(entry.volume_alert))
        if entry.overextended_alert is not None:
            detail_lines.append(escape_markdown(entry.overextended_alert))
        return '\n'.join(detail_lines)

    def _format_compact_stock_summary(
        self,
        entries: List[StocksMessageEntry],
    ) -> List[str]:
        if len(entries) == 0:
            return []

        below_ema20 = [
            f'{entry.symbol}({entry.close_ema20_delta_ratio:.2%})'
            for entry in entries
            if entry.close_ema20_delta_ratio < 0
        ]
        above_or_flat_ema20 = [
            f'{entry.symbol}({entry.close_ema20_delta_ratio:.2%})'
            for entry in entries
            if entry.close_ema20_delta_ratio >= 0
        ]

        lines = ['*Other tracked names*']
        if len(below_ema20) > 0:
            lines.append(
                f'Below ema20: {escape_markdown(", ".join(below_ema20))}'
            )
        if len(above_or_flat_ema20) > 0:
            lines.append(
                f'At/above ema20: {escape_markdown(", ".join(above_or_flat_ema20))}'
            )
        return lines

    def _format_message_for_economy_indicators(self, sorted_payload: List[TradingViewData]):
        lines = ['*Economy indicators*']
        for p in sorted_payload:
            symbol = p.symbol.upper()

            # TODO: Compare recent prices/volumes
            close = p.close_prices[0]
            potential_overextended_by_symbol = config.get_potential_overextended_by_symbol(
                is_test_mode=self.runtime_mode.relax_thresholds
            )

            # Compare close and overextended threshold
            message = f'*{symbol}*, close {escape_markdown(str(close))}'

            if potential_overextended_by_symbol.get(symbol, None) is not None:
                vix_overextended_up_threshold = potential_overextended_by_symbol[symbol].get('above', None)
                vix_overextended_down_threshold = potential_overextended_by_symbol[symbol].get('below', None)
                if vix_overextended_up_threshold is not None and close >= vix_overextended_up_threshold:
                    message = (
                        f'{message}, '
                        f'{escape_markdown("VIX is near the top around")} '
                        f'{escape_markdown(str(vix_overextended_up_threshold))}, '
                        f'{escape_markdown("watch for potential reversal")} '
                        f'{exclamation_mark()}'
                    )
                elif vix_overextended_down_threshold is not None and close <= vix_overextended_down_threshold:
                    message = (
                        f'{message}, '
                        f'{escape_markdown("VIX is near the bottom around")} '
                        f'{escape_markdown(str(vix_overextended_down_threshold))}, '
                        f'{escape_markdown("watch for potential reversal")} '
                        f'{exclamation_mark()}'
                    )

            lines.append(message)
        return '\n\n'.join(lines)

    # data is ordered in descending order of date. First element is the current day
    # return the number of consecutive past days for which the current volume is greater
    # for test mode, return value even if first day greater count is less than minimum threshold
    def get_current_data_highest_volume_info(
        self,
        data_by_date: List[float],
        num_days_range=None,
    ) -> int:
        if data_by_date is None or len(data_by_date) < 2:
            return None
        if num_days_range is None:
            num_days_range = config.get_number_of_past_days_range_for_stock_volume_rank(
                is_test_mode=self.runtime_mode.relax_thresholds
            )
        (num_days_min, num_days_max) = num_days_range

        # get the largest number of days that fit within the data length
        max_days_to_compare = max(min(num_days_min, len(data_by_date)), min(num_days_max, len(data_by_date)))
        if max_days_to_compare < num_days_min:
            return None
        first_day_greater_count = 0

        # count number of consecutive days for which the current day has higher volume
        for i in range(1, max_days_to_compare):
            if data_by_date[0] >= data_by_date[i]:
                first_day_greater_count += 1
            else:
                break

        if not self.runtime_mode.relax_thresholds:
            if first_day_greater_count < min(num_days_min - 1, max_days_to_compare):
                return None
            return first_day_greater_count + 1

        # Test mode keeps the same rule shape but allows shorter streaks so replayed
        # snapshots are more likely to surface the alert text during local review.
        return first_day_greater_count + 1

    def _payload_sorter(self, item: TradingViewData, type: TradingViewDataType):
        symbol = item.symbol.upper()

        if type == TradingViewDataType.STOCKS:
            # market indices should appear first
            if self.market_indices_order_map.get(symbol, False):
                return str(self.market_indices_order_map[symbol])
            return symbol

        # VIX should appear last
        # if symbol == 'VIX':
        #     return 'zzzzzzzzzzzzzzzzzz'

        return symbol
