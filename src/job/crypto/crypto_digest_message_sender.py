import asyncio
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
from src.service.crypto_signal.repository import CryptoSignalRepository
from src.service.crypto_signal.snapshot_builder import build_snapshot
from src.type.market_data_type import MarketDataType
from src.util.date_util import get_current_datetime
from src.util.exception import get_exception_message
from src.util.my_telegram import format_messages_to_telegram


logger = logging.getLogger('Crypto digest message sender')


class CryptoDigestMessageSender(MessageSenderWrapper):
    def __init__(self, runtime_mode=None):
        super().__init__(runtime_mode=runtime_mode)
        self.cmc_service = Dependencies.get_crypto_stats_service()
        self.sentiment_service = Dependencies.get_crypto_sentiment_service()
        self.signal_repository = CryptoSignalRepository()
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

        persistence_failure_message = self._persist_signal_snapshot(
            current=current,
            sentiment=sentiment,
            strongest_sector=strongest_sector,
            weakest_sector=weakest_sector,
            standout_entries=standout_entries,
            standout_coin_details=standout_coin_details,
            sector_details=sector_details,
            sector_detail_coin_details=sector_detail_coin_details,
            tracked_universe_coin_details=tracked_universe_coin_details,
        )
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

    def _persist_signal_snapshot(
        self,
        current,
        sentiment,
        strongest_sector,
        weakest_sector,
        standout_entries,
        standout_coin_details,
        sector_details,
        sector_detail_coin_details,
        tracked_universe_coin_details,
    ) -> str | None:
        runtime_mode = getattr(self, 'runtime_mode', DEFAULT_RUNTIME_MODE)
        repository = getattr(self, 'signal_repository', CryptoSignalRepository())
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
        snapshot = build_snapshot(
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
