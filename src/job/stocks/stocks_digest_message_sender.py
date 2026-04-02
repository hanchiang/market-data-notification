import logging
from typing import List

from src.config import config
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.job.stocks.sentiment_message_sender import StocksSentimentMessageSender
from src.job.stocks.stocks_digest_formatter import build_digest_messages
from src.job.stocks.tradingview_message_sender import TradingViewMessageSender
from src.job.stocks.vix_central_message_sender import VixCentralMessageSender
from src.notification_destination.telegram_notification import (
    market_data_type_to_admin_chat_id,
    market_data_type_to_chat_id,
    send_message_to_channel,
)
from src.type.market_data_type import MarketDataType
from src.util.exception import get_exception_message
from src.util.my_telegram import format_messages_to_telegram


logger = logging.getLogger('Stocks digest message sender')


class StocksDigestMessageSender(MessageSenderWrapper):
    def __init__(self, runtime_mode=None):
        super().__init__(runtime_mode=runtime_mode)
        self.tradingview_message_sender = TradingViewMessageSender(
            runtime_mode=self.runtime_mode
        )
        self.vix_central_message_sender = VixCentralMessageSender(
            runtime_mode=self.runtime_mode
        )
        self.sentiment_message_sender = StocksSentimentMessageSender(
            runtime_mode=self.runtime_mode
        )

    @property
    def data_source(self):
        return 'StocksDigest'

    @property
    def market_data_type(self):
        return MarketDataType.STOCKS

    async def start(self):
        try:
            messages = await self.format_message()

            if messages is None or len(messages) == 0:
                logger.warning(
                    'No message to send for market data type: %s, data source: %s',
                    self.market_data_type,
                    self.data_source,
                )
                return

            responses = []
            # Stocks now owns an intentional one-or-two-message presentation, so send
            # each digest chunk directly instead of collapsing back into one wrapper
            # payload and relying on transport-driven splitting.
            for message in messages:
                responses.append(
                    await send_message_to_channel(
                        message=message,
                        chat_id=market_data_type_to_chat_id[self.market_data_type],
                        market_data_type=self.market_data_type,
                        runtime_mode=self.runtime_mode,
                    )
                )
            return responses
        except Exception as error:
            logger.error(get_exception_message(error, cls=self.__class__.__name__))
            message = format_messages_to_telegram(
                [
                    get_exception_message(
                        error,
                        cls=self.__class__.__name__,
                        should_escape_markdown=True,
                    )
                ]
            )
            await send_message_to_channel(
                message=message,
                chat_id=market_data_type_to_admin_chat_id[self.market_data_type],
                market_data_type=self.market_data_type,
                runtime_mode=self.runtime_mode,
            )
            return None

    async def format_message(self) -> List[str]:
        tradingview_messages = await self.tradingview_message_sender.format_message()
        if tradingview_messages is None or len(tradingview_messages) == 0:
            # TradingView market-close data is the hard gate for the user-facing
            # stocks digest. Supporting sections should not be sent on their own.
            logger.info(
                'Skip stocks digest because TradingView anchor data is missing or stale.'
            )
            return []

        vix_messages = await self._load_supporting_messages(
            message_sender=self.vix_central_message_sender,
        )
        sentiment_messages = []
        if config.get_should_send_stocks_sentiment_message():
            sentiment_messages = await self._load_supporting_messages(
                message_sender=self.sentiment_message_sender,
            )

        return build_digest_messages(
            tradingview_messages=tradingview_messages,
            vix_messages=vix_messages,
            sentiment_messages=sentiment_messages,
        )

    async def _load_supporting_messages(
        self,
        message_sender: MessageSenderWrapper,
    ) -> List[str]:
        try:
            messages = await message_sender.format_message()
            return [] if messages is None else messages
        except Exception as error:
            logger.error(
                get_exception_message(error, cls=message_sender.__class__.__name__)
            )
            await self._send_supporting_error_alert(
                message_sender=message_sender,
                error=error,
            )
            return []

    async def _send_supporting_error_alert(
        self,
        message_sender: MessageSenderWrapper,
        error: Exception,
    ) -> None:
        message = format_messages_to_telegram(
            [
                get_exception_message(
                    error,
                    cls=message_sender.__class__.__name__,
                    should_escape_markdown=True,
                )
            ]
        )
        await send_message_to_channel(
            message=message,
            chat_id=market_data_type_to_admin_chat_id[self.market_data_type],
            market_data_type=self.market_data_type,
            runtime_mode=self.runtime_mode,
        )
