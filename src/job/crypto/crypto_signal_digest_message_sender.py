import datetime
import logging
from typing import List

from src.config import config
from src.job.crypto.crypto_signal_formatter import build_crypto_signal_message
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.notification_destination.telegram_notification import (
    send_crypto_signal_message,
    send_message_to_admin,
)
from src.runtime.runtime_mode import DEFAULT_RUNTIME_MODE
from src.service.crypto_signal.repository import CryptoSignalRepository
from src.service.crypto_signal.scorer import build_digest_view, get_window_start
from src.type.market_data_type import MarketDataType
from src.util.date_util import get_current_datetime
from src.util.exception import get_exception_message
from src.util.my_telegram import format_messages_to_telegram


logger = logging.getLogger('Crypto signal digest message sender')
_SNAPSHOT_FRESHNESS_THRESHOLD = datetime.timedelta(minutes=90)


class CryptoSignalDigestMessageSender(MessageSenderWrapper):
    def __init__(self, runtime_mode=None):
        super().__init__(runtime_mode=runtime_mode)
        self.signal_repository = CryptoSignalRepository()
        self.watchlist_entries = config.get_crypto_signal_watchlist()

    @property
    def data_source(self):
        return 'SQLite + heuristic scorer'

    @property
    def market_data_type(self):
        return MarketDataType.CRYPTO

    async def start(self):
        try:
            messages = await self.format_message()
            if messages is None or len(messages) == 0:
                logger.info('Skipping crypto signal send because no fresh snapshot was available')
                return None

            telegram_message = format_messages_to_telegram(messages)
            return await send_crypto_signal_message(
                message=telegram_message,
                chat_id=config.get_crypto_signal_recipient_id(),
                runtime_mode=self.runtime_mode,
            )
        except Exception as error:
            logger.error(get_exception_message(error, cls=self.__class__.__name__))
            await send_message_to_admin(
                message=format_messages_to_telegram(
                    [
                        get_exception_message(
                            error,
                            cls=self.__class__.__name__,
                            should_escape_markdown=True,
                        )
                    ]
                ),
                market_data_type=MarketDataType.CRYPTO,
            )
            return None

    async def format_message(self) -> List[str]:
        repository = getattr(self, 'signal_repository', CryptoSignalRepository())
        latest_snapshot = repository.get_latest_snapshot()
        if latest_snapshot is None:
            return []

        if not self._is_fresh_enough(latest_snapshot.run.run_timestamp_utc):
            return []

        window_label = '7d'
        history = repository.get_snapshots_since(
            get_window_start(latest_snapshot, window_label=window_label)
        )
        view = build_digest_view(
            latest_snapshot=latest_snapshot,
            history=history,
            watchlist_coin_ids={coin_id for _symbol, coin_id in self.watchlist_entries},
            window_label=window_label,
            limit=3,
        )
        return [build_crypto_signal_message(view)]

    def _is_fresh_enough(self, run_timestamp_utc: datetime.datetime) -> bool:
        runtime_mode = getattr(self, 'runtime_mode', DEFAULT_RUNTIME_MODE)
        if runtime_mode.bypass_schedule:
            return True

        now_utc = get_current_datetime().astimezone(datetime.timezone.utc)
        age = now_utc - run_timestamp_utc.astimezone(datetime.timezone.utc)
        return age <= _SNAPSHOT_FRESHNESS_THRESHOLD
