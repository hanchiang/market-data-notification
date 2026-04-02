import argparse
import logging
from abc import ABC, abstractmethod
from typing import List

from src.job.message_sender_wrapper import MessageSenderWrapper
from src.config import config
from src.db.redis import Redis
from src.dependencies import Dependencies
from src.notification_destination.telegram_notification import send_message_to_channel, \
    market_data_type_to_admin_chat_id, init_telegram_bots
from src.runtime.runtime_mode import DEFAULT_RUNTIME_MODE, RuntimeMode
from src.util.context_manager import TimeTrackerContext
from src.util.exception import get_exception_message
from src.util.my_telegram import format_messages_to_telegram
from src.type.market_data_type import MarketDataType


logger = logging.getLogger('Job wrapper')
class JobWrapper(ABC):
    def __init__(self, runtime_mode: RuntimeMode | None = None):
        self.runtime_mode = (
            DEFAULT_RUNTIME_MODE if runtime_mode is None else runtime_mode
        )

    async def start(self):
        parser = argparse.ArgumentParser(description='Sends daily stock data notification to telegram')
        parser.add_argument('--force_run', type=int, choices=[0, 1], default=0,
                            help='Run regardless of the timing it is scheduled to run at')
        parser.add_argument('--test_mode', type=int, choices=[0, 1], default=0, help='Run in test mode for dev testing')
        cli_args = parser.parse_args()

        force_run: bool = cli_args.force_run == 1
        test_mode: bool = cli_args.test_mode == 1

        self.runtime_mode = RuntimeMode.from_test_mode(test_mode)

        if not force_run and not self.should_run(self.runtime_mode):
            return

        with TimeTrackerContext(f'{self.market_data_type.value.lower()}_notification_job'):
            init_telegram_bots()
            # TODO: May need a lock in the future
            messages = []
            if self.runtime_mode.is_test_mode:
                messages.insert(0, '*THIS IS A TEST MESSAGE: Parameters have been adjusted*')
            elif config.get_simulate_tradingview_traffic():
                messages.insert(0, '*SIMULATING TRAFFIC FROM TRADING VIEW*')

            try:
                await Redis.start_redis()
                await Dependencies.build()

                res = []
                for message_sender in self.message_senders:
                    r = await message_sender.start()
                    res.append(r)

                return res

            except Exception as e:
                logger.error(get_exception_message(e, cls=self.__class__.__name__))
                messages.append(f"{get_exception_message(e, cls=self.__class__.__name__, should_escape_markdown=True)}")
                message = format_messages_to_telegram(messages)
                await send_message_to_channel(message=message, chat_id=market_data_type_to_admin_chat_id[self.market_data_type],
                                              market_data_type=self.market_data_type,
                                              runtime_mode=self.runtime_mode)
                return None
            finally:
                await Redis.stop_redis()
                # await Dependencies.get_vix_central_service().cleanup()
                await Dependencies.cleanup()

    @abstractmethod
    def should_run(self, runtime_mode: RuntimeMode | None = None) -> bool:
        pass

    @property
    @abstractmethod
    def market_data_type(self) -> MarketDataType:
        pass

    @property
    @abstractmethod
    def message_senders(self) -> List[MessageSenderWrapper]:
        pass

