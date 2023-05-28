from functools import reduce

from src.service.chainanalysis import FeesAverage, ChainAnalysisFees, RecentFees
from src.dependencies import Dependencies
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.type.market_data_type import MarketDataType
from src.util import date_util
from src.util.my_telegram import escape_markdown
from src.util.number import friendly_number


class ChainAnalysisMessageSender(MessageSenderWrapper):
    @property
    def data_source(self):
        return "ChainAnalysis"

    @property
    def market_data_type(self):
        return MarketDataType.CRYPTO

    async def format_message(self):
        symbol = 'BTC'
        messages = []
        chainanalysis_service = Dependencies.get_chainanalysis_service()
        thirdparty_chainanalysis_service = Dependencies.get_thirdparty_chainanalysis_service()
        chainanalysis_trade_intensity = await thirdparty_chainanalysis_service.get_trade_intensity(symbol=symbol)
        chainanalysis_fees = await chainanalysis_service.get_fees(symbol=symbol)

        thirdparty_chainanalysis_message = self._format_chainanalysis(trade_intensity=chainanalysis_trade_intensity,
                                                                fees=chainanalysis_fees)
        if thirdparty_chainanalysis_message is not None:
            messages.append(thirdparty_chainanalysis_message)

        return messages

    def _format_chainanalysis(self, trade_intensity: dict, fees: ChainAnalysisFees):
        message = f"*{escape_markdown(trade_intensity.get('highlight', ''))}*\n"
        message = f"{message}\n{self._format_fees_summary(fees)}\n"
        message = f"{message}\n{self._format_recent_fees(fees)}\n"
        message = f"{message}\n{self._format_average_fees(fees)}"
        return message

    def _format_fees_summary(self, fees: ChainAnalysisFees) -> str:
        percent_change = f'{escape_markdown(f"({fees.fees_summary.percent_change / 100:.2%})")}'
        return f"*Fees summary*:\n{escape_markdown(fees.highlight)}, change of {escape_markdown(str(fees.fees_summary.change))}{percent_change} in {fees.fees_summary.timeframe}"

    def _format_recent_fees(self, fees: ChainAnalysisFees) -> str:
        return reduce(self._format_recent_fee, fees.recent_fees, '*Recent fees*:')

    def _format_recent_fee(self, res: str, fee: RecentFees) -> str:
        dt = date_util.get_datetime_from_timestamp(fee.ts, use_ny_tz=False)
        formatted_date = escape_markdown(date_util.format(dt))
        asset_amount = escape_markdown(f"{friendly_number(num=fee.values[1].value, decimal_places=2)}")
        usd_amount = escape_markdown(f"({friendly_number(num=fee.values[0].value, decimal_places=3)} USD)")
        message = f"date: {formatted_date}, amount: {asset_amount}{usd_amount}"

        return f"{res}\n{message}"

    def _format_average_fees(self, fees: ChainAnalysisFees) -> str:
        return reduce(self._format_average_fee, fees.average_fees, '*Average fees*:')

    def _format_average_fee(self, res: str, fee: FeesAverage) -> str:
        asset_amount = escape_markdown(f"{friendly_number(num=fee.values[1].value, decimal_places=2)}")
        usd_amount = escape_markdown(f"({friendly_number(num=fee.values[0].value, decimal_places=3)} USD)")
        message = f"timeframe: {fee.timeframe}, amount: {asset_amount}{usd_amount}"

        return f"{res}\n{message}"


