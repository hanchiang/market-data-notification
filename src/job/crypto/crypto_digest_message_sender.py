from collections import OrderedDict
from typing import Iterable, List, Optional

from market_data_library.types import cmc_type

from src.dependencies import Dependencies
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.type.market_data_type import MarketDataType
from src.type.sentiment import FearGreedAverage, FearGreedData, FearGreedResult
from src.util.date_util import get_current_datetime
from src.util.my_telegram import escape_markdown
from src.util.number import friendly_number


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

        message = self._format_message(
            current=current,
            sentiment=sentiment,
            strongest_sectors=strongest_sectors,
            weakest_sectors=weakest_sectors,
            spotlight=spotlight,
        )
        return [message]

    def _format_message(
        self,
        current,
        sentiment: Optional[FearGreedResult],
        strongest_sectors: List[cmc_type.Sector24hChange],
        weakest_sectors: List[cmc_type.Sector24hChange],
        spotlight: Optional[cmc_type.Spotlight],
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
    ) -> List[str]:
        lines = ['*Sector breadth*']
        if strongest_sector is None and weakest_sector is None:
            lines.append('No sector breadth data available.')
            return lines

        if strongest_sector is not None:
            lines.append(
                self._format_sector_line(label='Strongest 24h', sector=strongest_sector)
            )
        if weakest_sector is not None:
            lines.append(
                self._format_sector_line(label='Weakest 24h', sector=weakest_sector)
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

    def _format_sector_line(self, label: str, sector: cmc_type.Sector24hChange) -> str:
        return (
            f"{label}: *{escape_markdown(sector.title)}* "
            f"24h {self._format_signed_percentage(sector.avgPriceChange)}, "
            f"mcap {self._format_signed_percentage(sector.marketChange)}, "
            f"volume {self._format_signed_percentage(sector.volumeChange)}, "
            f"gainers {sector.gainersNum}, losers {sector.losersNum}"
        )

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

    def _find_sentiment_point(
        self, points: List[FearGreedData], label: str
    ) -> Optional[FearGreedData]:
        for point in points:
            if point.relative_date_text == label:
                return point
        return None

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
