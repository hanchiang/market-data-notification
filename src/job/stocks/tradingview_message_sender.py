from typing import List

from src.dependencies import Dependencies
from src.config import config
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.type.trading_view import TradingViewDataType, TradingViewData
from src.util.date_util import get_datetime_from_timestamp
from src.util.my_telegram import escape_markdown
from src.type.market_data_type import MarketDataType

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
        self.market_indices = ['SPY', 'QQQ', 'IWM', 'DJIA']
        self.market_indices = sorted(self.market_indices)

        self.market_indices_order_map = {}
        for i in range(len(self.market_indices)):
            self.market_indices_order_map[self.market_indices[i]] = i + 1

    async def format_message(self):
        messages = []
        tradingview_stocks_data = await self.tradingview_service.get_tradingview_daily_stocks_data(type=TradingViewDataType.STOCKS)
        tradingview_economy_indicator_data = await self.tradingview_service.get_tradingview_daily_stocks_data(
            type=TradingViewDataType.ECONOMY_INDICATOR)

        if tradingview_stocks_data.get('data', None) is not None:
            tradingview_message = self._format_tradingview_message(stocks_payload=tradingview_stocks_data['data'],
                                                             economy_indicator_payload=
                                                             tradingview_economy_indicator_data[
                                                                 'data'])
            if tradingview_message is not None:
                tradingview_date = get_datetime_from_timestamp(tradingview_stocks_data['score']).strftime("%Y-%m-%d")
                tradingview_message = f"*Trading view market data at {escape_markdown(tradingview_date)}:*{tradingview_message}"
                messages.append(tradingview_message)

        return messages

    def _format_tradingview_message(self, stocks_payload: dict, economy_indicator_payload: dict):
        if len(stocks_payload.get('data', [])) == 0:
            return None

        stocks_list = self.tradingview_service.hydrate_data_list(data_list=stocks_payload.get('data'), type=TradingViewDataType.STOCKS)
        economy_indicator_list = self.tradingview_service.hydrate_data_list(data_list=economy_indicator_payload.get('data'),
                                                   type=TradingViewDataType.ECONOMY_INDICATOR)

        sorted_stocks = sorted(stocks_list, key=self._payload_sorter)
        sorted_economy_indicators = sorted(economy_indicator_list, key=self._payload_sorter)

        message = self._format_message_for_stocks(sorted_stocks)
        message = f"{message}\n{self._format_message_for_economy_indicators(sorted_economy_indicators)}"
        return message

    def _format_message_for_stocks(self, sorted_payload: List[TradingViewData]):
        message = ''
        for p in sorted_payload:
            symbol = p.symbol.upper()

            # TODO: Compare recent prices/volumes
            close = p.close_prices[0]
            ema20 = p.ema20s[0]
            volumes = p.volumes
            close_ema20_delta_ratio = (close - ema20) / ema20 if close > ema20 else -(ema20 - close) / ema20
            close_ema20_delta_percent = f"{close_ema20_delta_ratio:.2%}"
            potential_overextended_by_symbol = config.get_potential_overextended_by_symbol()

            message = f"{message}\nsymbol: *{symbol}*, close: {escape_markdown(str(close))}, {escape_markdown('ema20(1D)')}: {escape_markdown(str(f'{ema20:.2f}'))}, % diff from ema20: {escape_markdown(close_ema20_delta_percent)}"
            close_ema20_direction = 'above' if close > ema20 else 'below'

            if potential_overextended_by_symbol.get(symbol, None) is not None:
                if potential_overextended_by_symbol.get(symbol, {}).get(close_ema20_direction, None) is not None:
                    overextended_threshold = potential_overextended_by_symbol[symbol][close_ema20_direction]
                    if abs(close_ema20_delta_ratio) > abs(overextended_threshold):
                        message = f"{message}, *which is greater than the median overextended threshold of {escape_markdown(f'{overextended_threshold:.2%}')} when it is {'above' if close_ema20_direction == 'above' else 'below'} the ema20, watch for potential reversal* ‼️"
        return message

    def _format_message_for_economy_indicators(self, sorted_payload: List[TradingViewData]):
        message = ''
        for p in sorted_payload:
            symbol = p.symbol.upper()

            # TODO: Compare recent prices/volumes
            close = p.close_prices[0]
            potential_overextended_by_symbol = config.get_potential_overextended_by_symbol()

            # For VIX, compare close and overextended threshold. For other symbols, compare close_ema20_delta_ratio and overextended threshold
            message = f"{message}\nsymbol: *{symbol}*, close: {escape_markdown(str(close))}"

            if potential_overextended_by_symbol.get(symbol, None) is not None:
                vix_overextended_up_threshold = potential_overextended_by_symbol[symbol].get('above', None)
                vix_overextended_down_threshold = potential_overextended_by_symbol[symbol].get('below', None)
                if vix_overextended_up_threshold is not None and close >= vix_overextended_up_threshold:
                    message = f"{message}, *VIX is near the top around {f'{escape_markdown(str(vix_overextended_up_threshold))}'}, market could be near the bottom, watch for potential reversal* ‼️"
                elif vix_overextended_down_threshold is not None and close <= vix_overextended_down_threshold:
                    message = f"{message}, *VIX is near the bottom around {f'{escape_markdown(str(vix_overextended_down_threshold))}'}, market could be near the top, watch for potential reversal* ‼️"

        return message

    def _payload_sorter(self, item: TradingViewData):
        symbol = item.symbol.upper()

        if item.type == TradingViewDataType.STOCKS:
            # market indices should appear first
            if self.market_indices_order_map.get(symbol, False):
                return str(self.market_indices_order_map[symbol])
            return symbol

        # VIX should appear last
        # if symbol == 'VIX':
        #     return 'zzzzzzzzzzzzzzzzzz'

        return symbol


