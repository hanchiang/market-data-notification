from typing import Dict, List

from market_data_library.types import cmc_type
from src.data_source.market_data_library import get_crypto_api


class CryptoStatsService:
    def __init__(self):
        crypto_api = get_crypto_api()
        self.cmc_service = crypto_api.cmc.cmc_service
        self.cmc_type = cmc_type

    # sort_by: avg_price_change, market_cap_change
    async def get_sectors_24h_change(self, sort_by='avg_price_change', sort_direction='desc', limit: int = 10) -> Dict[str, List[cmc_type.Sector24hChange]]:
        if sort_by is None or sort_by == '':
            sort_by = 'avg_price_change'
        if sort_direction is None or sort_direction == '':
            sort_direction = 'desc'
        if limit is None or limit == '':
            limit = 10
        data: cmc_type.CMCSector24hChange = await self.cmc_service.get_sector_24h_change(sort_by=sort_by, sort_direction=sort_direction)
        return data.data[:limit]

    async def get_coin_detail(self, id: int) -> cmc_type.CoinDetail:
        coin_detail: cmc_type.CMCCoinDetail = await self.cmc_service.get_coin_detail(id=id)
        return coin_detail.data

    async def get_spotlight(self, limit=30, rank_range=500, timeframe='24h') -> cmc_type.Spotlight:
        data: cmc_type.CMCSpotlight = await self.cmc_service.get_spotlight(limit=limit, rank_range=rank_range, timeframe=timeframe)
        return data.data
