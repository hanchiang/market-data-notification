import datetime
from collections import OrderedDict
from statistics import mean
from typing import Iterable

from market_data_library.types import cmc_type

from src.job.crypto.crypto_digest_formatter import (
    _find_sector_detail,
    _is_same_sector,
    _select_sector_coins,
    should_emit_sector_detail_message,
)
from src.runtime.runtime_mode import RuntimeMode
from src.service.crypto_signal.models import (
    CryptoSignalCoinSnapshot,
    CryptoSignalRunRecord,
    CryptoSignalSnapshot,
)
from src.service.crypto_signal.repository import SNAPSHOT_VERSION
from src.type.sentiment import FearGreedResult


CONTEXT_TAG_ORDER = (
    'spotlight_trending',
    'spotlight_gainer',
    'spotlight_loser',
    'sector_leader_strongest',
    'sector_loser_strongest',
    'sector_leader_weakest',
    'sector_loser_weakest',
    'watchlist',
)

_SPOTLIGHT_REASON_TO_TAG = {
    'trending': 'spotlight_trending',
    'top gainer': 'spotlight_gainer',
    'top loser': 'spotlight_loser',
}


def build_snapshot(
    current: datetime.datetime,
    runtime_mode: RuntimeMode,
    source_name: str,
    sentiment: FearGreedResult | None,
    strongest_sector: cmc_type.Sector24hChange | None,
    weakest_sector: cmc_type.Sector24hChange | None,
    standout_entries: list[tuple[cmc_type.TrendingList, list[str]]],
    standout_coin_details: dict[int, cmc_type.CoinDetail],
    sector_details: dict[str, cmc_type.SectorDetail],
    sector_detail_coin_details: dict[int, cmc_type.CoinDetail],
    tracked_universe_entries: list[tuple[str, int]],
    tracked_universe_coin_details: dict[int, cmc_type.CoinDetail],
    watchlist_entries: list[tuple[str, int]],
) -> CryptoSignalSnapshot:
    run = CryptoSignalRunRecord(
        run_timestamp_utc=current.astimezone(datetime.timezone.utc),
        runtime_mode='test' if runtime_mode.is_test_mode else 'prod',
        source_name=source_name,
        snapshot_version=SNAPSHOT_VERSION,
        sentiment_now_value=_sentiment_value(sentiment, label='Now'),
        sentiment_now_label=_sentiment_label(sentiment, label='Now'),
        sentiment_yesterday_value=_sentiment_value(sentiment, label='Yesterday'),
        sentiment_last_week_value=_sentiment_value(sentiment, label='Last week'),
        sentiment_7d_avg=_sentiment_average(sentiment, timeframe='7d'),
        sentiment_30d_avg=_sentiment_average(sentiment, timeframe='30d'),
        strongest_sector_id=_sector_attr(strongest_sector, 'sectorId'),
        strongest_sector_name=_sector_attr(strongest_sector, 'title'),
        strongest_sector_avg_price_change_24h=_sector_attr(
            strongest_sector, 'avgPriceChange'
        ),
        strongest_sector_market_change_24h=_sector_attr(
            strongest_sector, 'marketChange'
        ),
        strongest_sector_volume_change_24h=_sector_attr(
            strongest_sector, 'volumeChange'
        ),
        strongest_sector_gainers_num=_safe_int(_sector_attr(strongest_sector, 'gainersNum')),
        strongest_sector_losers_num=_safe_int(_sector_attr(strongest_sector, 'losersNum')),
        weakest_sector_id=_sector_attr(weakest_sector, 'sectorId'),
        weakest_sector_name=_sector_attr(weakest_sector, 'title'),
        weakest_sector_avg_price_change_24h=_sector_attr(
            weakest_sector, 'avgPriceChange'
        ),
        weakest_sector_market_change_24h=_sector_attr(weakest_sector, 'marketChange'),
        weakest_sector_volume_change_24h=_sector_attr(weakest_sector, 'volumeChange'),
        weakest_sector_gainers_num=_safe_int(_sector_attr(weakest_sector, 'gainersNum')),
        weakest_sector_losers_num=_safe_int(_sector_attr(weakest_sector, 'losersNum')),
    )

    merged_coins: OrderedDict[int, CryptoSignalCoinSnapshot] = OrderedDict()

    for coin, reasons in standout_entries:
        _upsert_coin(
            merged_coins=merged_coins,
            coin_id=coin.id,
            symbol=coin.symbol,
            name=coin.name,
            price_usd=coin.priceChange.price,
            price_change_24h=coin.priceChange.priceChange24h,
            volume_24h=coin.priceChange.volume24h,
            volume_change_pct_24h=(
                standout_coin_details.get(coin.id).volumeChangePercentage24h
                if coin.id in standout_coin_details
                else None
            ),
            is_watchlist=False,
            context_tags=[
                _SPOTLIGHT_REASON_TO_TAG[reason]
                for reason in reasons
                if reason in _SPOTLIGHT_REASON_TO_TAG
            ],
        )

    if should_emit_sector_detail_message(
        strongest_sector=strongest_sector,
        weakest_sector=weakest_sector,
        sector_details=sector_details,
    ):
        _merge_sector_context(
            merged_coins=merged_coins,
            sector=strongest_sector,
            sector_detail=_find_sector_detail(strongest_sector, sector_details),
            sector_detail_coin_details=sector_detail_coin_details,
            leader_tag='sector_leader_strongest',
            loser_tag='sector_loser_strongest',
        )
        if not _is_same_sector(strongest_sector, weakest_sector):
            _merge_sector_context(
                merged_coins=merged_coins,
                sector=weakest_sector,
                sector_detail=_find_sector_detail(weakest_sector, sector_details),
                sector_detail_coin_details=sector_detail_coin_details,
                leader_tag='sector_leader_weakest',
                loser_tag='sector_loser_weakest',
            )

    watchlist_ids = {coin_id for _symbol, coin_id in watchlist_entries}
    for symbol, coin_id in tracked_universe_entries:
        coin_detail = tracked_universe_coin_details.get(coin_id)
        if coin_detail is None:
            continue
        _upsert_coin(
            merged_coins=merged_coins,
            coin_id=coin_id,
            symbol=symbol or coin_detail.symbol,
            name=coin_detail.name,
            price_usd=_coin_detail_price(coin_detail),
            price_change_24h=_coin_detail_price_change(coin_detail),
            volume_24h=coin_detail.volume,
            volume_change_pct_24h=coin_detail.volumeChangePercentage24h,
            is_watchlist=coin_id in watchlist_ids,
            context_tags=['watchlist'] if coin_id in watchlist_ids else [],
        )

    return CryptoSignalSnapshot(
        run=run,
        coins=sorted(
            merged_coins.values(),
            key=lambda coin: (coin.symbol, coin.coin_id),
        ),
    )


def _merge_sector_context(
    merged_coins: OrderedDict[int, CryptoSignalCoinSnapshot],
    sector: cmc_type.Sector24hChange | None,
    sector_detail: cmc_type.SectorDetail | None,
    sector_detail_coin_details: dict[int, cmc_type.CoinDetail],
    leader_tag: str,
    loser_tag: str,
) -> None:
    if sector is None or sector_detail is None:
        return

    leaders = _select_sector_coins(
        sector_detail=sector_detail,
        direction='leaders',
        limit=2,
        require_threshold=False,
    )
    losers = _select_sector_coins(
        sector_detail=sector_detail,
        direction='losers',
        limit=2,
        require_threshold=False,
    )
    for coin in leaders:
        _merge_sector_coin(
            merged_coins=merged_coins,
            coin=coin,
            coin_detail=sector_detail_coin_details.get(coin.id),
            context_tag=leader_tag,
        )
    for coin in losers:
        _merge_sector_coin(
            merged_coins=merged_coins,
            coin=coin,
            coin_detail=sector_detail_coin_details.get(coin.id),
            context_tag=loser_tag,
        )


def _merge_sector_coin(
    merged_coins: OrderedDict[int, CryptoSignalCoinSnapshot],
    coin: cmc_type.SectorCoin,
    coin_detail: cmc_type.CoinDetail | None,
    context_tag: str,
) -> None:
    quote = next(iter(coin.quote.values())) if coin.quote else None
    _upsert_coin(
        merged_coins=merged_coins,
        coin_id=coin.id,
        symbol=coin.symbol,
        name=coin.name,
        price_usd=(
            _coin_detail_price(coin_detail)
            if coin_detail is not None
            else getattr(quote, 'price', None)
        ),
        price_change_24h=(
            _coin_detail_price_change(coin_detail)
            if coin_detail is not None
            else getattr(quote, 'percent_change_24h', None)
        ),
        volume_24h=(
            coin_detail.volume if coin_detail is not None else getattr(quote, 'volume_24h', None)
        ),
        volume_change_pct_24h=(
            coin_detail.volumeChangePercentage24h if coin_detail is not None else None
        ),
        is_watchlist=False,
        context_tags=[context_tag],
    )


def _upsert_coin(
    merged_coins: OrderedDict[int, CryptoSignalCoinSnapshot],
    coin_id: int,
    symbol: str,
    name: str,
    price_usd: float | None,
    price_change_24h: float | None,
    volume_24h: float | None,
    volume_change_pct_24h: float | None,
    is_watchlist: bool,
    context_tags: Iterable[str],
) -> None:
    existing = merged_coins.get(coin_id)
    normalized_tags = _normalize_context_tags(context_tags)
    if existing is None:
        merged_coins[coin_id] = CryptoSignalCoinSnapshot(
            coin_id=coin_id,
            symbol=symbol,
            name=name,
            price_usd=price_usd,
            price_change_24h=price_change_24h,
            volume_24h=volume_24h,
            volume_change_pct_24h=volume_change_pct_24h,
            is_watchlist=is_watchlist,
            context_tags=normalized_tags,
        )
        return

    merged_coins[coin_id] = CryptoSignalCoinSnapshot(
        coin_id=coin_id,
        symbol=existing.symbol or symbol,
        name=existing.name or name,
        price_usd=existing.price_usd if existing.price_usd is not None else price_usd,
        price_change_24h=(
            existing.price_change_24h
            if existing.price_change_24h is not None
            else price_change_24h
        ),
        volume_24h=existing.volume_24h if existing.volume_24h is not None else volume_24h,
        volume_change_pct_24h=(
            existing.volume_change_pct_24h
            if existing.volume_change_pct_24h is not None
            else volume_change_pct_24h
        ),
        is_watchlist=existing.is_watchlist or is_watchlist,
        context_tags=_normalize_context_tags((*existing.context_tags, *normalized_tags)),
    )


def _normalize_context_tags(tags: Iterable[str]) -> tuple[str, ...]:
    seen_tags = {tag for tag in tags if tag}
    return tuple(tag for tag in CONTEXT_TAG_ORDER if tag in seen_tags)


def _sentiment_value(
    sentiment: FearGreedResult | None,
    label: str,
) -> float | None:
    point = _find_sentiment_point(sentiment, label=label)
    return float(point.value) if point is not None else None


def _sentiment_label(
    sentiment: FearGreedResult | None,
    label: str,
) -> str | None:
    point = _find_sentiment_point(sentiment, label=label)
    return point.sentiment_text if point is not None else None


def _sentiment_average(
    sentiment: FearGreedResult | None,
    timeframe: str,
) -> float | None:
    if sentiment is None:
        return None
    matches = [
        average.value
        for average in sentiment.average
        if average.timeframe == timeframe
    ]
    if len(matches) == 0:
        return None
    return float(mean(matches))


def _find_sentiment_point(
    sentiment: FearGreedResult | None,
    label: str,
):
    if sentiment is None:
        return None
    for point in sentiment.data:
        if point.relative_date_text == label:
            return point
    return None


def _sector_attr(
    sector: cmc_type.Sector24hChange | None,
    attr_name: str,
):
    if sector is None:
        return None
    return getattr(sector, attr_name)


def _safe_int(value) -> int | None:
    if value in (None, ''):
        return None
    return int(value)


def _coin_detail_price(coin_detail: cmc_type.CoinDetail | None) -> float | None:
    if coin_detail is None:
        return None
    return coin_detail.statistics.price


def _coin_detail_price_change(coin_detail: cmc_type.CoinDetail | None) -> float | None:
    if coin_detail is None:
        return None
    return coin_detail.statistics.priceChangePercentage24h
