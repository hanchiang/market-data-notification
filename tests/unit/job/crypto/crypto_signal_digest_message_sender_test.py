import datetime

import pytest

from src.job.crypto.crypto_signal_digest_message_sender import (
    CryptoSignalDigestMessageSender,
)
from src.runtime.runtime_mode import DEFAULT_RUNTIME_MODE
from src.service.crypto_signal.market_regime_collector import (
    AGGREGATE_INSTRUMENT_SCOPE,
    AGGREGATE_VENUE_SCOPE,
    COINALYZE_PROVIDER,
)
from src.service.crypto_signal.models import (
    CryptoSignalCoinSnapshot,
    CryptoSignalMarketRegimeMetric,
    CryptoSignalRunRecord,
    CryptoSignalSnapshot,
)


def _build_snapshot(
    run_timestamp_utc: datetime.datetime,
    *,
    sol_price_usd: float = 184.23,
) -> CryptoSignalSnapshot:
    return CryptoSignalSnapshot(
        run=CryptoSignalRunRecord(
            run_timestamp_utc=run_timestamp_utc,
            runtime_mode='prod',
            source_name='CMC + Alternative.me',
            snapshot_version=1,
            sentiment_now_value=63.0,
            sentiment_now_label='Greed',
            sentiment_yesterday_value=58.0,
            sentiment_last_week_value=49.0,
            sentiment_7d_avg=55.4,
            sentiment_30d_avg=51.8,
            strongest_sector_id='ai-big-data',
            strongest_sector_name='AI & Big Data',
            strongest_sector_avg_price_change_24h=8.4,
            strongest_sector_market_change_24h=6.9,
            strongest_sector_volume_change_24h=21.7,
            strongest_sector_gainers_num=18,
            strongest_sector_losers_num=5,
            weakest_sector_id='gaming',
            weakest_sector_name='Gaming',
            weakest_sector_avg_price_change_24h=-6.1,
            weakest_sector_market_change_24h=-4.8,
            weakest_sector_volume_change_24h=-12.3,
            weakest_sector_gainers_num=4,
            weakest_sector_losers_num=19,
        ),
        coins=[
            CryptoSignalCoinSnapshot(
                coin_id=5426,
                symbol='SOL',
                name='Solana',
                price_usd=sol_price_usd,
                price_change_24h=11.4,
                volume_24h=4_820_000_000,
                volume_change_pct_24h=27.1,
                is_watchlist=True,
                context_tags=(
                    'spotlight_trending',
                    'spotlight_gainer',
                    'sector_leader_strongest',
                    'watchlist',
                ),
            ),
            CryptoSignalCoinSnapshot(
                coin_id=1,
                symbol='BTC',
                name='Bitcoin',
                price_usd=95_200.0,
                price_change_24h=2.4,
                volume_24h=31_000_000_000,
                volume_change_pct_24h=7.5,
                is_watchlist=True,
                context_tags=('watchlist',),
            ),
        ],
    )


class _FakeRepository:
    def __init__(
        self,
        latest_snapshot: CryptoSignalSnapshot,
        history: list[CryptoSignalSnapshot] | None = None,
    ):
        self.latest_snapshot = latest_snapshot
        self.history = [latest_snapshot] if history is None else history

    def get_latest_snapshot(self):
        return self.latest_snapshot

    def get_snapshots_since(self, _start):
        return self.history

    def get_market_regime_metrics(self, **_kwargs):
        return []


def _build_sender(
    repository,
    *,
    watchlist_entries=None,
    tracked_universe_entries=None,
) -> CryptoSignalDigestMessageSender:
    return CryptoSignalDigestMessageSender(
        runtime_mode=DEFAULT_RUNTIME_MODE,
        signal_repository=repository,
        watchlist_entries=(
            [('BTC', 1), ('SOL', 5426)]
            if watchlist_entries is None
            else watchlist_entries
        ),
        tracked_universe_entries=(
            [('BTC', 1), ('SOL', 5426)]
            if tracked_universe_entries is None
            else tracked_universe_entries
        ),
    )


@pytest.mark.asyncio
async def test_format_message_builds_operator_digest(monkeypatch):
    earlier_snapshot = _build_snapshot(
        datetime.datetime(2026, 4, 20, 8, 45, tzinfo=datetime.timezone.utc),
        sol_price_usd=150.0,
    )
    latest_snapshot = _build_snapshot(
        datetime.datetime(2026, 4, 21, 8, 45, tzinfo=datetime.timezone.utc)
    )

    sender = _build_sender(
        _FakeRepository(
            latest_snapshot,
            history=[earlier_snapshot, latest_snapshot],
        ),
    )

    monkeypatch.setattr(
        'src.job.crypto.crypto_signal_digest_message_sender.get_current_datetime',
        lambda: datetime.datetime(2026, 4, 21, 9, 0, tzinfo=datetime.timezone.utc),
    )

    messages = await sender.format_message()

    assert len(messages) == 1
    assert '*Crypto trend signal*' in messages[0]
    assert '*Strong 7d momentum*' in messages[0]
    assert '7d \\+22\\.82%' in messages[0]
    assert '*Watchlist*' in messages[0]
    assert 'Solana' in messages[0]


@pytest.mark.asyncio
async def test_format_message_uses_stored_coinalyze_market_regime_summary(
    monkeypatch,
):
    latest_snapshot = _build_snapshot(
        datetime.datetime(2026, 4, 21, 8, 45, tzinfo=datetime.timezone.utc)
    )

    class _RepositoryWithRegime(_FakeRepository):
        def __init__(self, latest_snapshot):
            super().__init__(latest_snapshot)
            self.market_regime_kwargs = None

        def get_market_regime_metrics(self, **kwargs):
            self.market_regime_kwargs = kwargs
            return [
                CryptoSignalMarketRegimeMetric(
                    metric_name='open_interest_usd',
                    metric_value=100.0,
                    unit='usd',
                    source_timestamp_utc=datetime.datetime(
                        2026,
                        4,
                        20,
                        8,
                        45,
                        tzinfo=datetime.timezone.utc,
                    ),
                ),
                CryptoSignalMarketRegimeMetric(
                    metric_name='open_interest_usd',
                    metric_value=108.0,
                    unit='usd',
                    source_timestamp_utc=datetime.datetime(
                        2026,
                        4,
                        21,
                        8,
                        45,
                        tzinfo=datetime.timezone.utc,
                    ),
                ),
                CryptoSignalMarketRegimeMetric(
                    metric_name='funding_rate',
                    metric_value=0.01,
                    unit='percent',
                    source_timestamp_utc=datetime.datetime(
                        2026,
                        4,
                        21,
                        8,
                        45,
                        tzinfo=datetime.timezone.utc,
                    ),
                ),
            ]

    repository = _RepositoryWithRegime(latest_snapshot)
    sender = _build_sender(repository)

    monkeypatch.setattr(
        'src.job.crypto.crypto_signal_digest_message_sender.get_current_datetime',
        lambda: datetime.datetime(2026, 4, 21, 9, 0, tzinfo=datetime.timezone.utc),
    )

    messages = await sender.format_message()

    assert 'Leverage building' in messages[0]
    assert 'OI \\+8\\.0%' in messages[0]
    assert repository.market_regime_kwargs is not None
    assert repository.market_regime_kwargs['provider'] == COINALYZE_PROVIDER
    assert repository.market_regime_kwargs['venue_scope'] == AGGREGATE_VENUE_SCOPE
    assert (
        repository.market_regime_kwargs['instrument_scope']
        == AGGREGATE_INSTRUMENT_SCOPE
    )
    assert repository.market_regime_kwargs['interval'] == '1hour'


@pytest.mark.asyncio
async def test_format_message_skips_stale_snapshot(monkeypatch):
    latest_snapshot = _build_snapshot(
        datetime.datetime(2026, 4, 20, 8, 45, tzinfo=datetime.timezone.utc)
    )

    sender = _build_sender(_FakeRepository(latest_snapshot))

    monkeypatch.setattr(
        'src.job.crypto.crypto_signal_digest_message_sender.get_current_datetime',
        lambda: datetime.datetime(2026, 4, 21, 9, 0, tzinfo=datetime.timezone.utc),
    )

    messages = await sender.format_message()

    assert messages == []
