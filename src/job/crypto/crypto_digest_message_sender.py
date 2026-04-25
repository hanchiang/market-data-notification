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
from src.runtime.runtime_mode import DEFAULT_RUNTIME_MODE
from src.service.crypto_signal.backfill import CryptoSignalBackfillService
from src.service.crypto_signal.repository import CryptoSignalRepository
from src.service.crypto_signal.snapshot_builder import build_snapshot
from src.type.market_data_type import MarketDataType
from src.util.date_util import get_current_datetime
from src.util.exception import get_exception_message
from src.util.my_telegram import format_messages_to_telegram


logger = logging.getLogger('Crypto digest message sender')
_SIGNAL_BACKFILL_DAYS = 30


class CryptoDigestMessageSender(MessageSenderWrapper):
    def __init__(self, runtime_mode=None):
        super().__init__(runtime_mode=runtime_mode)
        self.cmc_service = Dependencies.get_crypto_stats_service()
        self.sentiment_service = Dependencies.get_crypto_sentiment_service()
        self.signal_repository = CryptoSignalRepository(
            runtime_mode=self.runtime_mode
        )
        self.tracked_universe_entries = config.get_crypto_signal_tracked_universe()
        self.watchlist_entries = config.get_crypto_signal_watchlist()

    @property
    def data_source(self):
        return 'CMC + Alternative.me'

    @property
    def market_data_type(self):
        return MarketDataType.CRYPTO

    async def format_message(self) -> List[str]:
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
        tracked_universe_coin_details = await self._load_tracked_universe_coin_details()

        snapshot = self._build_signal_snapshot(
            current=current,
            runtime_mode=getattr(self, 'runtime_mode', DEFAULT_RUNTIME_MODE),
            sentiment=sentiment,
            strongest_sector=strongest_sector,
            weakest_sector=weakest_sector,
            standout_entries=standout_entries,
            standout_coin_details=standout_coin_details,
            sector_details=sector_details,
            sector_detail_coin_details=sector_detail_coin_details,
            tracked_universe_coin_details=tracked_universe_coin_details,
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
            ),
            tracked_universe_coin_details=tracked_universe_coin_details,
            watchlist_entries=watchlist_entries,
        )

    async def _backfill_signal_history(
        self,
        current,
        snapshot,
    ) -> None:
        repository = getattr(
            self,
            'signal_repository',
            CryptoSignalRepository(runtime_mode=self.runtime_mode),
        )
        backfill_service = getattr(
            self,
            'signal_backfill_service',
            CryptoSignalBackfillService(cmc_service=self.cmc_service),
        )
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
        repository = getattr(
            self,
            'signal_repository',
            CryptoSignalRepository(runtime_mode=self.runtime_mode),
        )
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

    async def _load_tracked_universe_coin_details(self) -> Dict[int, cmc_type.CoinDetail]:
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
                )
            ],
            log_context='tracked universe coin enrichment',
        )

    @staticmethod
    def _get_persisted_universe_entries(
        tracked_universe_entries: List[tuple[str, int]],
        watchlist_entries: List[tuple[str, int]],
    ) -> List[tuple[str, int]]:
        merged_entries = {
            coin_id: (symbol, coin_id)
            for symbol, coin_id in tracked_universe_entries
        }
        for symbol, coin_id in watchlist_entries:
            merged_entries.setdefault(coin_id, (symbol, coin_id))
        return list(merged_entries.values())
