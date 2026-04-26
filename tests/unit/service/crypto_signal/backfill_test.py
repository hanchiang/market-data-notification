import datetime

import pytest
from market_data_library.types import cmc_type

from src.service.crypto_signal.backfill import (
    BACKFILL_RUNTIME_MODE,
    BACKFILL_SOURCE_NAME,
    CryptoSignalBackfillService,
)


def _build_quote(
    timestamp: str,
    *,
    close: float,
    volume: float,
) -> cmc_type.OHLCVHistoricalQuote:
    return cmc_type.OHLCVHistoricalQuote(
        timeOpen=timestamp,
        timeClose=timestamp,
        timeHigh=timestamp,
        timeLow=timestamp,
        quote=cmc_type.OHLCVHistoricalInnerQuote(
            open=close,
            high=close,
            low=close,
            close=close,
            volume=volume,
            marketCap=0,
            timestamp=timestamp,
        ),
    )


def _build_history(
    *,
    coin_id: int,
    name: str,
    symbol: str,
    quotes: list[cmc_type.OHLCVHistoricalQuote],
) -> cmc_type.OHLCVHistorical:
    return cmc_type.OHLCVHistorical(
        id=coin_id,
        name=name,
        symbol=symbol,
        timeEnd=quotes[-1].quote.timestamp if quotes else '',
        quotes=quotes,
    )


class _FakeCryptoStatsService:
    def __init__(self, responses: dict[int, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[int, str]] = []

    async def get_ohlcv_historical(
        self,
        id: int,
        interval: str = '24h',
    ) -> cmc_type.OHLCVHistorical:
        self.calls.append((id, interval))
        response = self.responses[id]
        if isinstance(response, Exception):
            raise response
        return response


@pytest.mark.asyncio
async def test_build_snapshots_groups_daily_history_filters_window_and_marks_watchlist():
    btc_history = _build_history(
        coin_id=1,
        name='Bitcoin',
        symbol='BTC',
        quotes=[
            _build_quote(
                '2026-03-20T00:00:00.000Z',
                close=80_000.0,
                volume=10_000_000.0,
            ),
            _build_quote(
                '2026-03-25T00:00:00.000Z',
                close=100_000.0,
                volume=15_000_000.0,
            ),
            _build_quote(
                '2026-03-26T00:00:00.000Z',
                close=110_000.0,
                volume=18_000_000.0,
            ),
            _build_quote(
                '2026-04-24T00:00:00.000Z',
                close=130_000.0,
                volume=21_000_000.0,
            ),
        ],
    )
    eth_history = _build_history(
        coin_id=1027,
        name='Ethereum',
        symbol='ETH',
        quotes=[
            _build_quote(
                '2026-03-25T00:00:00.000Z',
                close=2_000.0,
                volume=5_000_000.0,
            ),
            _build_quote(
                '2026-03-26T00:00:00.000Z',
                close=2_200.0,
                volume=6_000_000.0,
            ),
        ],
    )
    service = CryptoSignalBackfillService(
        cmc_service=_FakeCryptoStatsService(
            responses={
                1: btc_history,
                1027: eth_history,
            }
        )
    )

    snapshots = await service.build_snapshots(
        coin_entries=[('BTC', 1), ('ETH', 1027), ('BTC', 1)],
        watchlist_coin_ids={1},
        current_timestamp_utc=datetime.datetime(
            2026,
            4,
            23,
            8,
            45,
            tzinfo=datetime.timezone.utc,
        ),
        days=30,
    )

    assert [snapshot.run.run_timestamp_utc for snapshot in snapshots] == [
        datetime.datetime(2026, 3, 25, 0, 0, tzinfo=datetime.timezone.utc),
        datetime.datetime(2026, 3, 26, 0, 0, tzinfo=datetime.timezone.utc),
    ]
    first_snapshot = snapshots[0]
    assert first_snapshot.run.runtime_mode == BACKFILL_RUNTIME_MODE
    assert first_snapshot.run.source_name == BACKFILL_SOURCE_NAME
    assert [coin.symbol for coin in first_snapshot.coins] == ['BTC', 'ETH']
    btc_coin = first_snapshot.coins[0]
    eth_coin = first_snapshot.coins[1]
    assert btc_coin.is_watchlist is True
    assert btc_coin.context_tags == ('watchlist',)
    assert round(btc_coin.price_change_24h or 0, 2) == 25.0
    assert round(btc_coin.volume_change_pct_24h or 0, 2) == 50.0
    assert eth_coin.is_watchlist is False
    assert eth_coin.context_tags == ()
    assert eth_coin.price_change_24h is None
    assert eth_coin.volume_change_pct_24h is None

    second_snapshot = snapshots[1]
    second_btc_coin = second_snapshot.coins[0]
    second_eth_coin = second_snapshot.coins[1]
    assert round(second_btc_coin.price_change_24h or 0, 2) == 10.0
    assert round(second_btc_coin.volume_change_pct_24h or 0, 2) == 20.0
    assert round(second_eth_coin.price_change_24h or 0, 2) == 10.0
    assert round(second_eth_coin.volume_change_pct_24h or 0, 2) == 20.0


@pytest.mark.asyncio
async def test_build_snapshots_skips_failed_coin_history_and_returns_empty_for_empty_entries():
    fake_service = _FakeCryptoStatsService(
        responses={
            1: RuntimeError('cmc unavailable'),
        }
    )
    service = CryptoSignalBackfillService(cmc_service=fake_service)

    empty_result = await service.build_snapshots(
        coin_entries=[],
        watchlist_coin_ids=set(),
        current_timestamp_utc=datetime.datetime(
            2026,
            4,
            23,
            8,
            45,
            tzinfo=datetime.timezone.utc,
        ),
        days=30,
    )
    skipped_result = await service.build_snapshots(
        coin_entries=[('BTC', 1)],
        watchlist_coin_ids={1},
        current_timestamp_utc=datetime.datetime(
            2026,
            4,
            23,
            8,
            45,
            tzinfo=datetime.timezone.utc,
        ),
        days=30,
    )

    assert empty_result == []
    assert skipped_result == []
    assert fake_service.calls == [(1, '24h')]
