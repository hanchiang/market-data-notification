import datetime
import logging
from typing import List

from src.dependencies import Dependencies
from src.config import config
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.type.trading_view import TradingViewDataType, TradingViewData, TradingViewStocksData
from src.util.date_util import get_datetime_from_timestamp, get_most_recent_non_weekend_or_today, \
    get_current_date_preserve_time
from src.util.my_telegram import escape_markdown, message_separator, exclamation_mark
from src.type.market_data_type import MarketDataType
from src.util.number import friendly_number
from src.util.sleep import sleep


logger = logging.getLogger('Trading view message sender')
class TradingViewMessageSender(MessageSenderWrapper):
    @property
    def data_source(self):
        return "TradingView"

    @property
    def market_data_type(self):
        return MarketDataType.STOCKS

    def __init__(self):
        super().__init__()
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
        now = get_current_date_preserve_time()
        most_recent_day = get_most_recent_non_weekend_or_today(now - datetime.timedelta(days=1))
        time_diff = abs(int(most_recent_day.timestamp()) - tradingview_stocks_data.score)
        if not config.get_is_testing_telegram() and time_diff > 86400:
            logger.info(f'Skip formatting message. is testing telegram: {config.get_is_testing_telegram()}, time difference: {time_diff}')
            return messages

        tradingview_economy_indicator_data = await self.tradingview_service.get_tradingview_daily_stocks_data(
            type=TradingViewDataType.ECONOMY_INDICATOR)

        if tradingview_stocks_data is not None and tradingview_stocks_data.data is not None:
            tradingview_message = await self._format_tradingview_message(stocks_payload=tradingview_stocks_data.data,
                                                             economy_indicator_payload=tradingview_economy_indicator_data.data)
            if tradingview_message is not None:
                tradingview_date = get_datetime_from_timestamp(tradingview_stocks_data.score).strftime("%Y-%m-%d")
                tradingview_message = f"*Trading view market data at {escape_markdown(tradingview_date)}:*{tradingview_message}"
                messages.append(tradingview_message)

        return messages

    # TODO: type
    async def _format_tradingview_message(self, stocks_payload: TradingViewData, economy_indicator_payload: TradingViewData):
        if len(stocks_payload.data) == 0 and len(economy_indicator_payload.data) == 0:
            return None

        stocks_list = stocks_payload.data
        economy_indicator_list = economy_indicator_payload.data

        sorted_stocks = sorted(stocks_list, key=lambda x: self._payload_sorter(x, stocks_payload.type))
        sorted_economy_indicators = sorted(economy_indicator_list, key=lambda x: self._payload_sorter(x, economy_indicator_payload.type))

        message = await self._format_message_for_stocks(sorted_stocks)
        message = f"{message}{escape_markdown(message_separator())}"
        message = f"{message}{self._format_message_for_economy_indicators(sorted_economy_indicators)}"
        return message

    # TODO: refactor this with _format_message_for_economy_indicators
    async def _format_message_for_stocks(self, sorted_payload: List[TradingViewStocksData]):
        message = ''
        for p in sorted_payload:
            symbol = p.symbol.upper()

            close = p.close_prices[0]
            ema20 = p.ema20s[0]
            volumes = p.volumes
            close_ema20_delta_ratio = (close - ema20) / ema20 if close > ema20 else -(ema20 - close) / ema20
            close_ema20_delta_percent = f"{close_ema20_delta_ratio:.2%}"
            potential_overextended_by_symbol = config.get_potential_overextended_by_symbol()

            message = f"{message}\nsymbol: *{symbol}*, close: {escape_markdown(str(close))}, {escape_markdown('ema20(1D)')}: {escape_markdown(str(f'{ema20:.2f}'))}, % diff from ema20: {escape_markdown(close_ema20_delta_percent)}"

            if volumes is not None and type(volumes) is list and len(volumes) > 0:
                message = f"{message}, volume: {escape_markdown(friendly_number(volumes[0], decimal_places=2))}"
                # volume rank for recent days
                if config.get_should_compare_stocks_volume_rank():
                    # TODO: type
                    stock_prices = await self.barchart_service.get_stock_price(symbol=symbol, num_days=30)
                    await sleep(max_sec=0.2)
                    volumes = list(map(lambda x: x['volume'], stock_prices))
                    max_days_to_compare = self.get_current_data_highest_volume_info(volumes)
                    if max_days_to_compare is not None and len(volumes) > 1:
                        # TODO: configure a separate volume ratio threshold for each symbol
                        volume_ratio_diff = abs((volumes[0] - volumes[1]) / volumes[1])
                        if volume_ratio_diff > 0.2:
                            volume_ratio_text_escaped = escape_markdown(f"(+{volume_ratio_diff:.2%} vs ytd)")
                            message = f"{message}{escape_markdown('.')}\nHighest*{volume_ratio_text_escaped}* volume for the past *{max_days_to_compare} days {exclamation_mark()}*"


            # check for overextension for both sides
            close_ema20_direction = 'above' if close > ema20 else 'below'
            if potential_overextended_by_symbol.get(symbol, None) is not None:
                if potential_overextended_by_symbol.get(symbol, {}).get(close_ema20_direction, None) is not None:
                    overextended_threshold = potential_overextended_by_symbol[symbol][close_ema20_direction]
                    if abs(close_ema20_delta_ratio) > abs(overextended_threshold):
                        message = f"{message}{escape_markdown('.')}\n% diff from ema is *greater than the threshold {escape_markdown(f'{overextended_threshold:.2%}')} when it is {'above' if close_ema20_direction == 'above' else 'below'} ema20, watch for potential reversal* ‼️"

            message = f'{message}\n'
        return message

    def _format_message_for_economy_indicators(self, sorted_payload: List[TradingViewData]):
        message = ''
        for p in sorted_payload:
            symbol = p.symbol.upper()

            # TODO: Compare recent prices/volumes
            close = p.close_prices[0]
            potential_overextended_by_symbol = config.get_potential_overextended_by_symbol()

            # Compare close and overextended threshold
            message = f"{message}\nsymbol: *{symbol}*, close: {escape_markdown(str(close))}"

            if potential_overextended_by_symbol.get(symbol, None) is not None:
                vix_overextended_up_threshold = potential_overextended_by_symbol[symbol].get('above', None)
                vix_overextended_down_threshold = potential_overextended_by_symbol[symbol].get('below', None)
                if vix_overextended_up_threshold is not None and close >= vix_overextended_up_threshold:
                    message = f"{message}{escape_markdown('.')} *VIX is near the top around {f'{escape_markdown(str(vix_overextended_up_threshold))}'}, watch for potential reversal* {exclamation_mark()}"
                elif vix_overextended_down_threshold is not None and close <= vix_overextended_down_threshold:
                    message = f"{message}{escape_markdown('.')} *VIX is near the bottom around {f'{escape_markdown(str(vix_overextended_down_threshold))}'}, watch for potential reversal* {exclamation_mark()}"

            message = f'{message}\n'
        return message

    # data is ordered in descending order of date. First element is the current day
    # return the number of consecutive past days for which the current volume is greater
    # for test mode, return value even if first day greater count is less than minimum threshold
    def get_current_data_highest_volume_info(self, data_by_date: List[float],
                                             num_days_range=config.get_number_of_past_days_range_for_stock_volume_rank()) -> int:
        if data_by_date is None or len(data_by_date) < 2:
            return None
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

        if not config.get_is_testing_telegram():
            if first_day_greater_count < min(num_days_min - 1, max_days_to_compare):
                return None
            return first_day_greater_count + 1

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


