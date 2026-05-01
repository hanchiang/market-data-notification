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
from src.service.crypto_signal.market_regime import (
    FUNDING_RATE_METRIC,
    OPEN_INTEREST_METRIC,
    build_market_regime_summary,
)
from src.service.crypto_signal.market_regime_collector import (
    AGGREGATE_INSTRUMENT_SCOPE,
    AGGREGATE_VENUE_SCOPE,
    COINALYZE_PROVIDER,
)
from src.service.crypto_signal.repository import CryptoSignalRepository
from src.service.crypto_signal.scorer import build_digest_view, get_window_start
from src.type.market_data_type import MarketDataType
from src.util.date_util import get_current_datetime
from src.util.exception import get_exception_message
from src.util.my_telegram import format_messages_to_telegram


logger = logging.getLogger('Crypto signal digest message sender')
_SNAPSHOT_FRESHNESS_THRESHOLD = datetime.timedelta(minutes=90)


class CryptoSignalDigestMessageSender(MessageSenderWrapper):
    def __init__(
        self,
        runtime_mode=None,
        signal_repository=None,
        watchlist_entries=None,
        tracked_universe_entries=None,
    ):
        super().__init__(runtime_mode=runtime_mode)
        self.signal_repository = signal_repository or CryptoSignalRepository(
            runtime_mode=self.runtime_mode
        )
        self.watchlist_entries = (
            config.get_crypto_signal_watchlist()
            if watchlist_entries is None
            else watchlist_entries
        )
        self.tracked_universe_entries = (
            config.get_crypto_signal_tracked_universe()
            if tracked_universe_entries is None
            else tracked_universe_entries
        )

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
        repository = self.signal_repository
        latest_snapshot = repository.get_latest_snapshot()
        if latest_snapshot is None:
            return []

        # The scheduled operator digest should not replay old signals long
        # after the source crypto job ran; the local CLI report remains the
        # explicit path for reviewing older retained history.
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
            tracked_universe_coin_ids={
                coin_id for _symbol, coin_id in self.tracked_universe_entries
            },
            window_label=window_label,
            limit=3,
            min_dynamic_price_usd=config.get_crypto_signal_dynamic_candidate_min_price_usd(),
            min_dynamic_volume_24h=config.get_crypto_signal_dynamic_candidate_min_volume_24h(),
            market_regime_summary=self._load_market_regime_summary(
                repository=repository,
                latest_snapshot=latest_snapshot,
                window_label=window_label,
            ),
        )
        return [build_crypto_signal_message(view)]

    def _is_fresh_enough(self, run_timestamp_utc: datetime.datetime) -> bool:
        runtime_mode = getattr(self, 'runtime_mode', DEFAULT_RUNTIME_MODE)
        if runtime_mode.bypass_schedule:
            return True

        now_utc = get_current_datetime().astimezone(datetime.timezone.utc)
        age = now_utc - run_timestamp_utc.astimezone(datetime.timezone.utc)
        return age <= _SNAPSHOT_FRESHNESS_THRESHOLD

    def _load_market_regime_summary(
        self,
        *,
        repository: CryptoSignalRepository,
        latest_snapshot,
        window_label: str,
    ):
        try:
            provider = config.get_crypto_signal_market_regime_provider()
            if provider != COINALYZE_PROVIDER:
                return None
            # Operator summaries consume the aggregate basket only; raw venue
            # rows are retained for provenance and later provider comparisons.
            metrics = repository.get_market_regime_metrics(
                runtime_mode=latest_snapshot.run.runtime_mode,
                start_timestamp_utc=get_window_start(
                    latest_snapshot,
                    window_label=window_label,
                ),
                end_timestamp_utc=latest_snapshot.run.run_timestamp_utc,
                metric_names=[OPEN_INTEREST_METRIC, FUNDING_RATE_METRIC],
                provider=COINALYZE_PROVIDER,
                venue_scope=AGGREGATE_VENUE_SCOPE,
                instrument_scope=AGGREGATE_INSTRUMENT_SCOPE,
                interval=config.get_crypto_signal_market_regime_interval(),
            )
        except Exception:
            logger.warning(
                'Failed to load crypto signal market-regime summary',
                exc_info=True,
            )
            return None
        if len(metrics) == 0:
            return None
        return build_market_regime_summary(metrics)
