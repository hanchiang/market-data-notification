import decimal
from collections import OrderedDict
from typing import Dict, Iterable, List, Optional

from market_data_library.types import cmc_type

from src.config import config
from src.job.crypto.util import bold_text_for_metric_type
from src.type.metric_type import MetricTypeIndicator
from src.type.sentiment import FearGreedAverage, FearGreedData, FearGreedResult
from src.util.my_telegram import escape_markdown
from src.util.number import friendly_number


StandoutEntry = tuple[cmc_type.TrendingList, List[str]]


def get_standout_entries(
    spotlight: Optional[cmc_type.Spotlight],
) -> List[StandoutEntry]:
    if spotlight is None:
        return []

    spotlight_entries: OrderedDict[int, StandoutEntry] = OrderedDict()
    for reason, coins in [
        ('trending', spotlight.trendingList[:2]),
        ('top gainer', spotlight.gainerList[:2]),
        ('top loser', spotlight.loserList[:2]),
    ]:
        _merge_spotlight_group(
            spotlight_entries=spotlight_entries,
            reason=reason,
            coins=coins,
        )

    return list(spotlight_entries.values())[:5]


def build_digest_message(
    current,
    sentiment: Optional[FearGreedResult],
    strongest_sector: Optional[cmc_type.Sector24hChange],
    weakest_sector: Optional[cmc_type.Sector24hChange],
    standout_entries: List[StandoutEntry],
    standout_coin_details: Dict[int, cmc_type.CoinDetail],
    sector_details: Dict[str, cmc_type.SectorDetail],
    sector_detail_coin_details: Dict[int, cmc_type.CoinDetail],
) -> str:
    # Telegram digest layout:
    # Crypto market digest: YYYY-MM-DD
    #
    # Sentiment
    # Now: ...
    # Yesterday: ..., Last week: ...
    # Averages: ...
    #
    # Sector breadth
    # Strongest 24h: <sector> 24h ..., mcap ..., volume ..., gainers ..., losers ...,
    #   leaders <tickers>; losers <tickers>
    # Weakest 24h: <sector> ...
    #
    # Standout coins
    # • <reason tags>: <coin> <symbol>, 24h <move> <severity>, price <price>,
    #   volume <volume>, volume change <volume change>
    lines = [
        f"*Crypto market digest*: {escape_markdown(current.strftime('%Y-%m-%d'))}",
        '',
    ]
    lines.extend(_format_sentiment_section(sentiment))
    lines.append('')
    lines.extend(
        _format_sector_section(
            strongest_sector=strongest_sector,
            weakest_sector=weakest_sector,
            sector_details=sector_details,
        )
    )
    sector_detail_lines = build_sector_detail_lines(
        strongest_sector=strongest_sector,
        weakest_sector=weakest_sector,
        sector_details=sector_details,
        sector_detail_coin_details=sector_detail_coin_details,
    )
    if sector_detail_lines:
        lines.append('')
        lines.extend(sector_detail_lines)
    lines.append('')
    lines.extend(
        _format_standout_coin_section(
            standout_entries=standout_entries,
            standout_coin_details=standout_coin_details,
        )
    )
    return '\n'.join(line for line in lines if line is not None).strip()


def build_sector_detail_lines(
    strongest_sector: Optional[cmc_type.Sector24hChange],
    weakest_sector: Optional[cmc_type.Sector24hChange],
    sector_details: Dict[str, cmc_type.SectorDetail],
    sector_detail_coin_details: Dict[int, cmc_type.CoinDetail],
) -> List[str]:
    if not should_emit_sector_detail_message(
        strongest_sector=strongest_sector,
        weakest_sector=weakest_sector,
        sector_details=sector_details,
    ):
        return []

    detail_lines = ['*Sector detail*']

    strongest_detail = _format_sector_detail_lines(
        label='Strongest 24h',
        sector=strongest_sector,
        sector_detail=_find_sector_detail(
            sector=strongest_sector, sector_details=sector_details
        ),
        sector_detail_coin_details=sector_detail_coin_details,
    )
    if strongest_detail:
        detail_lines.extend(['', *strongest_detail])

    if not _is_same_sector(strongest_sector, weakest_sector):
        weakest_detail = _format_sector_detail_lines(
            label='Weakest 24h',
            sector=weakest_sector,
            sector_detail=_find_sector_detail(
                sector=weakest_sector, sector_details=sector_details
            ),
            sector_detail_coin_details=sector_detail_coin_details,
        )
        if weakest_detail:
            detail_lines.extend(['', *weakest_detail])

    if len(detail_lines) == 1:
        return []

    return detail_lines


def should_emit_sector_detail_message(
    strongest_sector: Optional[cmc_type.Sector24hChange],
    weakest_sector: Optional[cmc_type.Sector24hChange],
    sector_details: Dict[str, cmc_type.SectorDetail],
) -> bool:
    for sector in _iter_unique_sectors(strongest_sector, weakest_sector):
        sector_detail = _find_sector_detail(
            sector=sector,
            sector_details=sector_details,
        )
        if sector_detail is None:
            continue

        leaders = _select_sector_coins(
            sector_detail=sector_detail,
            direction='leaders',
            limit=2,
            require_threshold=True,
        )
        losers = _select_sector_coins(
            sector_detail=sector_detail,
            direction='losers',
            limit=2,
            require_threshold=True,
        )
        if len(leaders) > 0 or len(losers) > 0:
            return True

    return False


def collect_sector_detail_coin_ids(
    strongest_sector: Optional[cmc_type.Sector24hChange],
    weakest_sector: Optional[cmc_type.Sector24hChange],
    sector_details: Dict[str, cmc_type.SectorDetail],
) -> List[int]:
    sector_coin_ids = OrderedDict()

    for sector in _iter_unique_sectors(strongest_sector, weakest_sector):
        sector_detail = _find_sector_detail(
            sector=sector,
            sector_details=sector_details,
        )
        if sector_detail is None:
            continue

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
        for coin in [*leaders, *losers]:
            sector_coin_ids.setdefault(coin.id, None)

    return list(sector_coin_ids.keys())


def _format_sentiment_section(
    sentiment: Optional[FearGreedResult],
) -> List[str]:
    lines = ['*Sentiment*']
    if sentiment is None or len(sentiment.data) == 0:
        lines.append('No fear and greed data available.')
        return lines

    current = _find_sentiment_point(sentiment.data, 'Now') or sentiment.data[0]
    yesterday = _find_sentiment_point(sentiment.data, 'Yesterday')
    last_week = _find_sentiment_point(sentiment.data, 'Last week')

    lines.append(f"Now: {_format_sentiment_point(current)}")

    comparisons = []
    if yesterday is not None:
        comparisons.append(f"Yesterday: {_format_sentiment_point(yesterday)}")
    if last_week is not None:
        comparisons.append(f"Last week: {_format_sentiment_point(last_week)}")
    if len(comparisons) > 0:
        lines.append(', '.join(comparisons))

    if len(sentiment.average) > 0:
        average_summaries = [
            f"{escape_markdown(average.timeframe)} avg: {_format_average(average)}"
            for average in sentiment.average
        ]
        lines.append(f"Averages: {', '.join(average_summaries)}")

    return lines


def _format_sector_section(
    strongest_sector: Optional[cmc_type.Sector24hChange],
    weakest_sector: Optional[cmc_type.Sector24hChange],
    sector_details: Dict[str, cmc_type.SectorDetail],
) -> List[str]:
    lines = ['*Sector breadth*']
    if strongest_sector is None and weakest_sector is None:
        lines.append('No sector breadth data available.')
        return lines

    if strongest_sector is not None:
        lines.append(
            _format_sector_breadth_line(
                label='Strongest 24h',
                sector=strongest_sector,
                sector_detail=_find_sector_detail(
                    sector=strongest_sector,
                    sector_details=sector_details,
                ),
            )
        )
    if weakest_sector is not None and not _is_same_sector(
        strongest_sector, weakest_sector
    ):
        lines.append(
            _format_sector_breadth_line(
                label='Weakest 24h',
                sector=weakest_sector,
                sector_detail=_find_sector_detail(
                    sector=weakest_sector,
                    sector_details=sector_details,
                ),
            )
        )

    return lines


def _format_sector_detail_lines(
    label: str,
    sector: Optional[cmc_type.Sector24hChange],
    sector_detail: Optional[cmc_type.SectorDetail],
    sector_detail_coin_details: Dict[int, cmc_type.CoinDetail],
) -> List[str]:
    if sector is None or sector_detail is None:
        return []

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
    if len(leaders) == 0 and len(losers) == 0:
        return []

    lines = [f"{label}: *{escape_markdown(sector.title)}*"]
    detail_sections = [('Leaders', leaders), ('Losers', losers)]
    if sector.avgPriceChange < 0:
        detail_sections = [('Losers', losers), ('Leaders', leaders)]

    for section_label, coins in detail_sections:
        if len(coins) == 0:
            continue
        lines.append(f'{section_label}:')
        lines.extend(
            _format_sector_coin_lines(
                coins=coins,
                include_volume_context=True,
                coin_details=sector_detail_coin_details,
            )
        )
    return lines


def _format_standout_coin_section(
    standout_entries: List[StandoutEntry],
    standout_coin_details: Dict[int, cmc_type.CoinDetail],
) -> List[str]:
    lines = ['*Standout coins*']
    if len(standout_entries) == 0:
        lines.append('No standout coin data available.')
        return lines

    for coin, reasons in standout_entries:
        lines.append(
            _format_standout_coin_line(
                coin=coin,
                coin_detail=standout_coin_details.get(coin.id),
                reasons=reasons,
            )
        )
    return lines


def _merge_spotlight_group(
    spotlight_entries: OrderedDict[int, StandoutEntry],
    reason: str,
    coins: Iterable[cmc_type.TrendingList],
) -> None:
    for coin in coins:
        existing = spotlight_entries.get(coin.id)
        if existing is None:
            spotlight_entries[coin.id] = (coin, [reason])
            continue

        existing_coin, reasons = existing
        if reason not in reasons:
            reasons.append(reason)
        spotlight_entries[coin.id] = (existing_coin, reasons)


def _format_sector_breadth_line(
    label: str,
    sector: cmc_type.Sector24hChange,
    sector_detail: Optional[cmc_type.SectorDetail],
) -> str:
    line = (
        f"{label}: *{escape_markdown(sector.title)}* "
        f"24h {_format_signed_percentage(sector.avgPriceChange)}, "
        f"mcap {_format_signed_percentage(sector.marketChange)}, "
        f"volume {_format_signed_percentage(sector.volumeChange)}, "
        f"gainers {sector.gainersNum}, losers {sector.losersNum}"
    )
    ticker_context = _format_sector_ticker_context(sector_detail=sector_detail)
    if len(ticker_context) > 0:
        return f"{line}, {'; '.join(ticker_context)}"
    return line


def _format_standout_coin_line(
    coin: cmc_type.TrendingList,
    coin_detail: Optional[cmc_type.CoinDetail],
    reasons: List[str],
) -> str:
    reason_text = escape_markdown(', '.join(reasons))
    symbol = escape_markdown(coin.symbol)
    metrics = [
        f"24h {_format_price_change_with_severity(coin.priceChange.priceChange24h)}",
        f"price {_format_price(coin.priceChange.price)}",
        f"volume {escape_markdown(friendly_number(coin.priceChange.volume24h))}",
    ]
    if coin_detail is not None:
        metrics.append(
            'volume change '
            f"{_format_signed_percentage(coin_detail.volumeChangePercentage24h)}"
        )

    return (
        f"• {reason_text}: *{escape_markdown(coin.name)}* {symbol}, "
        + ', '.join(metrics)
    )


def _format_sector_ticker_context(
    sector_detail: Optional[cmc_type.SectorDetail],
) -> List[str]:
    if sector_detail is None:
        return []

    parts = []
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
    if len(leaders) > 0:
        parts.append(
            _format_sector_coin_summary(
                label='leaders',
                coins=leaders,
                include_change=False,
            )
        )
    if len(losers) > 0:
        parts.append(
            _format_sector_coin_summary(
                label='losers',
                coins=losers,
                include_change=False,
            )
        )
    return parts


def _find_sentiment_point(
    points: List[FearGreedData],
    label: str,
) -> Optional[FearGreedData]:
    for point in points:
        if point.relative_date_text == label:
            return point
    return None


def _find_sector_detail(
    sector: Optional[cmc_type.Sector24hChange],
    sector_details: Dict[str, cmc_type.SectorDetail],
) -> Optional[cmc_type.SectorDetail]:
    if sector is None or not sector.sectorId:
        return None
    return sector_details.get(sector.sectorId)


def _iter_unique_sectors(
    strongest_sector: Optional[cmc_type.Sector24hChange],
    weakest_sector: Optional[cmc_type.Sector24hChange],
) -> List[Optional[cmc_type.Sector24hChange]]:
    sectors = [strongest_sector]
    if not _is_same_sector(strongest_sector, weakest_sector):
        sectors.append(weakest_sector)
    return sectors


def _is_same_sector(
    first: Optional[cmc_type.Sector24hChange],
    second: Optional[cmc_type.Sector24hChange],
) -> bool:
    if first is None or second is None:
        return False
    if first.sectorId and second.sectorId:
        return first.sectorId == second.sectorId
    return first.title == second.title


def _select_sector_coins(
    sector_detail: cmc_type.SectorDetail,
    direction: str,
    limit: int,
    require_threshold: bool,
) -> List[cmc_type.SectorCoin]:
    coins_with_change = []
    for coin in sector_detail.coins:
        change = _get_sector_coin_change(coin)
        if change is None:
            continue
        if direction == 'leaders' and change <= 0:
            continue
        if direction == 'losers' and change >= 0:
            continue
        if require_threshold and not _should_include_sector_detail_change(change):
            continue
        coins_with_change.append((coin, change))

    if direction == 'leaders':
        sorted_coins = sorted(coins_with_change, key=lambda item: item[1], reverse=True)
    else:
        sorted_coins = sorted(coins_with_change, key=lambda item: item[1])

    return [coin for coin, _change in sorted_coins[:limit]]


def _format_sector_coin_summary(
    label: str,
    coins: List[cmc_type.SectorCoin],
    include_change: bool,
    include_volume_context: bool = False,
    coin_details: Optional[Dict[int, cmc_type.CoinDetail]] = None,
) -> str:
    entries = [
        _format_sector_coin_entry(
            coin=coin,
            include_change=include_change,
            include_volume_context=include_volume_context,
            coin_detail=coin_details.get(coin.id) if coin_details else None,
        )
        for coin in coins
    ]

    return f"{label} {', '.join(entries)}"


def _format_sector_coin_lines(
    coins: List[cmc_type.SectorCoin],
    include_volume_context: bool,
    coin_details: Optional[Dict[int, cmc_type.CoinDetail]] = None,
) -> List[str]:
    return [
        '• '
        + _format_sector_coin_entry(
            coin=coin,
            include_change=True,
            include_volume_context=include_volume_context,
            coin_detail=coin_details.get(coin.id) if coin_details else None,
            bold_symbol=True,
        )
        for coin in coins
    ]


def _format_sector_coin_entry(
    coin: cmc_type.SectorCoin,
    include_change: bool,
    include_volume_context: bool,
    coin_detail: Optional[cmc_type.CoinDetail],
    bold_symbol: bool = False,
) -> str:
    symbol = escape_markdown(coin.symbol)
    if bold_symbol:
        symbol = f'*{symbol}*'
    if not include_change:
        return symbol

    change = _get_sector_coin_change(coin)
    if change is None:
        return symbol

    entry = f"{symbol} {_format_signed_percentage(change)}"
    if include_volume_context:
        entry = _append_sector_coin_volume_context(
            entry=entry,
            coin=coin,
            coin_detail=coin_detail,
        )
    return entry


def _get_sector_coin_change(coin: cmc_type.SectorCoin) -> Optional[float]:
    if len(coin.quote) == 0:
        return None
    first_quote = next(iter(coin.quote.values()))
    return first_quote.percent_change_24h


def _get_sector_coin_volume(coin: cmc_type.SectorCoin) -> Optional[float]:
    if len(coin.quote) == 0:
        return None
    first_quote = next(iter(coin.quote.values()))
    return first_quote.volume_24h


def _should_include_sector_detail_change(change: float) -> bool:
    return abs(change) >= config.get_cmc_coin_price_change_24h_percentage_threshold()


def _append_sector_coin_volume_context(
    entry: str,
    coin: cmc_type.SectorCoin,
    coin_detail: Optional[cmc_type.CoinDetail],
) -> str:
    volume_24h = _get_sector_coin_volume(coin)
    parts = [entry]
    if volume_24h is not None and volume_24h > 0:
        parts.append(f"vol {escape_markdown(friendly_number(volume_24h))}")
    if coin_detail is not None:
        parts.append(
            'vol chg '
            f"{_format_signed_percentage(coin_detail.volumeChangePercentage24h)}"
        )
    return ', '.join(parts)


def _format_sentiment_point(point: FearGreedData) -> str:
    return (
        f"{escape_markdown(point.sentiment_text)} "
        f"{point.value} {escape_markdown(point.emoji)}"
    )


def _format_average(average: FearGreedAverage) -> str:
    return (
        f"{escape_markdown(average.sentiment_text)} "
        f"{int(round(average.value, 0))} {escape_markdown(average.emoji)}"
    )


def _format_signed_percentage(value: float) -> str:
    return escape_markdown(f'{value:+.2f}%')


def _format_price(value: float) -> str:
    if value >= 1000:
        return escape_markdown(friendly_number(value))
    if value >= 1:
        return _format_decimal_value(value=value, decimal_places=2)
    if value >= 0.01:
        return _format_decimal_value(value=value, decimal_places=4)
    if value >= 0.0001:
        return _format_decimal_value(value=value, decimal_places=6)
    return _format_decimal_value(value=value, decimal_places=8)


def _format_price_change_with_severity(value: float) -> str:
    formatted_change = _format_signed_percentage(value)
    severity = bold_text_for_metric_type(
        metric_type=MetricTypeIndicator.COIN_PRICE_CHANGE_24H,
        value=value / 100,
    )
    if severity == '':
        return formatted_change
    return f'{formatted_change} {severity}'


def _format_decimal_value(value: float, decimal_places: int) -> str:
    quantized = decimal.Decimal(str(value)).quantize(
        decimal.Decimal(f'1e-{decimal_places}'),
        rounding=decimal.ROUND_HALF_UP,
    )
    formatted = format(quantized, 'f').rstrip('0').rstrip('.')
    if formatted == '-0':
        formatted = '0'
    return escape_markdown(formatted)
