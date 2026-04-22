import datetime

import pytest

from src.job.crypto.crypto_signal_digest_message_sender import (
    CryptoSignalDigestMessageSender,
)
from src.runtime.runtime_mode import DEFAULT_RUNTIME_MODE
from src.service.crypto_signal.models import (
    CryptoSignalCoinSnapshot,
    CryptoSignalRunRecord,
    CryptoSignalSnapshot,
)


def _build_snapshot(run_timestamp_utc: datetime.datetime) -> CryptoSignalSnapshot:
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
                price_usd=184.23,
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
    def __init__(self, latest_snapshot: CryptoSignalSnapshot):
        self.latest_snapshot = latest_snapshot

    def get_latest_snapshot(self):
        return self.latest_snapshot

    def get_snapshots_since(self, _start):
        return [self.latest_snapshot]


@pytest.mark.asyncio
async def test_format_message_builds_operator_digest(monkeypatch):
    latest_snapshot = _build_snapshot(
        datetime.datetime(2026, 4, 21, 8, 45, tzinfo=datetime.timezone.utc)
    )

    sender = object.__new__(CryptoSignalDigestMessageSender)
    sender.signal_repository = _FakeRepository(latest_snapshot)
    sender.watchlist_entries = [('BTC', 1), ('SOL', 5426)]
    sender.runtime_mode = DEFAULT_RUNTIME_MODE

    monkeypatch.setattr(
        'src.job.crypto.crypto_signal_digest_message_sender.get_current_datetime',
        lambda: datetime.datetime(2026, 4, 21, 9, 0, tzinfo=datetime.timezone.utc),
    )

    messages = await sender.format_message()

    assert len(messages) == 1
    assert '*Crypto trend signal*' in messages[0]
    assert '*Strong momentum*' in messages[0]
    assert '*Watchlist*' in messages[0]
    assert 'Solana' in messages[0]


@pytest.mark.asyncio
async def test_format_message_skips_stale_snapshot(monkeypatch):
    latest_snapshot = _build_snapshot(
        datetime.datetime(2026, 4, 20, 8, 45, tzinfo=datetime.timezone.utc)
    )

    sender = object.__new__(CryptoSignalDigestMessageSender)
    sender.signal_repository = _FakeRepository(latest_snapshot)
    sender.watchlist_entries = [('BTC', 1), ('SOL', 5426)]
    sender.runtime_mode = DEFAULT_RUNTIME_MODE

    monkeypatch.setattr(
        'src.job.crypto.crypto_signal_digest_message_sender.get_current_datetime',
        lambda: datetime.datetime(2026, 4, 21, 9, 0, tzinfo=datetime.timezone.utc),
    )

    messages = await sender.format_message()

    assert messages == []
