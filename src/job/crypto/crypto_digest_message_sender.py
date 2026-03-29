import asyncio
import logging
from collections import OrderedDict
from typing import Dict, Iterable, List, Optional

from market_data_library.types import cmc_type

from src.config import config
from src.dependencies import Dependencies
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.type.market_data_type import MarketDataType
from src.type.sentiment import FearGreedAverage, FearGreedData, FearGreedResult
from src.util.date_util import get_current_datetime
from src.util.my_telegram import escape_markdown
from src.util.number import friendly_number


logger = logging.getLogger('Crypto digest message sender')
class CryptoDigestMessageSender(MessageSenderWrapper):
    def __init__(self):
        self.cmc_service = Dependencies.get_crypto_stats_service()
        self.sentiment_service = Dependencies.get_crypto_sentiment_service()

    @property
    def data_source(self):
        return 'CMC + Alternative.me'

    @property
    def market_data_type(self):
        return MarketDataType.CRYPTO

    async def format_message(self) -> List[str]:
        current = get_current_datetime()
        sentiment = await self.sentiment_service.get_crypto_fear_greed_index()
        strongest_sectors = await self.cmc_service.get_sectors_24h_change(
            sort_by='avg_price_change', sort_direction='desc', limit=1
        )
        weakest_sectors = await self.cmc_service.get_sectors_24h_change(
            sort_by='avg_price_change', sort_direction='asc', limit=1
        )
        spotlight = await self.cmc_service.get_spotlight()
        sector_details = await self._load_sector_details(
            strongest_sector=strongest_sectors[0] if strongest_sectors else None,
            weakest_sector=weakest_sectors[0] if weakest_sectors else None,
        )

        message = self._format_message(
            current=current,
            sentiment=sentiment,
            strongest_sectors=strongest_sectors,
            weakest_sectors=weakest_sectors,
            spotlight=spotlight,
            sector_details=sector_details,
        )
        messages = [message]
        sector_detail_message = self._format_sector_detail_message(
            strongest_sector=strongest_sectors[0] if strongest_sectors else None,
            weakest_sector=weakest_sectors[0] if weakest_sectors else None,
            sector_details=sector_details,
        )
        if sector_detail_message is not None:
            messages.append(sector_detail_message)
        return messages

    def _format_message(
        self,
        current,
        sentiment: Optional[FearGreedResult],
        strongest_sectors: List[cmc_type.Sector24hChange],
        weakest_sectors: List[cmc_type.Sector24hChange],
        spotlight: Optional[cmc_type.Spotlight],
        sector_details: Dict[str, cmc_type.SectorDetail],
    ) -> str:
        lines = [
            f"*Crypto market digest*: {escape_markdown(current.strftime('%Y-%m-%d'))}",
            '',
        ]

        lines.extend(self._format_sentiment_section(sentiment))
        lines.append('')
        lines.extend(
            self._format_sector_section(
                strongest_sector=strongest_sectors[0] if strongest_sectors else None,
                weakest_sector=weakest_sectors[0] if weakest_sectors else None,
                sector_details=sector_details,
            )
        )
        lines.append('')
        lines.extend(self._format_coin_section(spotlight))

        return '\n'.join(line for line in lines if line is not None).strip()

    def _format_sentiment_section(
        self, sentiment: Optional[FearGreedResult]
    ) -> List[str]:
        lines = ['*Sentiment*']
        if sentiment is None or len(sentiment.data) == 0:
            lines.append('No fear and greed data available.')
            return lines

        current = self._find_sentiment_point(sentiment.data, 'Now') or sentiment.data[0]
        yesterday = self._find_sentiment_point(sentiment.data, 'Yesterday')
        last_week = self._find_sentiment_point(sentiment.data, 'Last week')

        lines.append(
            f"Now: {self._format_sentiment_point(current)}"
        )

        comparisons = []
        if yesterday is not None:
            comparisons.append(f"Yesterday: {self._format_sentiment_point(yesterday)}")
        if last_week is not None:
            comparisons.append(f"Last week: {self._format_sentiment_point(last_week)}")

        if len(comparisons) > 0:
            lines.append(', '.join(comparisons))

        if len(sentiment.average) > 0:
            average_summaries = [
                f"{escape_markdown(average.timeframe)} avg: {self._format_average(average)}"
                for average in sentiment.average
            ]
            lines.append(f"Averages: {', '.join(average_summaries)}")

        return lines

    def _format_sector_section(
        self,
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
                self._format_sector_line(
                    label='Strongest 24h',
                    sector=strongest_sector,
                    sector_detail=self._find_sector_detail(
                        sector=strongest_sector, sector_details=sector_details
                    ),
                )
            )
        if weakest_sector is not None and not self._is_same_sector(
            strongest_sector, weakest_sector
        ):
            lines.append(
                self._format_sector_line(
                    label='Weakest 24h',
                    sector=weakest_sector,
                    sector_detail=self._find_sector_detail(
                        sector=weakest_sector, sector_details=sector_details
                    ),
                )
            )

        return lines

    def _format_sector_detail_message(
        self,
        strongest_sector: Optional[cmc_type.Sector24hChange],
        weakest_sector: Optional[cmc_type.Sector24hChange],
        sector_details: Dict[str, cmc_type.SectorDetail],
    ) -> Optional[str]:
        detail_lines = ['*Sector detail*']

        strongest_detail = self._format_sector_detail_lines(
            label='Strongest 24h',
            sector=strongest_sector,
            sector_detail=self._find_sector_detail(
                sector=strongest_sector, sector_details=sector_details
            ),
        )
        if strongest_detail:
            detail_lines.extend(['', *strongest_detail])

        if not self._is_same_sector(strongest_sector, weakest_sector):
            weakest_detail = self._format_sector_detail_lines(
                label='Weakest 24h',
                sector=weakest_sector,
                sector_detail=self._find_sector_detail(
                    sector=weakest_sector, sector_details=sector_details
                ),
            )
            if weakest_detail:
                detail_lines.extend(['', *weakest_detail])

        if len(detail_lines) == 1:
            return None

        return '\n'.join(detail_lines)

    def _format_sector_detail_lines(
        self,
        label: str,
        sector: Optional[cmc_type.Sector24hChange],
        sector_detail: Optional[cmc_type.SectorDetail],
    ) -> List[str]:
        if sector is None or sector_detail is None:
            return []

        leaders = self._select_sector_coins(
            sector_detail=sector_detail,
            direction='leaders',
            limit=2,
            require_threshold=True,
        )
        losers = self._select_sector_coins(
            sector_detail=sector_detail,
            direction='losers',
            limit=2,
            require_threshold=True,
        )
        if len(leaders) == 0 and len(losers) == 0:
            return []

        lines = [f"{label}: *{escape_markdown(sector.title)}*"]
        if len(leaders) > 0:
            lines.append(
                self._format_sector_coin_summary(
                    label='Leaders', coins=leaders, include_change=True
                )
            )
        if len(losers) > 0:
            lines.append(
                self._format_sector_coin_summary(
                    label='Losers', coins=losers, include_change=True
                )
            )
        return lines

    def _format_coin_section(
        self, spotlight: Optional[cmc_type.Spotlight]
    ) -> List[str]:
        lines = ['*Standout coins*']
        if spotlight is None:
            lines.append('No standout coin data available.')
            return lines

        spotlight_entries = OrderedDict()
        spotlight_groups = [
            ('trending', spotlight.trendingList[:2]),
            ('top gainer', spotlight.gainerList[:2]),
            ('top loser', spotlight.loserList[:2]),
        ]
        for reason, coins in spotlight_groups:
            self._merge_spotlight_group(
                spotlight_entries=spotlight_entries, reason=reason, coins=coins
            )

        if len(spotlight_entries) == 0:
            lines.append('No standout coin data available.')
            return lines

        for coin, reasons in list(spotlight_entries.values())[:5]:
            lines.append(self._format_coin_line(coin=coin, reasons=reasons))

        return lines

    def _merge_spotlight_group(
        self,
        spotlight_entries: OrderedDict[int, tuple[cmc_type.TrendingList, List[str]]],
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

    def _format_sector_line(
        self,
        label: str,
        sector: cmc_type.Sector24hChange,
        sector_detail: Optional[cmc_type.SectorDetail],
    ) -> str:
        line = (
            f"{label}: *{escape_markdown(sector.title)}* "
            f"24h {self._format_signed_percentage(sector.avgPriceChange)}, "
            f"mcap {self._format_signed_percentage(sector.marketChange)}, "
            f"volume {self._format_signed_percentage(sector.volumeChange)}, "
            f"gainers {sector.gainersNum}, losers {sector.losersNum}"
        )
        ticker_context = self._format_sector_ticker_context(sector_detail=sector_detail)
        if len(ticker_context) > 0:
            return f"{line}, {'; '.join(ticker_context)}"
        return line

    def _format_coin_line(
        self, coin: cmc_type.TrendingList, reasons: List[str]
    ) -> str:
        reason_text = escape_markdown(', '.join(reasons))
        symbol = escape_markdown(coin.symbol)
        return (
            f"• {reason_text}: *{escape_markdown(coin.name)}* {symbol}, "
            f"24h {self._format_signed_percentage(coin.priceChange.priceChange24h)}, "
            f"7d {self._format_signed_percentage(coin.priceChange.priceChange7d)}, "
            f"price {self._format_price(coin.priceChange.price)}, "
            f"mcap {escape_markdown(friendly_number(coin.marketCap))}, "
            f"volume {escape_markdown(friendly_number(coin.priceChange.volume24h))}"
        )

    def _format_sentiment_point(self, point: FearGreedData) -> str:
        return (
            f"{escape_markdown(point.sentiment_text)} "
            f"{point.value} {escape_markdown(point.emoji)}"
        )

    def _format_average(self, average: FearGreedAverage) -> str:
        return (
            f"{escape_markdown(average.sentiment_text)} "
            f"{int(round(average.value, 0))} {escape_markdown(average.emoji)}"
        )

    def _format_sector_ticker_context(
        self, sector_detail: Optional[cmc_type.SectorDetail]
    ) -> List[str]:
        if sector_detail is None:
            return []

        parts = []
        leaders = self._select_sector_coins(
            sector_detail=sector_detail,
            direction='leaders',
            limit=2,
            require_threshold=False,
        )
        losers = self._select_sector_coins(
            sector_detail=sector_detail,
            direction='losers',
            limit=2,
            require_threshold=False,
        )
        if len(leaders) > 0:
            parts.append(
                self._format_sector_coin_summary(
                    label='leaders', coins=leaders, include_change=False
                )
            )
        if len(losers) > 0:
            parts.append(
                self._format_sector_coin_summary(
                    label='losers', coins=losers, include_change=False
                )
            )
        return parts

    def _find_sentiment_point(
        self, points: List[FearGreedData], label: str
    ) -> Optional[FearGreedData]:
        for point in points:
            if point.relative_date_text == label:
                return point
        return None

    def _is_same_sector(
        self,
        first: Optional[cmc_type.Sector24hChange],
        second: Optional[cmc_type.Sector24hChange],
    ) -> bool:
        if first is None or second is None:
            return False
        if first.sectorId and second.sectorId:
            return first.sectorId == second.sectorId
        return first.title == second.title

    def _should_include_sector_detail_coin(self, coin: cmc_type.TopCoin) -> bool:
        return (
            abs(coin.percentageChangePriceUsd)
            >= config.get_cmc_coin_price_change_24h_percentage_threshold()
        )

    async def _load_sector_details(
        self,
        strongest_sector: Optional[cmc_type.Sector24hChange],
        weakest_sector: Optional[cmc_type.Sector24hChange],
    ) -> Dict[str, cmc_type.SectorDetail]:
        sectors = []
        for sector in [strongest_sector, weakest_sector]:
            if sector is None or not sector.sectorId:
                continue
            if any(existing.sectorId == sector.sectorId for existing in sectors):
                continue
            sectors.append(sector)

        if len(sectors) == 0:
            return {}

        details = await asyncio.gather(
            *[
                self.cmc_service.get_sector_detail(sector_id=sector.sectorId)
                for sector in sectors
            ],
            return_exceptions=True,
        )
        sector_details = {}
        for sector, detail in zip(sectors, details, strict=False):
            if isinstance(detail, Exception):
                logger.warning(
                    'Skipping sector detail enrichment for %s (%s): %s',
                    sector.title,
                    sector.sectorId,
                    detail,
                )
                continue
            sector_details[sector.sectorId] = detail
        return sector_details

    def _find_sector_detail(
        self,
        sector: Optional[cmc_type.Sector24hChange],
        sector_details: Dict[str, cmc_type.SectorDetail],
    ) -> Optional[cmc_type.SectorDetail]:
        if sector is None or not sector.sectorId:
            return None
        return sector_details.get(sector.sectorId)

    def _select_sector_coins(
        self,
        sector_detail: cmc_type.SectorDetail,
        direction: str,
        limit: int,
        require_threshold: bool,
    ) -> List[cmc_type.SectorCoin]:
        coins_with_change = []
        for coin in sector_detail.coins:
            change = self._get_sector_coin_change(coin)
            if change is None:
                continue
            if direction == 'leaders' and change <= 0:
                continue
            if direction == 'losers' and change >= 0:
                continue
            if require_threshold and not self._should_include_sector_detail_change(change):
                continue
            coins_with_change.append((coin, change))

        if direction == 'leaders':
            sorted_coins = sorted(coins_with_change, key=lambda item: item[1], reverse=True)
        else:
            sorted_coins = sorted(coins_with_change, key=lambda item: item[1])

        return [coin for coin, _change in sorted_coins[:limit]]

    def _format_sector_coin_summary(
        self,
        label: str,
        coins: List[cmc_type.SectorCoin],
        include_change: bool,
    ) -> str:
        entries = []
        for coin in coins:
            symbol = escape_markdown(coin.symbol)
            if not include_change:
                entries.append(symbol)
                continue

            change = self._get_sector_coin_change(coin)
            if change is None:
                entries.append(symbol)
                continue
            entries.append(f"{symbol} {self._format_signed_percentage(change)}")

        return f"{label} {', '.join(entries)}"

    def _get_sector_coin_change(self, coin: cmc_type.SectorCoin) -> Optional[float]:
        if len(coin.quote) == 0:
            return None
        first_quote = next(iter(coin.quote.values()))
        return first_quote.percent_change_24h

    def _should_include_sector_detail_change(self, change: float) -> bool:
        return (
            abs(change) >= config.get_cmc_coin_price_change_24h_percentage_threshold()
        )

    def _format_signed_percentage(self, value: float) -> str:
        return escape_markdown(f'{value:+.2f}%')

    def _format_price(self, value: float) -> str:
        if value >= 1000:
            return escape_markdown(friendly_number(value))
        if value >= 1:
            return escape_markdown(friendly_number(value, decimal_places=2))
        if value >= 0.01:
            return escape_markdown(friendly_number(value, decimal_places=4))
        if value >= 0.0001:
            return escape_markdown(friendly_number(value, decimal_places=6))
        return escape_markdown(friendly_number(value, decimal_places=8))
