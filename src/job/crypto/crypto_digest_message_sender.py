import asyncio
import datetime
import logging
from typing import Dict, List, Optional

from market_data_library.types import cmc_type

from src.config import config
from src.dependencies import Dependencies
from src.job.crypto.crypto_digest_formatter import (
    build_digest_message,
    collect_sector_detail_coin_ids,
    get_standout_entries,
    should_emit_sector_detail_message,
)
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.notification_destination.telegram_notification import send_message_to_admin
from src.service.crypto_signal.market_regime_collector import (
    CryptoSignalMarketRegimeCollector,
)
from src.service.crypto_signal.backfill import CryptoSignalBackfillService
from src.service.crypto_signal.models import CALIBRATION_FOLLOW_UP_CONTEXT_TAG
from src.service.crypto_signal.repository import (
    BTC_COIN_ID,
    ETH_COIN_ID,
    CryptoSignalRepository,
)
from src.service.crypto_signal.snapshot_builder import build_snapshot
from src.type.market_data_type import MarketDataType
from src.util.date_util import get_current_datetime
from src.util.exception import get_exception_message
from src.util.my_telegram import format_messages_to_telegram


logger = logging.getLogger('Crypto digest message sender')
_SIGNAL_BACKFILL_DAYS = 30


class CryptoDigestMessageSender(MessageSenderWrapper):
    def __init__(
        self,
        runtime_mode=None,
        cmc_service=None,
        sentiment_service=None,
        signal_repository=None,
        signal_backfill_service=None,
        market_regime_collector=None,
        market_regime_repository=None,
    ):
        super().__init__(runtime_mode=runtime_mode)
        self.cmc_service = cmc_service
        self.sentiment_service = sentiment_service
        self.signal_repository = (
            CryptoSignalRepository(runtime_mode=self.runtime_mode)
            if signal_repository is None
            else signal_repository
        )
        self.signal_backfill_service = signal_backfill_service
        self.market_regime_collector = market_regime_collector
        self.market_regime_repository = market_regime_repository
        self.tracked_universe_entries = config.get_crypto_signal_tracked_universe()
        self.watchlist_entries = config.get_crypto_signal_watchlist()

    @property
    def data_source(self):
        return 'CMC + Alternative.me'

    @property
    def market_data_type(self):
        return MarketDataType.CRYPTO

    async def format_message(self) -> List[str]:
        self._ensure_runtime_dependencies()
        current = get_current_datetime()
        sentiment = await self.sentiment_service.get_crypto_fear_greed_index()
        strongest_sector, weakest_sector = await self._load_sector_snapshots()
        spotlight = await self.cmc_service.get_spotlight()

        standout_entries = get_standout_entries(spotlight)
        standout_coin_details = await self._load_coin_details(
            coin_ids=[coin.id for coin, _reasons in standout_entries],
            log_context='spotlight coin detail enrichment',
        )
        sector_details = await self._load_sector_details(
            strongest_sector=strongest_sector,
            weakest_sector=weakest_sector,
        )
        sector_detail_coin_details = await self._load_sector_detail_coin_details(
            strongest_sector=strongest_sector,
            weakest_sector=weakest_sector,
            sector_details=sector_details,
        )
        candidate_follow_up_entries = self._load_candidate_follow_up_entries(
            current=current
        )
        tracked_universe_coin_details = await self._load_tracked_universe_coin_details(
            extra_entries=candidate_follow_up_entries
        )

        snapshot = self._build_signal_snapshot(
            current=current,
            runtime_mode=self.runtime_mode,
            sentiment=sentiment,
            strongest_sector=strongest_sector,
            weakest_sector=weakest_sector,
            standout_entries=standout_entries,
            standout_coin_details=standout_coin_details,
            sector_details=sector_details,
            sector_detail_coin_details=sector_detail_coin_details,
            tracked_universe_coin_details=tracked_universe_coin_details,
            candidate_follow_up_entries=candidate_follow_up_entries,
        )
        self._mark_calibration_follow_up_only_coins(
            snapshot=snapshot,
            candidate_follow_up_entries=candidate_follow_up_entries,
        )
        await self._backfill_signal_history(
            current=current,
            snapshot=snapshot,
        )
        persistence_failure_message = self._persist_signal_snapshot(snapshot=snapshot)
        if persistence_failure_message is not None:
            await send_message_to_admin(
                message=format_messages_to_telegram(
                    [persistence_failure_message]
                ),
                market_data_type=MarketDataType.CRYPTO,
            )
        else:
            self._resolve_due_candidate_outcomes(current=current)
        await self._persist_market_regime_snapshots(current=current)

        digest_message = build_digest_message(
            current=current,
            sentiment=sentiment,
            strongest_sector=strongest_sector,
            weakest_sector=weakest_sector,
            standout_entries=standout_entries,
            standout_coin_details=standout_coin_details,
            sector_details=sector_details,
            sector_detail_coin_details=sector_detail_coin_details,
        )
        return [digest_message]

    def _ensure_runtime_dependencies(self) -> None:
        if self.cmc_service is None:
            self.cmc_service = Dependencies.get_crypto_stats_service()
        if self.sentiment_service is None:
            self.sentiment_service = Dependencies.get_crypto_sentiment_service()
        if self.signal_backfill_service is None:
            self.signal_backfill_service = CryptoSignalBackfillService(
                cmc_service=self.cmc_service
            )

    def _build_signal_snapshot(
        self,
        current,
        runtime_mode,
        sentiment,
        strongest_sector,
        weakest_sector,
        standout_entries,
        standout_coin_details,
        sector_details,
        sector_detail_coin_details,
        tracked_universe_coin_details,
        candidate_follow_up_entries,
    ):
        tracked_universe_entries = getattr(
            self,
            'tracked_universe_entries',
            config.get_crypto_signal_tracked_universe(),
        )
        watchlist_entries = getattr(
            self,
            'watchlist_entries',
            config.get_crypto_signal_watchlist(),
        )
        return build_snapshot(
            current=current,
            runtime_mode=runtime_mode,
            source_name=self.data_source,
            sentiment=sentiment,
            strongest_sector=strongest_sector,
            weakest_sector=weakest_sector,
            standout_entries=standout_entries,
            standout_coin_details=standout_coin_details,
            sector_details=sector_details,
            sector_detail_coin_details=sector_detail_coin_details,
            tracked_universe_entries=self._get_persisted_universe_entries(
                tracked_universe_entries=tracked_universe_entries,
                watchlist_entries=watchlist_entries,
                extra_entries=candidate_follow_up_entries,
            ),
            tracked_universe_coin_details=tracked_universe_coin_details,
            watchlist_entries=watchlist_entries,
        )

    async def _backfill_signal_history(
        self,
        current,
        snapshot,
    ) -> None:
        repository = self.signal_repository
        backfill_service = self.signal_backfill_service
        tracked_universe_entries = getattr(
            self,
            'tracked_universe_entries',
            config.get_crypto_signal_tracked_universe(),
        )
        watchlist_entries = getattr(
            self,
            'watchlist_entries',
            config.get_crypto_signal_watchlist(),
        )
        current_timestamp_utc = current.astimezone(datetime.timezone.utc)
        persisted_entries = self._build_backfill_entries(
            snapshot=snapshot,
            tracked_universe_entries=tracked_universe_entries,
            watchlist_entries=watchlist_entries,
        )
        if len(persisted_entries) == 0:
            return

        history_start_utc = current_timestamp_utc - datetime.timedelta(
            days=_SIGNAL_BACKFILL_DAYS
        )

        try:
            repository.init_schema()
            observation_counts = repository.get_coin_observation_counts_since(
                coin_ids=[coin_id for _symbol, coin_id in persisted_entries],
                start_timestamp_utc=history_start_utc,
            )
            # Only bootstrap coins with no retained observations yet. Once a coin
            # has entered the store, future runs should extend it forward rather
            # than re-requesting and re-merging the whole historical window.
            missing_entries = [
                entry
                for entry in persisted_entries
                if observation_counts.get(entry[1], 0) == 0
            ]
            if len(missing_entries) == 0:
                return

            # Bootstrap before the live snapshot write so first-seen coins can
            # immediately score against retained history in the same run.
            backfill_snapshots = await backfill_service.build_snapshots(
                coin_entries=missing_entries,
                watchlist_coin_ids={coin_id for _symbol, coin_id in watchlist_entries},
                current_timestamp_utc=current_timestamp_utc,
                days=_SIGNAL_BACKFILL_DAYS,
            )
            for backfill_snapshot in backfill_snapshots:
                repository.save_or_merge_snapshot(backfill_snapshot)
        except Exception:
            logger.exception(
                'Crypto signal backfill failed; continuing with live snapshot only'
            )

    def _persist_signal_snapshot(
        self,
        snapshot,
    ) -> str | None:
        repository = self.signal_repository
        try:
            repository.save_snapshot(snapshot)
        except Exception as error:
            logger.exception('Failed to persist crypto signal snapshot')
            return get_exception_message(
                error,
                cls=self.__class__.__name__,
                should_escape_markdown=True,
            )
        return None

    def _resolve_due_candidate_outcomes(
        self,
        current,
    ) -> None:
        try:
            self.signal_repository.resolve_due_candidate_outcomes(
                runtime_mode=self._runtime_mode_label(),
                current_timestamp_utc=current.astimezone(datetime.timezone.utc),
            )
        except Exception:
            logger.warning(
                'Crypto signal candidate outcome resolution failed; continuing digest',
                exc_info=True,
            )

    def _mark_calibration_follow_up_only_coins(
        self,
        snapshot,
        candidate_follow_up_entries: List[tuple[str, int]],
    ) -> None:
        if len(candidate_follow_up_entries) == 0:
            return
        normal_entries = self._get_persisted_universe_entries(
            tracked_universe_entries=self.tracked_universe_entries,
            watchlist_entries=self.watchlist_entries,
        )
        normal_coin_ids = {coin_id for _symbol, coin_id in normal_entries}
        follow_up_only_coin_ids = {
            coin_id
            for _symbol, coin_id in candidate_follow_up_entries
            if coin_id not in normal_coin_ids
        }
        for coin in snapshot.coins:
            if coin.coin_id not in follow_up_only_coin_ids:
                continue
            if len(coin.context_tags) > 0:
                continue
            # Follow-up-only rows are retained for calibration outcome coverage,
            # but this tag keeps them out of operator ranking unless they become
            # normally tracked/watchlisted again.
            coin.context_tags = (
                *coin.context_tags,
                CALIBRATION_FOLLOW_UP_CONTEXT_TAG,
            )

    async def _persist_market_regime_snapshots(
        self,
        current,
    ) -> None:
        if not config.is_crypto_signal_market_regime_enabled():
            return

        try:
            # Regime collection is optional operator context; failures must not
            # suppress the public digest or the phase-1 signal snapshot.
            provider = config.get_crypto_signal_market_regime_provider()
            if provider != 'coinalyze':
                logger.warning(
                    'Skipping crypto signal market-regime collection for unsupported provider %s',
                    provider,
                )
                return
            collector = self.market_regime_collector
            if collector is None:
                collector = CryptoSignalMarketRegimeCollector()
            repository = self.market_regime_repository or self.signal_repository
            snapshots = await collector.collect_coinalyze_btc_snapshots(
                observed_at_utc=current,
                runtime_mode=self._runtime_mode_label(),
                symbols=config.get_crypto_signal_market_regime_coinalyze_symbols(),
                interval=config.get_crypto_signal_market_regime_interval(),
                backfill_days=config.get_crypto_signal_market_regime_backfill_days(),
            )
            for snapshot in snapshots:
                repository.save_market_regime_snapshot(snapshot)
        except Exception:
            logger.warning(
                'Crypto signal market-regime collection failed; continuing digest',
                exc_info=True,
            )

    def _runtime_mode_label(self) -> str:
        return 'test' if self.runtime_mode.is_test_mode else 'prod'

    def _build_backfill_entries(
        self,
        snapshot,
        tracked_universe_entries,
        watchlist_entries,
    ) -> list[tuple[str, int]]:
        ordered_entries: dict[int, tuple[str, int]] = {}
        # Seed bootstrap coverage from the configured persistence universe, then
        # extend it with whatever the live snapshot surfaced without duplicating
        # coin ids across those sources.
        for symbol, coin_id in self._get_persisted_universe_entries(
            tracked_universe_entries=tracked_universe_entries,
            watchlist_entries=watchlist_entries,
        ):
            ordered_entries.setdefault(coin_id, (symbol, coin_id))
        for coin in snapshot.coins:
            ordered_entries.setdefault(coin.coin_id, (coin.symbol, coin.coin_id))
        return list(ordered_entries.values())

    async def _load_sector_snapshots(
        self,
    ) -> tuple[
        Optional[cmc_type.Sector24hChange],
        Optional[cmc_type.Sector24hChange],
    ]:
        strongest_sectors = await self.cmc_service.get_sectors_24h_change(
            sort_by='avg_price_change',
            sort_direction='desc',
            limit=1,
        )
        weakest_sectors = await self.cmc_service.get_sectors_24h_change(
            sort_by='avg_price_change',
            sort_direction='asc',
            limit=1,
        )
        strongest_sector = strongest_sectors[0] if strongest_sectors else None
        weakest_sector = weakest_sectors[0] if weakest_sectors else None
        return strongest_sector, weakest_sector

    async def _load_sector_details(
        self,
        strongest_sector: Optional[cmc_type.Sector24hChange],
        weakest_sector: Optional[cmc_type.Sector24hChange],
    ) -> Dict[str, cmc_type.SectorDetail]:
        sectors = []
        for sector in [strongest_sector, weakest_sector]:
            if sector is None or not sector.sectorId:
                continue
            if any(existing.sectorId == sector.sectorId for existing in sectors):
                continue
            sectors.append(sector)

        if len(sectors) == 0:
            return {}

        details = await asyncio.gather(
            *[
                self.cmc_service.get_sector_detail(sector_id=sector.sectorId)
                for sector in sectors
            ],
            return_exceptions=True,
        )
        sector_details = {}
        for sector, detail in zip(sectors, details, strict=False):
            if isinstance(detail, Exception):
                logger.warning(
                    'Skipping sector detail enrichment for %s (%s): %s',
                    sector.title,
                    sector.sectorId,
                    detail,
                )
                continue
            sector_details[sector.sectorId] = detail
        return sector_details

    async def _load_sector_detail_coin_details(
        self,
        strongest_sector: Optional[cmc_type.Sector24hChange],
        weakest_sector: Optional[cmc_type.Sector24hChange],
        sector_details: Dict[str, cmc_type.SectorDetail],
    ) -> Dict[int, cmc_type.CoinDetail]:
        if not should_emit_sector_detail_message(
            strongest_sector=strongest_sector,
            weakest_sector=weakest_sector,
            sector_details=sector_details,
        ):
            return {}

        coin_ids = collect_sector_detail_coin_ids(
            strongest_sector=strongest_sector,
            weakest_sector=weakest_sector,
            sector_details=sector_details,
        )
        return await self._load_coin_details(
            coin_ids=coin_ids,
            log_context='sector detail coin enrichment',
        )

    async def _load_coin_details(
        self,
        coin_ids: List[int],
        log_context: str,
    ) -> Dict[int, cmc_type.CoinDetail]:
        unique_coin_ids = list(dict.fromkeys(coin_ids))
        if len(unique_coin_ids) == 0:
            return {}

        details = await asyncio.gather(
            *[
                self.cmc_service.get_coin_detail(id=coin_id)
                for coin_id in unique_coin_ids
            ],
            return_exceptions=True,
        )
        coin_details = {}
        for coin_id, detail in zip(unique_coin_ids, details, strict=False):
            if isinstance(detail, Exception):
                logger.warning(
                    'Skipping %s for %s: %s',
                    log_context,
                    coin_id,
                    detail,
                )
                continue
            coin_details[coin_id] = detail
        return coin_details

    async def _load_tracked_universe_coin_details(
        self,
        extra_entries: List[tuple[str, int]] | None = None,
    ) -> Dict[int, cmc_type.CoinDetail]:
        tracked_universe_entries = getattr(
            self,
            'tracked_universe_entries',
            config.get_crypto_signal_tracked_universe(),
        )
        watchlist_entries = getattr(
            self,
            'watchlist_entries',
            config.get_crypto_signal_watchlist(),
        )
        return await self._load_coin_details(
            coin_ids=[
                coin_id
                for _symbol, coin_id in self._get_persisted_universe_entries(
                    tracked_universe_entries=tracked_universe_entries,
                    watchlist_entries=watchlist_entries,
                    extra_entries=extra_entries,
                )
            ],
            log_context='tracked universe coin enrichment',
        )

    def _load_candidate_follow_up_entries(
        self,
        current,
    ) -> List[tuple[str, int]]:
        try:
            due_entries = (
                self.signal_repository.get_unresolved_candidate_follow_up_entries(
                    runtime_mode=self._runtime_mode_label(),
                    current_timestamp_utc=current.astimezone(datetime.timezone.utc),
                )
            )
        except Exception:
            logger.warning(
                'Failed to load crypto signal candidate follow-up entries; continuing digest',
                exc_info=True,
            )
            return []
        if len(due_entries) == 0:
            return []
        # Outcome calibration compares candidates against BTC and ETH, so keep
        # benchmark prices present even if an operator removes them from the
        # normal tracked-universe env.
        return self._merge_coin_entries(
            [
                *due_entries,
                ('BTC', BTC_COIN_ID),
                ('ETH', ETH_COIN_ID),
            ]
        )

    @staticmethod
    def _get_persisted_universe_entries(
        tracked_universe_entries: List[tuple[str, int]],
        watchlist_entries: List[tuple[str, int]],
        extra_entries: List[tuple[str, int]] | None = None,
    ) -> List[tuple[str, int]]:
        return CryptoDigestMessageSender._merge_coin_entries(
            [
                *tracked_universe_entries,
                *watchlist_entries,
                *(extra_entries or []),
            ]
        )

    @staticmethod
    def _merge_coin_entries(
        entries: List[tuple[str, int]],
    ) -> List[tuple[str, int]]:
        merged_entries = {}
        for symbol, coin_id in entries:
            merged_entries.setdefault(coin_id, (symbol, coin_id))
        return list(merged_entries.values())
