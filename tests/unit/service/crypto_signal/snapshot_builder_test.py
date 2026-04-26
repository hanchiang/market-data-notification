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


def test_build_snapshot_uses_explicit_source_precedence_for_overlapping_coin():
    coin_id = 999
    spotlight_coin = cmc_type.TrendingList(
        id=coin_id,
        name='Spotlight Name',
        symbol='SPOT',
        priceChange=cmc_type.PriceChange(
            price=1.0,
            priceChange24h=2.0,
            volume24h=100_000_000,
        ),
    )
    sector_coin = cmc_type.SectorCoin(
        id=coin_id,
        name='Sector Name',
        slug='sector-name',
        symbol='SECTOR',
        quote={
            'USD': cmc_type.SectorCoinQuote(
                price=2.0,
                percent_change_24h=12.0,
                volume_24h=200_000_000,
            )
        },
    )
    sector_detail = cmc_type.SectorDetail(
        sectorId='ai',
        title='AI',
        coins=[sector_coin],
    )
    sector_coin_detail = cmc_type.CoinDetail(
        id=coin_id,
        name='Sector Detail Name',
        symbol='SDC',
        volume=300_000_000,
        volumeChangePercentage24h=22.0,
        statistics=cmc_type.CoinDetailStatistics(
            price=3.0,
            priceChangePercentage24h=13.0,
        ),
    )
    tracked_coin_detail = cmc_type.CoinDetail(
        id=coin_id,
        name='Tracked Detail Name',
        symbol='TRACKED',
        volume=400_000_000,
        volumeChangePercentage24h=33.0,
        statistics=cmc_type.CoinDetailStatistics(
            price=4.0,
            priceChangePercentage24h=14.0,
        ),
    )

    snapshot = build_snapshot(
        current=datetime.datetime(2026, 4, 21, 8, 45, tzinfo=datetime.timezone.utc),
        runtime_mode=RuntimeMode(),
        source_name='CMC + Alternative.me',
        sentiment=None,
        strongest_sector=cmc_type.Sector24hChange(
            sectorId='ai',
            title='AI',
        ),
        weakest_sector=None,
        standout_entries=[(spotlight_coin, ['trending'])],
        standout_coin_details={coin_id: sector_coin_detail},
        sector_details={'ai': sector_detail},
        sector_detail_coin_details={coin_id: sector_coin_detail},
        tracked_universe_entries=[('CONFIGURED', coin_id)],
        tracked_universe_coin_details={coin_id: tracked_coin_detail},
        watchlist_entries=[('TRACKED', coin_id)],
    )

    assert len(snapshot.coins) == 1
    coin = snapshot.coins[0]

    assert coin.symbol == 'TRACKED'
    assert coin.name == 'Tracked Detail Name'
    assert coin.price_usd == 4.0
    assert coin.price_change_24h == 14.0
    assert coin.volume_24h == 400_000_000
    assert coin.volume_change_pct_24h == 33.0
    assert coin.is_watchlist is True
    assert coin.context_tags == (
        'spotlight_trending',
        'sector_leader_strongest',
        'watchlist',
    )
