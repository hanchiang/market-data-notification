import datetime

from src.service.crypto_signal.models import (
    CryptoSignalCoinSnapshot,
    CryptoSignalRunRecord,
    CryptoSignalSnapshot,
)
from src.service.crypto_signal.scorer import build_digest_view


def _build_snapshot(
    run_timestamp_utc: datetime.datetime,
    *,
    price_change_24h: float,
    volume_change_pct_24h: float,
    context_tags: tuple[str, ...],
    is_watchlist: bool = True,
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
                price_usd=184.23,
                price_change_24h=price_change_24h,
                volume_24h=4_820_000_000,
                volume_change_pct_24h=volume_change_pct_24h,
                is_watchlist=is_watchlist,
                context_tags=context_tags,
            )
        ],
    )


def test_build_digest_view_requires_two_observations_for_strong_signal():
    latest_snapshot = _build_snapshot(
        datetime.datetime(2026, 4, 22, 8, 45, tzinfo=datetime.timezone.utc),
        price_change_24h=10.0,
        volume_change_pct_24h=30.0,
        context_tags=(
            'spotlight_trending',
            'spotlight_gainer',
            'sector_leader_strongest',
            'watchlist',
        ),
    )

    view = build_digest_view(
        latest_snapshot=latest_snapshot,
        history=[latest_snapshot],
        watchlist_coin_ids={5426},
        window_label='7d',
        limit=3,
    )

    assert view.strong_candidates == []
    assert len(view.watchlist_candidates) == 1
    candidate = view.watchlist_candidates[0]
    assert candidate.score == 0
    assert candidate.observation_count == 1
    assert candidate.reason_tags == ('risk-on', 'thin-history')


def test_build_digest_view_emits_score_derived_reason_tags():
    earlier_snapshot = _build_snapshot(
        datetime.datetime(2026, 4, 21, 8, 45, tzinfo=datetime.timezone.utc),
        price_change_24h=8.0,
        volume_change_pct_24h=25.0,
        context_tags=(
            'spotlight_trending',
            'spotlight_gainer',
            'sector_leader_strongest',
            'watchlist',
        ),
    )
    latest_snapshot = _build_snapshot(
        datetime.datetime(2026, 4, 22, 8, 45, tzinfo=datetime.timezone.utc),
        price_change_24h=10.0,
        volume_change_pct_24h=30.0,
        context_tags=(
            'spotlight_trending',
            'spotlight_gainer',
            'sector_leader_strongest',
            'watchlist',
        ),
    )

    view = build_digest_view(
        latest_snapshot=latest_snapshot,
        history=[earlier_snapshot, latest_snapshot],
        watchlist_coin_ids={5426},
        window_label='7d',
        limit=3,
    )

    assert len(view.strong_candidates) == 1
    candidate = view.strong_candidates[0]
    assert candidate.score == 9
    assert candidate.reason_tags == (
        'price-up-persistent',
        'vol-confirm',
        'spotlight-repeat',
        'breadth-align',
        'risk-on',
        'thin-history',
    )


def test_build_digest_view_keeps_watchlist_candidate_from_recent_history_when_latest_snapshot_omits_it():
    earlier_snapshot = _build_snapshot(
        datetime.datetime(2026, 4, 21, 8, 45, tzinfo=datetime.timezone.utc),
        price_change_24h=3.4,
        volume_change_pct_24h=9.8,
        context_tags=('watchlist',),
    )
    latest_snapshot = CryptoSignalSnapshot(
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
        coins=[],
    )

    view = build_digest_view(
        latest_snapshot=latest_snapshot,
        history=[earlier_snapshot, latest_snapshot],
        watchlist_coin_ids={5426},
        window_label='7d',
        limit=3,
    )

    assert view.strong_candidates == []
    assert len(view.watchlist_candidates) == 1
    candidate = view.watchlist_candidates[0]
    assert candidate.coin_id == 5426
    assert candidate.symbol == 'SOL'
    assert candidate.latest_price_change_24h == 3.4
    assert candidate.observation_count == 1
    assert candidate.reason_tags == ('risk-on', 'thin-history')
