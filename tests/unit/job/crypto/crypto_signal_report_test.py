import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.job.crypto import crypto_signal_report
from src.service.crypto_signal.models import (
    CryptoSignalCoinSnapshot,
    CryptoSignalRunRecord,
    CryptoSignalSnapshot,
)


def _build_snapshot() -> CryptoSignalSnapshot:
    return CryptoSignalSnapshot(
        run=CryptoSignalRunRecord(
            run_timestamp_utc=datetime.datetime(2026, 4, 22, 8, 45, tzinfo=datetime.timezone.utc),
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
                coin_id=1,
                symbol='BTC',
                name='Bitcoin',
                price_usd=95_200.0,
                price_change_24h=2.4,
                volume_24h=31_000_000_000,
                volume_change_pct_24h=7.5,
                is_watchlist=True,
                context_tags=('watchlist',),
            )
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
async def test_main_prints_report_to_stdout_when_send_telegram_disabled(
    monkeypatch,
    capsys,
):
    snapshot = _build_snapshot()
    fake_repository = _FakeRepository(snapshot)
    send_crypto_signal_message = AsyncMock()

    monkeypatch.setattr(
        crypto_signal_report.argparse.ArgumentParser,
        'parse_args',
        lambda _self: SimpleNamespace(window='7d', limit=3, send_telegram=0, test_mode=0),
    )
    monkeypatch.setattr(
        crypto_signal_report,
        'CryptoSignalRepository',
        lambda runtime_mode=None: fake_repository,
    )
    monkeypatch.setattr(
        crypto_signal_report,
        'build_crypto_signal_message',
        lambda _view: '*Crypto trend signal*',
    )
    monkeypatch.setattr(
        crypto_signal_report.config,
        'get_crypto_signal_watchlist',
        lambda: [('BTC', 1)],
    )
    monkeypatch.setattr(
        crypto_signal_report,
        'send_crypto_signal_message',
        send_crypto_signal_message,
    )

    await crypto_signal_report.main()

    captured = capsys.readouterr()
    assert captured.out == '*Crypto trend signal*\n'
    send_crypto_signal_message.assert_not_awaited()
