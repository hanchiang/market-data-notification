import logging
from abc import ABC, abstractmethod
from typing import List

from src.notification_destination.telegram_notification import send_message_to_channel, \
    market_data_type_to_admin_chat_id, market_data_type_to_chat_id
from src.util.exception import get_exception_message
from src.util.my_telegram import escape_markdown, format_messages_to_telegram


logger = logging.getLogger('Message sender wrapper')
class MessageSenderWrapper(ABC):
    async def start(self):
        try:
            messages = await self.format_message()

            if messages is None or len(messages) == 0:
                logger.warning(f"No message to send for market data type: {self.market_data_type}, data source: {self.data_source}")
                return

            telegram_message = format_messages_to_telegram(messages)
            res = await send_message_to_channel(message=telegram_message,
                                                chat_id=market_data_type_to_chat_id[self.market_data_type],
                                                market_data_type=self.market_data_type)
            return res
        except Exception as e:
            logger.error(get_exception_message(e, cls=self.__class__.__name__))
            messages = [f"{get_exception_message(e, cls=self.__class__.__name__, should_escape_markdown=True)}"]
            message = format_messages_to_telegram(messages)
            await send_message_to_channel(message=message, chat_id=market_data_type_to_admin_chat_id[self.market_data_type],
                                          market_data_type=self.market_data_type)
            return None

    @abstractmethod
    async def format_message(self) -> List[str]:
        pass

    @property
    @abstractmethod
    def data_source(self):
        pass

    @property
    @abstractmethod
    def market_data_type(self):
        pass
