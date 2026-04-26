import datetime

from src.job.crypto.crypto_signal_formatter import build_crypto_signal_message
from src.service.crypto_signal.models import (
    CryptoSignalCandidate,
    CryptoSignalDigestView,
    CryptoSignalRunRecord,
    CryptoSignalSnapshot,
)


def test_build_crypto_signal_message_uses_reason_tags_not_raw_context_tags():
    snapshot = CryptoSignalSnapshot(
        run=CryptoSignalRunRecord(
            run_timestamp_utc=datetime.datetime(
                2026,
                4,
                22,
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
        coins=[],
    )
    candidate = CryptoSignalCandidate(
        coin_id=5426,
        symbol='SOL',
        name='Solana',
        latest_price_usd=184.23,
        latest_volume_24h=4_820_000_000,
        latest_price_change_24h=11.4,
        window_price_change_pct=24.7,
        latest_volume_change_pct_24h=27.1,
        latest_context_tags=(
            'spotlight_trending',
            'sector_leader_strongest',
        ),
        score=9,
        price_persistence_score=4,
        volume_confirmation_score=2,
        attention_persistence_score=2,
        breadth_alignment_score=1,
        observation_count=4,
        reason_tags=(
            'price-up-persistent',
            'vol-confirm',
            'spotlight-repeat',
            'breadth-align',
            'risk-on',
        ),
        flags=('risk-on', 'watchlist'),
        is_watchlist=True,
    )
    view = CryptoSignalDigestView(
        latest_snapshot=snapshot,
        window_label='7d',
        market_regime_label='Risk-on bias',
        market_regime_reason='sentiment 63 with strongest sector +8.4%',
        strong_candidates=[candidate],
        weak_candidates=[],
        watchlist_candidates=[candidate],
    )

    message = build_crypto_signal_message(view)

    assert 'price\\-up\\-persistent' in message
    assert '*Strong 7d momentum*' in message
    assert '7d \\+24\\.70%' in message
    assert '24h \\+11\\.40%' in message
    assert 'vol\\-confirm' in message
    assert 'spotlight\\_trending' not in message
    assert 'sector\\_leader\\_strongest' not in message
