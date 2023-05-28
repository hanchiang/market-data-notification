from src.dependencies import Dependencies
from src.job.service.messari import format_messari_metrics
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.util.date_util import get_current_datetime
from src.util.my_telegram import escape_markdown
from src.type.market_data_type import MarketDataType

class MessariMessageSender(MessageSenderWrapper):
    @property
    def data_source(self):
        return "Messari"

    @property
    def market_data_type(self):
        return MarketDataType.CRYPTO

    async def format_message(self):
        messages = []
        curr = get_current_datetime()
        messages.append(f"*Crypto market data at {escape_markdown(curr.strftime('%Y-%m-%d'))}*")

        messari_service = Dependencies.get_messari_service()
        messari_res = await messari_service.get_asset_metrics()
        messari_message = format_messari_metrics(messari_res)
        if messari_message is not None:
            messages.append(messari_message)

        return messages



