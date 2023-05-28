import argparse
from abc import ABC, abstractmethod
from typing import List

from src.job.message_sender_wrapper import MessageSenderWrapper
from src.config import config
from src.db.redis import Redis
from src.dependencies import Dependencies
from src.notification_destination.telegram_notification import send_message_to_channel, market_data_type_to_admin_chat_id
from src.util.context_manager import TimeTrackerContext
from src.util.my_telegram import format_messages_to_telegram, escape_markdown
from src.type.market_data_type import MarketDataType


class JobWrapper(ABC):
    async def start(self):
        parser = argparse.ArgumentParser(description='Sends daily stock data notification to telegram')
        parser.add_argument('--force_run', type=int, choices=[0, 1], default=0,
                            help='Run regardless of the timing it is scheduled to run at')
        parser.add_argument('--test_mode', type=int, choices=[0, 1], default=0, help='Run in test mode for dev testing')
        cli_args = parser.parse_args()

        force_run: bool = cli_args.force_run == 1
        test_mode: bool = cli_args.test_mode == 1

        if test_mode:
            config.set_is_testing_telegram('true')

        if not force_run and not self.should_run():
            return

        with TimeTrackerContext(f'{self.market_data_type.value.lower()}_notification_job'):
            # TODO: May need a lock in the future
            messages = []
            if config.get_is_testing_telegram():
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
                print(f"{self.__class__.__name__} exception: {e}")
                messages.append(f"{escape_markdown(str(e))}")
                message = format_messages_to_telegram(messages)
                await send_message_to_channel(message=message, chat_id=market_data_type_to_admin_chat_id[self.market_data_type],
                                              market_data_type=self.market_data_type)
                return None
            finally:
                await Redis.stop_redis()
                # await Dependencies.get_vix_central_service().cleanup()
                await Dependencies.cleanup()

    @abstractmethod
    def should_run(self) -> bool:
        pass

    @property
    @abstractmethod
    def market_data_type(self) -> MarketDataType:
        pass

    @property
    @abstractmethod
    def message_senders(self) -> List[MessageSenderWrapper]:
        pass


