from src.dependencies import Dependencies
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.job.service.vix_central import format_vix_central_message
from src.type.market_data_type import MarketDataType


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
        vix_central_message = format_vix_central_message(vix_central_data)

        if vix_central_message is not None:
            messages.append(vix_central_message)

        return messages

