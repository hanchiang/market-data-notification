import asyncio
import datetime
import logging
from collections import OrderedDict

from market_data_library.types import cmc_type

from src.service.crypto.crypto_stats import CryptoStatsService
from src.service.crypto_signal.models import (
    CryptoSignalCoinSnapshot,
    CryptoSignalRunRecord,
    CryptoSignalSnapshot,
)
from src.service.crypto_signal.repository import SNAPSHOT_VERSION


logger = logging.getLogger('Crypto signal backfill')

# Backfill only seeds enough retained history for phase-1 operator scoring; the
# live crypto job remains the source of truth for current snapshots and delivery.
BACKFILL_SOURCE_NAME = 'CMC historical bootstrap'
BACKFILL_RUNTIME_MODE = 'bootstrap'
BACKFILL_INTERVAL = '24h'


class CryptoSignalBackfillService:
    def __init__(self, cmc_service: CryptoStatsService | None = None) -> None:
        self.cmc_service = cmc_service or CryptoStatsService()

    async def build_snapshots(
        self,
        coin_entries: list[tuple[str, int]],
        watchlist_coin_ids: set[int],
        current_timestamp_utc: datetime.datetime,
        days: int,
    ) -> list[CryptoSignalSnapshot]:
        unique_entries = list(dict.fromkeys(coin_entries))
        if len(unique_entries) == 0:
            return []

        history_results = await asyncio.gather(
            *[
                self.cmc_service.get_ohlcv_historical(
                    id=coin_id,
                    interval=BACKFILL_INTERVAL,
                )
                for _symbol, coin_id in unique_entries
            ],
            return_exceptions=True,
        )

        grouped_snapshots: OrderedDict[datetime.datetime, CryptoSignalSnapshot] = (
            OrderedDict()
        )
        window_start_utc = current_timestamp_utc - datetime.timedelta(days=days)

        for (symbol, coin_id), history_result in zip(
            unique_entries,
            history_results,
            strict=False,
        ):
            if isinstance(history_result, Exception):
                logger.warning(
                    'Skipping crypto signal backfill for %s (%s): %s',
                    symbol,
                    coin_id,
                    history_result,
                )
                continue
            self._merge_coin_history(
                grouped_snapshots=grouped_snapshots,
                symbol=symbol,
                coin_id=coin_id,
                history=history_result,
                watchlist_coin_ids=watchlist_coin_ids,
                window_start_utc=window_start_utc,
                current_timestamp_utc=current_timestamp_utc,
            )

        return list(grouped_snapshots.values())

    def _merge_coin_history(
        self,
        grouped_snapshots: OrderedDict[datetime.datetime, CryptoSignalSnapshot],
        symbol: str,
        coin_id: int,
        history: cmc_type.OHLCVHistorical,
        watchlist_coin_ids: set[int],
        window_start_utc: datetime.datetime,
        current_timestamp_utc: datetime.datetime,
    ) -> None:
        sorted_quotes = sorted(
            history.quotes,
            key=lambda quote: self._parse_quote_timestamp(quote),
        )
        previous_quote: cmc_type.OHLCVHistoricalQuote | None = None

        for quote in sorted_quotes:
            quote_timestamp_utc = self._parse_quote_timestamp(quote)
            # Provider quote timestamps follow candle/bucket boundaries rather
            # than "time fetched". For 24h history, the API can still return
            # today's in-progress candle labeled with the day-end timestamp,
            # which may be later than the current live run time. Bootstrap rows
            # should only represent completed past observations, so skip the
            # current/future bucket here and let the live run persist its own
            # snapshot separately.
            if quote_timestamp_utc >= current_timestamp_utc:
                previous_quote = quote
                continue
            if quote_timestamp_utc < window_start_utc:
                # Preserve the last earlier candle so the first retained row can
                # still compute a 24h change from real provider history.
                previous_quote = quote
                continue

            snapshot = grouped_snapshots.get(quote_timestamp_utc)
            if snapshot is None:
                # Group all coins that share the same historical timestamp into
                # one synthetic run so bootstrap data matches the normal
                # one-run-many-coins storage model.
                snapshot = CryptoSignalSnapshot(
                    run=CryptoSignalRunRecord(
                        run_timestamp_utc=quote_timestamp_utc,
                        runtime_mode=BACKFILL_RUNTIME_MODE,
                        source_name=BACKFILL_SOURCE_NAME,
                        snapshot_version=SNAPSHOT_VERSION,
                        sentiment_now_value=None,
                        sentiment_now_label=None,
                        sentiment_yesterday_value=None,
                        sentiment_last_week_value=None,
                        sentiment_7d_avg=None,
                        sentiment_30d_avg=None,
                        strongest_sector_id=None,
                        strongest_sector_name=None,
                        strongest_sector_avg_price_change_24h=None,
                        strongest_sector_market_change_24h=None,
                        strongest_sector_volume_change_24h=None,
                        strongest_sector_gainers_num=None,
                        strongest_sector_losers_num=None,
                        weakest_sector_id=None,
                        weakest_sector_name=None,
                        weakest_sector_avg_price_change_24h=None,
                        weakest_sector_market_change_24h=None,
                        weakest_sector_volume_change_24h=None,
                        weakest_sector_gainers_num=None,
                        weakest_sector_losers_num=None,
                    ),
                    coins=[],
                )
                grouped_snapshots[quote_timestamp_utc] = snapshot

            snapshot.coins.append(
                CryptoSignalCoinSnapshot(
                    coin_id=coin_id,
                    symbol=history.symbol or symbol,
                    name=history.name,
                    price_usd=quote.quote.close,
                    price_change_24h=self._calculate_change_pct(
                        current_value=quote.quote.close,
                        previous_value=(
                            previous_quote.quote.close if previous_quote is not None else None
                        ),
                    ),
                    volume_24h=quote.quote.volume,
                    volume_change_pct_24h=self._calculate_change_pct(
                        current_value=quote.quote.volume,
                        previous_value=(
                            previous_quote.quote.volume if previous_quote is not None else None
                        ),
                    ),
                    is_watchlist=coin_id in watchlist_coin_ids,
                    context_tags=('watchlist',) if coin_id in watchlist_coin_ids else (),
                )
            )
            previous_quote = quote

    @staticmethod
    def _parse_quote_timestamp(
        quote: cmc_type.OHLCVHistoricalQuote,
    ) -> datetime.datetime:
        raw_value = quote.quote.timestamp or quote.timeClose or quote.timeOpen
        return datetime.datetime.fromisoformat(raw_value.replace('Z', '+00:00'))

    @staticmethod
    def _calculate_change_pct(
        current_value: float | None,
        previous_value: float | None,
    ) -> float | None:
        if current_value is None or previous_value is None or previous_value == 0:
            return None
        return ((current_value - previous_value) / previous_value) * 100
