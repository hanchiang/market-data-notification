from typing import List

from market_data_library.crypto.cmc.type import Sector24hChange, CoinDetail

from src.config import config
from src.dependencies import Dependencies
from src.job.message_sender_wrapper import MessageSenderWrapper
from src.type.cmc import CMCSectorSortBy, CMCSectorSortDirection
from src.type.market_data_type import MarketDataType
from src.util.date_util import get_current_datetime
from src.util.my_telegram import escape_markdown
from src.util.number import friendly_number


class TopSectorsMessageSender(MessageSenderWrapper):

    def __init__(self, sort_by: CMCSectorSortBy = CMCSectorSortBy.AVG_PRICE_CHANGE, sort_direction: CMCSectorSortDirection = CMCSectorSortDirection.DESCENDING):
        self.cmc_service = Dependencies.get_crypto_stats_service()
        self.sort_by = sort_by
        self.sort_direction = sort_direction

    @property
    def data_source(self):
        return "CMC"

    @property
    def market_data_type(self):
        return MarketDataType.CRYPTO

    async def format_message(self):
        messages = []
        curr = get_current_datetime()
        messages.append(f"*Top sectors within past 24h at {escape_markdown(curr.strftime('%Y-%m-%d'))}*")

        top_sectors = await self.cmc_service.get_sectors_24h_change(sort_by=self.sort_by.value[0], sort_direction=self.sort_direction.value[0])

        message = await self._format_message(top_sectors=top_sectors, sort_type=self.sort_by, sort_direction=self.sort_direction)

        if message is not None:
            messages.append(message)

        return messages

    async def _format_message(self, top_sectors: List[Sector24hChange], sort_type: CMCSectorSortBy, sort_direction: CMCSectorSortDirection) -> str:
        top_num_sectors = min(3, len(top_sectors))
        res = f'*Top {top_num_sectors} sectors by {sort_direction.value[1]} {sort_type.value[1]}:*\n'

        for i in range (0, top_num_sectors):
            top_sector = top_sectors[i]
            res = f'{res}*Sector {i+1}*: {escape_markdown(top_sector.title)}, average price change: 1d: {escape_markdown(friendly_number(top_sector.avgPriceChange))}%, ' \
                  f'7d: {escape_markdown((friendly_number(top_sector.avgPriceChange7d)))}%, 30d: {escape_markdown((friendly_number(top_sector.avgPriceChange30d)))}%, ' \
                  f'market cap: {escape_markdown(friendly_number(top_sector.marketCap // 1))}, ' \
                  f'market cap change: {escape_markdown(friendly_number(top_sector.marketChange))}%, volume: {escape_markdown(friendly_number(top_sector.marketVolume // 1))}, ' \
                  f'volume change: {escape_markdown(friendly_number(top_sector.volumeChange))}%, gainers: {top_sector.gainersNum}, losers: {top_sector.losersNum}\n'

            # at most 3 top coins
            top_num_coins = 3
            top_coins = top_sector.topCoins[:top_num_coins]
            top_coins_message = ''
            for j in range(0, len(top_coins)):
                top_coin = top_coins[j]
                symbol_escaped = escape_markdown(f'({top_coin.symbol})')
                top_coin_detail = await self.cmc_service.get_coin_detail(id=top_coin.id)
                if not self.should_include_coin(coin_detail=top_coin_detail, sort_type=sort_type):
                    continue
                top_coins_message = f'{top_coins_message}*{escape_markdown(top_coin.name)}{symbol_escaped}*, price: {escape_markdown(friendly_number(top_coin_detail.statistics.price))}, ' \
                      f'price change: 1d: {escape_markdown(friendly_number(top_coin_detail.statistics.priceChangePercentage24h))}%, ' \
                      f'7d: {escape_markdown(friendly_number(top_coin_detail.statistics.priceChangePercentage7d))}%, ' \
                      f'30d: {escape_markdown(friendly_number(top_coin_detail.statistics.priceChangePercentage30d))}%, ' \
                      f'volume: {escape_markdown(friendly_number(int(top_coin_detail.volume)))}, ' \
                      f'volume change 1d: {escape_markdown(friendly_number(top_coin_detail.volumeChangePercentage24h))}%, ' \
                      f'rank: {top_coin_detail.statistics.rank}, volume mc rank: {top_coin_detail.statistics.volumeMcRank}, '
                if top_coin_detail.statistics.marketCap // 1 > 0:
                    top_coins_message = f'{top_coins_message} market cap: {escape_markdown(friendly_number(top_coin_detail.statistics.marketCap // 1))}, '
                if top_coin_detail.statistics.marketCapChangePercentage24h > 0:
                    top_coins_message = f'{top_coins_message} market change 1d: {escape_markdown(friendly_number(top_coin_detail.statistics.marketCapChangePercentage24h))}%, '
                if top_coin_detail.statistics.marketCapDominance > 0:
                    top_coins_message = f'{top_coins_message} market dominance: {escape_markdown(friendly_number(top_coin_detail.statistics.marketCapDominance))}%, '

                top_coins_message = top_coins_message[:-3]
                top_coins_message = f'{top_coins_message}\n\n'

                # include top holders?

            if len(top_coins_message) > 0:
                res = f'{res}*\nTop coins:*\n'
                res = f'{res}{top_coins_message}'
            res = f'{res}\n'
        return res

    def should_include_coin(self, coin_detail: CoinDetail, sort_type: CMCSectorSortBy):
        if sort_type == CMCSectorSortBy.AVG_PRICE_CHANGE:
            return abs(coin_detail.statistics.priceChangePercentage24h) >= config.get_cmc_coin_price_change_24h_percentage_threshold()
        elif sort_type == CMCSectorSortBy.MARKET_CAP_CHANGE:
            return abs(coin_detail.statistics.marketCapChangePercentage24h) >= config.get_cmc_market_cap_change_24h_percentage_threshold()
        else:
            return 0
