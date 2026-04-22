import datetime
import sqlite3

from src.service.crypto_signal.models import (
    CryptoSignalCoinSnapshot,
    CryptoSignalRunRecord,
    CryptoSignalSnapshot,
)
from src.service.crypto_signal.repository import CryptoSignalRepository


def test_save_snapshot_and_load_latest_snapshot(tmp_path):
    repository = CryptoSignalRepository(
        db_path=str(tmp_path / 'crypto_signal.sqlite3')
    )
    snapshot = CryptoSignalSnapshot(
        run=CryptoSignalRunRecord(
            run_timestamp_utc=datetime.datetime(
                2026,
                4,
                21,
                8,
                45,
                tzinfo=datetime.timezone.utc,
            ),
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
                coin_id=5690,
                symbol='RENDER',
                name='Render',
                price_usd=8.12,
                price_change_24h=-1.9,
                volume_24h=286_000_000,
                volume_change_pct_24h=11.3,
                is_watchlist=False,
                context_tags=('sector_loser_strongest',),
            ),
        ],
    )

    saved_snapshot = repository.save_snapshot(snapshot)
    latest_snapshot = repository.get_latest_snapshot()

    assert saved_snapshot.run.run_id is not None
    assert latest_snapshot is not None
    assert latest_snapshot.run.run_timestamp_utc == snapshot.run.run_timestamp_utc
    assert [coin.symbol for coin in latest_snapshot.coins] == ['RENDER', 'SOL']
    assert latest_snapshot.coins[1].context_tags == (
        'spotlight_trending',
        'spotlight_gainer',
        'sector_leader_strongest',
        'watchlist',
    )


def test_init_schema_sets_schema_version_metadata(tmp_path):
    db_path = tmp_path / 'crypto_signal.sqlite3'
    repository = CryptoSignalRepository(db_path=str(db_path))

    repository.init_schema()

    connection = sqlite3.connect(db_path)
    row = connection.execute(
        "SELECT value FROM crypto_signal_metadata WHERE key = 'schema_version'"
    ).fetchone()
    connection.close()

    assert row == ('1',)
