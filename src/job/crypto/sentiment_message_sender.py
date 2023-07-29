from src.dependencies import Dependencies
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.type.sentiment import FearGreedResult
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

        service = Dependencies.get_sentiment_service()
        data = await service.get_crypto_fear_greed_index()

        if data is None or data.data is None or len(data.data) == 0:
            return messages

        messages.append(f"*Crypto fear greed index*: {escape_markdown(date_util.format(data.data[0].date))}")

        message = self._format_message(data)
        if message is not None:
            messages.append(message)

        return messages

    def _format_message(self, res: FearGreedResult):
        message = 'Sentiment:\n'
        for data in res.data:
            message = f'{message}{data.relative_date_text}: {data.sentiment_text}{escape_markdown("(")}{data.value} {data.emoji}{escape_markdown(")")}\n'

        message = f'{message}\n'
        message = f'{message}Average:\n'
        for average in res.average:
            rounded_value = int(round(average.value, 0))
            message = f'{message}Timeframe: {average.timeframe}: {average.sentiment_text}{escape_markdown("(")}{rounded_value} {average.emoji}{escape_markdown(")")}\n'
        return message