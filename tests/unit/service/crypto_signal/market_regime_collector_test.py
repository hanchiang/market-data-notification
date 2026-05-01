import datetime
from unittest.mock import AsyncMock

import pytest
from market_data_library.core.crypto.coinalyze.type import (
    CoinalyzeFutureMarket,
    CoinalyzeHistoryPoint,
    CoinalyzeHistorySeries,
)

from src.service.crypto_signal.market_regime import (
    FUNDING_RATE_METRIC,
    OPEN_INTEREST_METRIC,
)
from src.service.crypto_signal.market_regime_collector import (
    AGGREGATE_INSTRUMENT_SCOPE,
    AGGREGATE_VENUE_SCOPE,
    CryptoSignalMarketRegimeCollector,
)


class _FakeCoinalyzeService:
    def __init__(self) -> None:
        self.get_future_markets = AsyncMock(
            return_value=[
                CoinalyzeFutureMarket(
                    symbol='BTCUSDT_PERP.A',
                    exchange='BINANCE',
                    base_asset='BTC',
                    quote_asset='USDT',
                    is_perpetual=True,
                ),
                CoinalyzeFutureMarket(
                    symbol='BTCUSD_PERP.0',
                    exchange='BITMEX',
                    base_asset='BTC',
                    quote_asset='USD',
                    is_perpetual=True,
                ),
                CoinalyzeFutureMarket(
                    symbol='ETHUSDT_PERP.A',
                    exchange='BINANCE',
                    base_asset='ETH',
                    quote_asset='USDT',
                    is_perpetual=True,
                ),
                CoinalyzeFutureMarket(
                    symbol='BTCUSDT.1',
                    exchange='BINANCE',
                    base_asset='BTC',
                    quote_asset='USDT',
                    is_perpetual=False,
                ),
            ]
        )
        self.get_open_interest_history = AsyncMock(
            return_value=[
                CoinalyzeHistorySeries(
                    symbol='BTCUSDT_PERP.A',
                    history=[
                        CoinalyzeHistoryPoint(t=1711929600, c=100.0),
                        CoinalyzeHistoryPoint(t=1711933200, c=110.0),
                    ],
                ),
                CoinalyzeHistorySeries(
                    symbol='BTCUSD_PERP.0',
                    history=[
                        CoinalyzeHistoryPoint(t=1711929600, c=50.0),
                        CoinalyzeHistoryPoint(t=1711933200, c=55.0),
                    ],
                ),
            ]
        )
        self.get_funding_rate_history = AsyncMock(
            return_value=[
                CoinalyzeHistorySeries(
                    symbol='BTCUSDT_PERP.A',
                    history=[
                        CoinalyzeHistoryPoint(t=1711929600, c=0.01),
                        CoinalyzeHistoryPoint(t=1711933200, c=0.02),
                    ],
                ),
                CoinalyzeHistorySeries(
                    symbol='BTCUSD_PERP.0',
                    history=[
                        CoinalyzeHistoryPoint(t=1711929600, c=0.03),
                        CoinalyzeHistoryPoint(t=1711933200, c=0.04),
                    ],
                ),
            ]
        )


@pytest.mark.asyncio
async def test_collect_coinalyze_btc_snapshots_builds_raw_and_aggregate_rows():
    service = _FakeCoinalyzeService()
    collector = CryptoSignalMarketRegimeCollector(coinalyze_service=service)
    observed_at = datetime.datetime(2026, 4, 29, 8, 0, tzinfo=datetime.timezone.utc)

    snapshots = await collector.collect_coinalyze_btc_snapshots(
        observed_at_utc=observed_at,
        runtime_mode='test',
        symbols=['BTCUSDT_PERP.A', 'BTCUSD_PERP.0'],
        interval='1hour',
        backfill_days=30,
    )

    assert [snapshot.instrument_scope for snapshot in snapshots] == [
        'BTCUSDT_PERP.A',
        'BTCUSD_PERP.0',
        AGGREGATE_INSTRUMENT_SCOPE,
    ]
    assert snapshots[0].venue_scope == 'binance'
    assert snapshots[2].venue_scope == AGGREGATE_VENUE_SCOPE
    aggregate_metrics = snapshots[2].metrics
    assert [
        metric.metric_value
        for metric in aggregate_metrics
        if metric.metric_name == OPEN_INTEREST_METRIC
    ] == [150.0, 165.0]
    assert [
        metric.metric_value
        for metric in aggregate_metrics
        if metric.metric_name == FUNDING_RATE_METRIC
    ] == [0.02, 0.03]

    service.get_future_markets.assert_awaited_once()
    service.get_open_interest_history.assert_awaited_once_with(
        symbols=['BTCUSDT_PERP.A', 'BTCUSD_PERP.0'],
        interval='1hour',
        from_timestamp_seconds=int(
            (observed_at - datetime.timedelta(days=30)).timestamp()
        ),
        to_timestamp_seconds=int(observed_at.timestamp()),
        convert_to_usd=True,
    )
    service.get_funding_rate_history.assert_awaited_once()


@pytest.mark.asyncio
async def test_collect_coinalyze_btc_snapshots_drops_non_btc_perpetual_symbols():
    service = _FakeCoinalyzeService()
    collector = CryptoSignalMarketRegimeCollector(coinalyze_service=service)

    snapshots = await collector.collect_coinalyze_btc_snapshots(
        observed_at_utc=datetime.datetime(
            2026,
            4,
            29,
            8,
            0,
            tzinfo=datetime.timezone.utc,
        ),
        runtime_mode='test',
        symbols=[
            'BTCUSDT_PERP.A',
            'ETHUSDT_PERP.A',
            'BTCUSDT.1',
            'MISSING_PERP.A',
        ],
        interval='1hour',
        backfill_days=30,
    )

    assert [snapshot.instrument_scope for snapshot in snapshots] == [
        'BTCUSDT_PERP.A',
        AGGREGATE_INSTRUMENT_SCOPE,
    ]
    service.get_open_interest_history.assert_awaited_once()
    assert service.get_open_interest_history.await_args.kwargs['symbols'] == [
        'BTCUSDT_PERP.A'
    ]


@pytest.mark.asyncio
async def test_collect_coinalyze_btc_snapshots_fails_closed_when_metadata_fails():
    service = _FakeCoinalyzeService()
    service.get_future_markets.side_effect = RuntimeError('metadata unavailable')
    collector = CryptoSignalMarketRegimeCollector(coinalyze_service=service)

    snapshots = await collector.collect_coinalyze_btc_snapshots(
        observed_at_utc=datetime.datetime(
            2026,
            4,
            29,
            8,
            0,
            tzinfo=datetime.timezone.utc,
        ),
        runtime_mode='test',
        symbols=['BTCUSDT_PERP.A'],
        interval='1hour',
        backfill_days=30,
    )

    assert snapshots == []
    service.get_open_interest_history.assert_not_awaited()
    service.get_funding_rate_history.assert_not_awaited()
