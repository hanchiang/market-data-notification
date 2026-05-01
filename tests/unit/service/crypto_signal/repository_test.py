import datetime
import sqlite3

from src.service.crypto_signal.models import (
    CryptoSignalCoinSnapshot,
    CryptoSignalMarketRegimeMetric,
    CryptoSignalMarketRegimeSnapshot,
    CryptoSignalRunRecord,
    CryptoSignalSnapshot,
)
from src.service.crypto_signal.market_regime import (
    FUNDING_RATE_METRIC,
    OPEN_INTEREST_METRIC,
)
from src.service.crypto_signal.repository import CryptoSignalRepository
from src.runtime.runtime_mode import RuntimeMode


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


def test_repository_uses_runtime_specific_default_db_path(monkeypatch):
    monkeypatch.delenv('CRYPTO_SIGNAL_DB_PATH', raising=False)
    monkeypatch.delenv('CRYPTO_SIGNAL_TEST_DB_PATH', raising=False)

    repository = CryptoSignalRepository(
        runtime_mode=RuntimeMode.from_test_mode(True)
    )

    assert repository.db_path == 'var/crypto_signal/crypto_signal.test.sqlite3'


def test_get_latest_snapshot_reads_existing_db_through_readonly_connection(tmp_path):
    db_path = tmp_path / 'crypto_signal.sqlite3'
    writable_repository = CryptoSignalRepository(db_path=str(db_path))
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
                coin_id=1,
                symbol='BTC',
                name='Bitcoin',
                price_usd=95_200.0,
                price_change_24h=2.4,
                volume_24h=31_000_000_000,
                volume_change_pct_24h=7.5,
                is_watchlist=False,
                context_tags=(),
            )
        ],
    )
    writable_repository.save_snapshot(snapshot)

    readonly_repository = CryptoSignalRepository(db_path=str(db_path))

    def _readonly_connect():
        connection = sqlite3.connect(
            f'file:{db_path}?mode=ro',
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        return connection

    readonly_repository._connect = _readonly_connect
    latest_snapshot = readonly_repository.get_latest_snapshot()

    assert latest_snapshot is not None
    assert latest_snapshot.run.run_timestamp_utc == snapshot.run.run_timestamp_utc
    assert [coin.symbol for coin in latest_snapshot.coins] == ['BTC']


def test_get_latest_snapshot_returns_none_when_db_is_missing(tmp_path):
    repository = CryptoSignalRepository(
        db_path=str(tmp_path / 'missing_crypto_signal.sqlite3')
    )

    assert repository.get_latest_snapshot() is None
    assert not (tmp_path / 'missing_crypto_signal.sqlite3').exists()


def test_get_coin_observation_counts_since_does_not_create_missing_db(tmp_path):
    db_path = tmp_path / 'missing_crypto_signal.sqlite3'
    repository = CryptoSignalRepository(db_path=str(db_path))

    counts = repository.get_coin_observation_counts_since(
        coin_ids=[1, 5426],
        start_timestamp_utc=datetime.datetime(
            2026,
            4,
            20,
            0,
            0,
            tzinfo=datetime.timezone.utc,
        ),
    )

    assert counts == {
        1: 0,
        5426: 0,
    }
    assert not db_path.exists()


def test_save_or_merge_snapshot_reuses_run_timestamp_and_upserts_coin_rows(tmp_path):
    repository = CryptoSignalRepository(
        db_path=str(tmp_path / 'crypto_signal.sqlite3')
    )
    run_timestamp_utc = datetime.datetime(
        2026,
        4,
        21,
        0,
        0,
        tzinfo=datetime.timezone.utc,
    )
    first_snapshot = CryptoSignalSnapshot(
        run=CryptoSignalRunRecord(
            run_timestamp_utc=run_timestamp_utc,
            runtime_mode='bootstrap',
            source_name='CMC historical bootstrap',
            snapshot_version=1,
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
        coins=[
            CryptoSignalCoinSnapshot(
                coin_id=1,
                symbol='BTC',
                name='Bitcoin',
                price_usd=95_200.0,
                price_change_24h=None,
                volume_24h=31_000_000_000,
                volume_change_pct_24h=None,
                is_watchlist=True,
                context_tags=('watchlist',),
            )
        ],
    )
    second_snapshot = CryptoSignalSnapshot(
        run=CryptoSignalRunRecord(
            run_timestamp_utc=run_timestamp_utc,
            runtime_mode='bootstrap',
            source_name='CMC historical bootstrap',
            snapshot_version=1,
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
        coins=[
            CryptoSignalCoinSnapshot(
                coin_id=1027,
                symbol='ETH',
                name='Ethereum',
                price_usd=3_450.0,
                price_change_24h=1.7,
                volume_24h=19_000_000_000,
                volume_change_pct_24h=5.2,
                is_watchlist=False,
                context_tags=(),
            )
        ],
    )

    repository.save_or_merge_snapshot(first_snapshot)
    repository.save_or_merge_snapshot(second_snapshot)
    repository.save_or_merge_snapshot(first_snapshot)
    latest_snapshot = repository.get_latest_snapshot()

    assert latest_snapshot is not None
    assert [coin.symbol for coin in latest_snapshot.coins] == ['BTC', 'ETH']

    connection = sqlite3.connect(tmp_path / 'crypto_signal.sqlite3')
    run_count = connection.execute(
        'SELECT COUNT(*) FROM crypto_signal_runs'
    ).fetchone()[0]
    coin_count = connection.execute(
        'SELECT COUNT(*) FROM crypto_signal_coin_snapshots'
    ).fetchone()[0]
    connection.close()

    assert run_count == 1
    assert coin_count == 2


def test_get_coin_observation_counts_since_returns_zero_for_missing_coin(tmp_path):
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
                context_tags=('watchlist',),
            )
        ],
    )

    repository.save_snapshot(snapshot)
    counts = repository.get_coin_observation_counts_since(
        coin_ids=[1, 5426],
        start_timestamp_utc=datetime.datetime(
            2026,
            4,
            20,
            0,
            0,
            tzinfo=datetime.timezone.utc,
        ),
    )

    assert counts == {
        1: 0,
        5426: 1,
    }


def test_save_market_regime_snapshot_upserts_metrics(tmp_path):
    repository = CryptoSignalRepository(
        db_path=str(tmp_path / 'crypto_signal.sqlite3')
    )
    observed_at_utc = datetime.datetime(
        2026,
        4,
        27,
        8,
        0,
        tzinfo=datetime.timezone.utc,
    )
    source_timestamp_utc = datetime.datetime(
        2026,
        4,
        27,
        0,
        0,
        tzinfo=datetime.timezone.utc,
    )
    first_snapshot = CryptoSignalMarketRegimeSnapshot(
        observed_at_utc=observed_at_utc,
        runtime_mode='prod',
        provider='coinalyze',
        asset_symbol='BTC',
        venue_scope='aggregate',
        instrument_scope='btc_perpetual_basket',
        interval='1hour',
        source_payload_version=1,
        metrics=[
            CryptoSignalMarketRegimeMetric(
                metric_name=OPEN_INTEREST_METRIC,
                metric_value=35_000_000_000,
                unit='usd',
                source_timestamp_utc=source_timestamp_utc,
            ),
        ],
    )
    updated_snapshot = CryptoSignalMarketRegimeSnapshot(
        observed_at_utc=observed_at_utc,
        runtime_mode='prod',
        provider='coinalyze',
        asset_symbol='BTC',
        venue_scope='aggregate',
        instrument_scope='btc_perpetual_basket',
        interval='1hour',
        source_payload_version=1,
        metrics=[
            CryptoSignalMarketRegimeMetric(
                metric_name=OPEN_INTEREST_METRIC,
                metric_value=36_000_000_000,
                unit='usd',
                source_timestamp_utc=source_timestamp_utc,
            ),
            CryptoSignalMarketRegimeMetric(
                metric_name=FUNDING_RATE_METRIC,
                metric_value=0.0125,
                unit='percent',
                source_timestamp_utc=source_timestamp_utc,
            ),
        ],
    )
    binance_snapshot = CryptoSignalMarketRegimeSnapshot(
        observed_at_utc=observed_at_utc,
        runtime_mode='prod',
        provider='binance',
        asset_symbol='BTC',
        venue_scope='binance',
        instrument_scope='BTCUSDT',
        interval='1hour',
        source_payload_version=1,
        metrics=[
            CryptoSignalMarketRegimeMetric(
                metric_name=OPEN_INTEREST_METRIC,
                metric_value=40_000_000_000,
                unit='usd',
                source_timestamp_utc=source_timestamp_utc,
            ),
        ],
    )
    later_coinalyze_snapshot = CryptoSignalMarketRegimeSnapshot(
        observed_at_utc=observed_at_utc + datetime.timedelta(hours=1),
        runtime_mode='prod',
        provider='coinalyze',
        asset_symbol='BTC',
        venue_scope='aggregate',
        instrument_scope='btc_perpetual_basket',
        interval='1hour',
        source_payload_version=1,
        metrics=[
            CryptoSignalMarketRegimeMetric(
                metric_name=OPEN_INTEREST_METRIC,
                metric_value=37_000_000_000,
                unit='usd',
                source_timestamp_utc=source_timestamp_utc,
            ),
        ],
    )

    first_saved = repository.save_market_regime_snapshot(first_snapshot)
    second_saved = repository.save_market_regime_snapshot(updated_snapshot)
    repository.save_market_regime_snapshot(binance_snapshot)
    repository.save_market_regime_snapshot(later_coinalyze_snapshot)
    metrics = repository.get_market_regime_metrics(
        runtime_mode='prod',
        start_timestamp_utc=datetime.datetime(
            2026,
            4,
            26,
            0,
            0,
            tzinfo=datetime.timezone.utc,
        ),
        end_timestamp_utc=datetime.datetime(
            2026,
            4,
            28,
            0,
            0,
            tzinfo=datetime.timezone.utc,
        ),
        metric_names=[OPEN_INTEREST_METRIC, FUNDING_RATE_METRIC],
        provider='coinalyze',
        venue_scope='aggregate',
        instrument_scope='btc_perpetual_basket',
        interval='1hour',
    )

    assert first_saved.snapshot_id == second_saved.snapshot_id
    assert [(metric.metric_name, metric.metric_value) for metric in metrics] == [
        (FUNDING_RATE_METRIC, 0.0125),
        (OPEN_INTEREST_METRIC, 37_000_000_000),
    ]
    assert {
        (
            metric.provider,
            metric.asset_symbol,
            metric.venue_scope,
            metric.instrument_scope,
            metric.interval,
        )
        for metric in metrics
    } == {('coinalyze', 'BTC', 'aggregate', 'btc_perpetual_basket', '1hour')}

    connection = sqlite3.connect(tmp_path / 'crypto_signal.sqlite3')
    snapshot_count = connection.execute(
        'SELECT COUNT(*) FROM crypto_signal_market_regime_snapshots'
    ).fetchone()[0]
    metric_count = connection.execute(
        'SELECT COUNT(*) FROM crypto_signal_market_regime_metrics'
    ).fetchone()[0]
    connection.close()

    assert snapshot_count == 3
    assert metric_count == 4


def test_get_market_regime_metrics_returns_empty_for_missing_db(tmp_path):
    repository = CryptoSignalRepository(
        db_path=str(tmp_path / 'missing_crypto_signal.sqlite3')
    )

    assert repository.get_market_regime_metrics(
        runtime_mode='prod',
        start_timestamp_utc=datetime.datetime(
            2026,
            4,
            26,
            0,
            0,
            tzinfo=datetime.timezone.utc,
        ),
        end_timestamp_utc=datetime.datetime(
            2026,
            4,
            28,
            0,
            0,
            tzinfo=datetime.timezone.utc,
        ),
        metric_names=[OPEN_INTEREST_METRIC],
    ) == []
