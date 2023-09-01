from src.dependencies import Dependencies
from src.job.crypto.message_formatter.telegram_formatter import crypto_sentiment_message_formatter
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.type.market_data_type import MarketDataType
from src.util.my_telegram import escape_markdown
import src.util.date_util as date_util

class SentimentMessageSender(MessageSenderWrapper):
    @property
    def data_source(self):
        return "Sentiment"

    @property
    def market_data_type(self):
        return MarketDataType.CRYPTO

    async def format_message(self):
        messages = []

        service = Dependencies.get_crypto_sentiment_service()
        data = await service.get_crypto_fear_greed_index()

        if data is None or data.data is None or len(data.data) == 0:
            return messages

        messages.append(f"*Crypto fear greed index*: {escape_markdown(date_util.format(data.data[0].date))}")

        message = crypto_sentiment_message_formatter(data)
        if message is not None:
            messages.append(message)

        return messages
