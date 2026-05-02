import datetime
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol

from market_data_library.core.crypto.coinalyze.type import (
    CoinalyzeFutureMarket,
    CoinalyzeHistoryPoint,
    CoinalyzeHistorySeries,
)

from src.service.crypto.coinalyze import CoinalyzeService
from src.service.crypto_signal.market_regime import (
    FUNDING_RATE_METRIC,
    OPEN_INTEREST_METRIC,
)
from src.service.crypto_signal.models import (
    CryptoSignalMarketRegimeMetric,
    CryptoSignalMarketRegimeSnapshot,
)


logger = logging.getLogger('Crypto signal market regime collector')
COINALYZE_PROVIDER = 'coinalyze'
BTC_ASSET_SYMBOL = 'BTC'
AGGREGATE_VENUE_SCOPE = 'aggregate'
AGGREGATE_INSTRUMENT_SCOPE = 'btc_perpetual_basket'
SOURCE_PAYLOAD_VERSION = 1


@dataclass(slots=True)
class _MetricSeries:
    open_interest_by_symbol: dict[str, CoinalyzeHistorySeries]
    funding_by_symbol: dict[str, CoinalyzeHistorySeries]


class _CoinalyzeMarketRegimeService(Protocol):
    async def get_future_markets(self) -> list[CoinalyzeFutureMarket]:
        ...

    async def get_open_interest_history(
        self,
        symbols: list[str],
        interval: str,
        from_timestamp_seconds: int,
        to_timestamp_seconds: int,
        convert_to_usd: bool = True,
    ) -> list[CoinalyzeHistorySeries]:
        ...

    async def get_funding_rate_history(
        self,
        symbols: list[str],
        interval: str,
        from_timestamp_seconds: int,
        to_timestamp_seconds: int,
    ) -> list[CoinalyzeHistorySeries]:
        ...


class CryptoSignalMarketRegimeCollector:
    def __init__(
        self, coinalyze_service: _CoinalyzeMarketRegimeService | None = None
    ) -> None:
        self.coinalyze_service = coinalyze_service

    async def collect_coinalyze_btc_snapshots(
        self,
        *,
        observed_at_utc: datetime.datetime,
        runtime_mode: str,
        symbols: list[str],
        interval: str,
        backfill_days: int,
    ) -> list[CryptoSignalMarketRegimeSnapshot]:
        coinalyze_service = self._get_coinalyze_service()
        if coinalyze_service is None:
            logger.warning('Skipping Coinalyze market-regime collection: API key absent')
            return []
        if len(symbols) == 0:
            logger.warning('Skipping Coinalyze market-regime collection: no symbols configured')
            return []

        observed_at_utc = observed_at_utc.astimezone(datetime.timezone.utc)
        from_timestamp_seconds = int(
            (observed_at_utc - datetime.timedelta(days=backfill_days)).timestamp()
        )
        to_timestamp_seconds = int(observed_at_utc.timestamp())
        metadata_by_symbol = await self._load_market_metadata(
            coinalyze_service=coinalyze_service,
            symbols=symbols,
        )
        # Metadata is the guardrail that prevents ETH or dated futures from
        # being mislabeled as BTC regime context, so missing metadata fails closed.
        selected_symbols = self._filter_btc_perpetual_symbols(
            symbols=symbols,
            metadata_by_symbol=metadata_by_symbol,
        )
        if len(selected_symbols) == 0:
            logger.warning(
                'Skipping Coinalyze market-regime collection: no configured BTC perpetual symbols'
            )
            return []
        series = await self._load_metric_series(
            coinalyze_service=coinalyze_service,
            symbols=selected_symbols,
            interval=interval,
            from_timestamp_seconds=from_timestamp_seconds,
            to_timestamp_seconds=to_timestamp_seconds,
        )

        raw_snapshots = [
            self._build_raw_snapshot(
                observed_at_utc=observed_at_utc,
                runtime_mode=runtime_mode,
                symbol=symbol,
                market=metadata_by_symbol.get(symbol),
                interval=interval,
                open_interest_series=series.open_interest_by_symbol.get(symbol),
                funding_series=series.funding_by_symbol.get(symbol),
            )
            for symbol in selected_symbols
        ]
        raw_snapshots = [
            snapshot for snapshot in raw_snapshots if len(snapshot.metrics) > 0
        ]
        # Raw venue facts preserve provenance for audits and later provider
        # comparisons; operator output reads only the aggregate basket.
        aggregate_snapshot = self._build_aggregate_snapshot(
            observed_at_utc=observed_at_utc,
            runtime_mode=runtime_mode,
            interval=interval,
            series=series,
        )
        if len(aggregate_snapshot.metrics) == 0:
            return raw_snapshots
        return [*raw_snapshots, aggregate_snapshot]

    def _get_coinalyze_service(self) -> _CoinalyzeMarketRegimeService | None:
        if self.coinalyze_service is not None:
            return self.coinalyze_service
        coinalyze_service = CoinalyzeService()
        if not coinalyze_service.is_configured():
            return None
        return coinalyze_service

    async def _load_market_metadata(
        self,
        *,
        coinalyze_service: _CoinalyzeMarketRegimeService,
        symbols: list[str],
    ) -> dict[str, CoinalyzeFutureMarket]:
        try:
            markets = await coinalyze_service.get_future_markets()
        except Exception:
            logger.warning(
                'Failed to load Coinalyze futures metadata; skipping market-regime collection',
                exc_info=True,
            )
            return {}
        requested_symbols = set(symbols)
        return {
            market.symbol: market
            for market in markets
            if market.symbol in requested_symbols
        }

    def _filter_btc_perpetual_symbols(
        self,
        *,
        symbols: list[str],
        metadata_by_symbol: dict[str, CoinalyzeFutureMarket],
    ) -> list[str]:
        # A blank map means metadata was unavailable, not that every configured
        # symbol is valid for the BTC perpetual basket.
        if len(metadata_by_symbol) == 0:
            return []

        selected_symbols = []
        for symbol in symbols:
            market = metadata_by_symbol.get(symbol)
            if market is None:
                logger.warning(
                    'Dropping Coinalyze market-regime symbol %s: futures metadata missing',
                    symbol,
                )
                continue
            if market.base_asset.upper() != BTC_ASSET_SYMBOL or not market.is_perpetual:
                logger.warning(
                    'Dropping Coinalyze market-regime symbol %s: not a BTC perpetual',
                    symbol,
                )
                continue
            selected_symbols.append(symbol)
        return selected_symbols

    async def _load_metric_series(
        self,
        *,
        coinalyze_service: _CoinalyzeMarketRegimeService,
        symbols: list[str],
        interval: str,
        from_timestamp_seconds: int,
        to_timestamp_seconds: int,
    ) -> _MetricSeries:
        open_interest = await coinalyze_service.get_open_interest_history(
            symbols=symbols,
            interval=interval,
            from_timestamp_seconds=from_timestamp_seconds,
            to_timestamp_seconds=to_timestamp_seconds,
            convert_to_usd=True,
        )
        funding = await coinalyze_service.get_funding_rate_history(
            symbols=symbols,
            interval=interval,
            from_timestamp_seconds=from_timestamp_seconds,
            to_timestamp_seconds=to_timestamp_seconds,
        )
        return _MetricSeries(
            open_interest_by_symbol={entry.symbol: entry for entry in open_interest},
            funding_by_symbol={entry.symbol: entry for entry in funding},
        )

    def _build_raw_snapshot(
        self,
        *,
        observed_at_utc: datetime.datetime,
        runtime_mode: str,
        symbol: str,
        market: CoinalyzeFutureMarket | None,
        interval: str,
        open_interest_series: CoinalyzeHistorySeries | None,
        funding_series: CoinalyzeHistorySeries | None,
    ) -> CryptoSignalMarketRegimeSnapshot:
        metrics = [
            *self._build_metrics(
                metric_name=OPEN_INTEREST_METRIC,
                unit='usd',
                points=[] if open_interest_series is None else open_interest_series.history,
            ),
            *self._build_metrics(
                metric_name=FUNDING_RATE_METRIC,
                unit='percent',
                points=[] if funding_series is None else funding_series.history,
            ),
        ]
        return CryptoSignalMarketRegimeSnapshot(
            observed_at_utc=observed_at_utc,
            runtime_mode=runtime_mode,
            provider=COINALYZE_PROVIDER,
            asset_symbol=BTC_ASSET_SYMBOL,
            venue_scope=self._venue_scope(market=market),
            instrument_scope=symbol,
            interval=interval,
            source_payload_version=SOURCE_PAYLOAD_VERSION,
            metrics=metrics,
        )

    def _build_aggregate_snapshot(
        self,
        *,
        observed_at_utc: datetime.datetime,
        runtime_mode: str,
        interval: str,
        series: _MetricSeries,
    ) -> CryptoSignalMarketRegimeSnapshot:
        metrics = [
            # OI is additive across venues once converted to USD; funding is a
            # rate, so the first slice averages it rather than summing.
            *self._build_aggregate_metrics(
                metric_name=OPEN_INTEREST_METRIC,
                unit='usd',
                series_by_symbol=series.open_interest_by_symbol,
                aggregation='sum',
            ),
            *self._build_aggregate_metrics(
                metric_name=FUNDING_RATE_METRIC,
                unit='percent',
                series_by_symbol=series.funding_by_symbol,
                aggregation='mean',
            ),
        ]
        return CryptoSignalMarketRegimeSnapshot(
            observed_at_utc=observed_at_utc,
            runtime_mode=runtime_mode,
            provider=COINALYZE_PROVIDER,
            asset_symbol=BTC_ASSET_SYMBOL,
            venue_scope=AGGREGATE_VENUE_SCOPE,
            instrument_scope=AGGREGATE_INSTRUMENT_SCOPE,
            interval=interval,
            source_payload_version=SOURCE_PAYLOAD_VERSION,
            metrics=metrics,
        )

    def _build_metrics(
        self,
        *,
        metric_name: str,
        unit: str,
        points: list[CoinalyzeHistoryPoint],
    ) -> list[CryptoSignalMarketRegimeMetric]:
        return [
            CryptoSignalMarketRegimeMetric(
                metric_name=metric_name,
                metric_value=point.c,
                unit=unit,
                source_timestamp_utc=datetime.datetime.fromtimestamp(
                    point.t,
                    tz=datetime.timezone.utc,
                ),
            )
            for point in points
        ]

    def _build_aggregate_metrics(
        self,
        *,
        metric_name: str,
        unit: str,
        series_by_symbol: dict[str, CoinalyzeHistorySeries],
        aggregation: str,
    ) -> list[CryptoSignalMarketRegimeMetric]:
        values_by_timestamp = defaultdict(list)
        for series in series_by_symbol.values():
            for point in series.history:
                # Aggregate only facts observed for the same provider timestamp;
                # carrying forward stale venue values would invent precision.
                values_by_timestamp[point.t].append(point.c)

        metrics = []
        for timestamp in sorted(values_by_timestamp):
            values = values_by_timestamp[timestamp]
            # OI is a notional exposure pool and can be summed across venues.
            # Funding is a rate, so basket context uses an equal-weight mean.
            metric_value = (
                sum(values)
                if aggregation == 'sum'
                else sum(values) / len(values)
            )
            metrics.append(
                CryptoSignalMarketRegimeMetric(
                    metric_name=metric_name,
                    metric_value=metric_value,
                    unit=unit,
                    source_timestamp_utc=datetime.datetime.fromtimestamp(
                        timestamp,
                        tz=datetime.timezone.utc,
                    ),
                )
            )
        return metrics

    def _venue_scope(self, market: CoinalyzeFutureMarket | None) -> str:
        if market is None or market.exchange == '':
            return 'unknown'
        return market.exchange.lower()
