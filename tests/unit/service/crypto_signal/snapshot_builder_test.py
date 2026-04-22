import datetime

from market_data_library.types import cmc_type

from src.runtime.runtime_mode import RuntimeMode
from src.service.crypto_signal.snapshot_builder import build_snapshot


def test_build_snapshot_persists_tracked_universe_and_marks_watchlist_subset():
    btc_detail = cmc_type.CoinDetail(
        id=1,
        name='Bitcoin',
        symbol='BTC',
        volume=31_000_000_000,
        volumeChangePercentage24h=7.5,
        statistics=cmc_type.CoinDetailStatistics(
            price=95_200.0,
            priceChangePercentage24h=2.4,
        ),
    )
    eth_detail = cmc_type.CoinDetail(
        id=1027,
        name='Ethereum',
        symbol='ETH',
        volume=19_000_000_000,
        volumeChangePercentage24h=5.2,
        statistics=cmc_type.CoinDetailStatistics(
            price=3_450.0,
            priceChangePercentage24h=1.7,
        ),
    )

    snapshot = build_snapshot(
        current=datetime.datetime(2026, 4, 21, 8, 45, tzinfo=datetime.timezone.utc),
        runtime_mode=RuntimeMode(),
        source_name='CMC + Alternative.me',
        sentiment=None,
        strongest_sector=None,
        weakest_sector=None,
        standout_entries=[],
        standout_coin_details={},
        sector_details={},
        sector_detail_coin_details={},
        tracked_universe_entries=[('BTC', 1), ('ETH', 1027)],
        tracked_universe_coin_details={
            1: btc_detail,
            1027: eth_detail,
        },
        watchlist_entries=[('ETH', 1027)],
    )

    coins_by_id = {coin.coin_id: coin for coin in snapshot.coins}

    assert set(coins_by_id) == {1, 1027}
    assert coins_by_id[1].is_watchlist is False
    assert coins_by_id[1].context_tags == ()
    assert coins_by_id[1027].is_watchlist is True
    assert coins_by_id[1027].context_tags == ('watchlist',)
