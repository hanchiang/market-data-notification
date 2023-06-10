from functools import reduce

from src.config import config
from src.service.vix_central import RecentVixFuturesValues
from src.dependencies import Dependencies
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.type.market_data_type import MarketDataType
from src.util.my_telegram import escape_markdown, exclamation_mark


class VixCentralMessageSender(MessageSenderWrapper):
    @property
    def data_source(self):
        return "VixCentral"

    @property
    def market_data_type(self):
        return MarketDataType.STOCKS

    async def format_message(self):
        messages = []
        vix_central_service = Dependencies.get_vix_central_service()
        vix_central_data = await vix_central_service.get_recent_values()
        vix_central_message = self._format_vix_central_message(vix_central_data)

        if vix_central_message is not None:
            messages.append(vix_central_message)

        return messages

    def _format_vix_central_message(self, vix_central_value: RecentVixFuturesValues):
        if vix_central_value is None or len(vix_central_value.vix_futures_values) == 0:
            return None
        message = reduce(self._format_vix_futures_values, vix_central_value.vix_futures_values,
                         f"*VIX central data for {vix_central_value.vix_futures_values[0].futures_date} futures:*")
        if config.get_display_vix_futures_contango_decrease_past_n_days() and vix_central_value.is_contango_decrease_for_past_n_days:
            message = f"{message}\n*Contango has been decreasing for the past {vix_central_value.actual_contango_decrease_past_n_days} days {exclamation_mark()}*"
        return message

    def _format_vix_futures_values(self, res, curr):
        message = f"{res}\ndate: {escape_markdown(curr.current_date)}, contango %: {escape_markdown(curr.formatted_contango)}"
        message = f"{message}" if curr.formatted_contango_change_prev_day is None else f"{message}, changed by {escape_markdown(curr.formatted_contango_change_prev_day)} from the previous day"
        if curr.is_contango_single_day_decrease_alert:
            threshold = f"{curr.contango_single_day_decrease_alert_ratio:.1%}"
            message = f"{message}, *which is greater than the threshold {escape_markdown(threshold)}, watch for potential reversal {exclamation_mark()}*"
        return message

