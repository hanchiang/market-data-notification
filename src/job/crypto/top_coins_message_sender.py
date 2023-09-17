from typing import List

from market_data_library.crypto.cmc.type import CoinDetail, TrendingList

from src.config import config
from src.dependencies import Dependencies
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.type.cmc import CMCSectorSortBy, CMCSpotlightType
from src.type.market_data_type import MarketDataType
from src.util.date_util import get_current_datetime
from src.util.my_telegram import escape_markdown
from src.util.number import friendly_number


class TopCoinsMessageSender(MessageSenderWrapper):

    def __init__(self, spotlight_type: CMCSpotlightType = CMCSpotlightType.TRENDING):
        self.cmc_service = Dependencies.get_crypto_stats_service()
        self.spotlight_type = spotlight_type

    @property
    def data_source(self):
        return "CMC"

    @property
    def market_data_type(self):
        return MarketDataType.CRYPTO

    async def format_message(self):
        messages = []
        curr = get_current_datetime()
        messages.append(f"*{self.spotlight_type.value.capitalize()} coins within past 24h at {escape_markdown(curr.strftime('%Y-%m-%d'))}*")

        spotlight = await self.cmc_service.get_spotlight()
        data = []
        if self.spotlight_type == CMCSpotlightType.TRENDING:
            data = spotlight.trendingList
        elif self.spotlight_type == CMCSpotlightType.LOSER_LIST:
            data = spotlight.loserList
        elif self.spotlight_type == CMCSpotlightType.GAINER_LIST:
            data = spotlight.gainerList
        elif self.spotlight_type == CMCSpotlightType.MOST_VISITED:
            data = spotlight.mostVisitedList

        message = await self._format_message(coin_list=data)

        if message is not None:
            messages.append(message)

        return messages

    async def _format_message(self, coin_list: List[TrendingList]) -> str:
        num_coins_to_show = min(5, len(coin_list))
        res = ''

        for i in range (0, num_coins_to_show):
            coin = coin_list[i]
            symbol_escaped = escape_markdown(f'({coin.symbol})')
            coin_detail = await self.cmc_service.get_coin_detail(id=coin.id)

            res = f'{res}*{i+1}*: *{escape_markdown(coin.name)}{symbol_escaped}*, price: {escape_markdown(friendly_number(coin.priceChange.price))}, ' \
                  f'price change 24h: {escape_markdown(friendly_number(coin.priceChange.priceChange24h))}%, 7d: {escape_markdown(friendly_number(coin.priceChange.priceChange7d))}%, ' \
                  f'30d: {escape_markdown(friendly_number(coin.priceChange.priceChange30d))}%, volume 24h: {escape_markdown(friendly_number(coin.priceChange.volume24h))}, ' \
                  f'volume change 24h: {escape_markdown(friendly_number(coin_detail.volumeChangePercentage24h))}%, ' \
                  f'market cap: ${escape_markdown(friendly_number(coin.marketCap))}, ' \
                  f'market cap change 24h: {escape_markdown(friendly_number(coin_detail.statistics.marketCapChangePercentage24h))}%, ' \
                  f'watch count: {coin_detail.watchCount}, watchlist ranking: {coin_detail.watchListRanking}, ' \
                  f'rank: {coin.rank}, volume mc rank: {coin_detail.statistics.volumeMcRank}\n\n'

        return res

    def should_include_coin(self, coin_detail: CoinDetail, sort_type: CMCSectorSortBy):
        if sort_type == CMCSectorSortBy.AVG_PRICE_CHANGE:
            return abs(coin_detail.statistics.priceChangePercentage24h) >= config.get_cmc_coin_price_change_24h_percentage_threshold()
        elif sort_type == CMCSectorSortBy.MARKET_CAP_CHANGE:
            return abs(coin_detail.statistics.marketCapChangePercentage24h) >= config.get_cmc_market_cap_change_24h_percentage_threshold()
        else:
            return 0
