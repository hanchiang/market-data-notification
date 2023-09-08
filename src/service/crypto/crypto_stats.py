from typing import Dict, List

from market_data_library import CMCAPI
from market_data_library.crypto.cmc.type import CMCSector24hChange, CMCCoinDetail, CoinDetail, Sector24hChange


class CryptoStatsService:
    def __init__(self):
        cmc_api = CMCAPI()
        self.cmc_service = cmc_api.cmc_service
        self.cmc_type = cmc_api.cmc_type

    # sort_by: avg_price_change, market_cap_change
    async def get_sectors_24h_change(self, sort_by='avg_price_change', sort_direction='desc', limit: int = 10) -> Dict[str, List[Sector24hChange]]:
        if sort_by is None or sort_by == '':
            sort_by = 'avg_price_change'
        if sort_direction is None or sort_direction == '':
            sort_direction = 'desc'
        if limit is None or limit == '':
            limit = 10
        data: CMCSector24hChange = await self.cmc_service.get_sector_24h_change(sort_by=sort_by, sort_direction=sort_direction)
        return data.data[:limit]

    async def get_coin_detail(self, id: int) -> CoinDetail:
        coin_detail: CMCCoinDetail = await self.cmc_service.get_coin_detail(id=id)
        return coin_detail.data