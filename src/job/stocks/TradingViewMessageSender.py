from src.job.message_sender_wrapper import MessageSenderWrapper
from src.job.service.tradingview import get_tradingview_daily_stocks_data, format_tradingview_message
from src.type.trading_view import TradingViewDataType
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

    async def format_message(self):
        messages = []
        tradingview_stocks_data = await get_tradingview_daily_stocks_data(type=TradingViewDataType.STOCKS)
        tradingview_economy_indicator_data = await get_tradingview_daily_stocks_data(
            type=TradingViewDataType.ECONOMY_INDICATOR)

        if tradingview_stocks_data.get('data', None) is not None:
            tradingview_message = format_tradingview_message(stocks_payload=tradingview_stocks_data['data'],
                                                             economy_indicator_payload=
                                                             tradingview_economy_indicator_data[
                                                                 'data'])
            if tradingview_message is not None:
                tradingview_date = get_datetime_from_timestamp(tradingview_stocks_data['score']).strftime("%Y-%m-%d")
                tradingview_message = f"*Trading view market data at {escape_markdown(tradingview_date)}:*{tradingview_message}"
                messages.append(tradingview_message)

        return messages



