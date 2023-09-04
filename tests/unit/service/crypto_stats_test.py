import asyncio
import json
import os
from unittest.mock import patch, AsyncMock

import pytest
from dacite import from_dict
from market_data_library.crypto.cmc.type import Sector24hChange, CMCSector24hChange

from src.service.crypto.crypto_stats import CryptoStatsService

class TestCryptoStatsService:

    def setup_method(self):
        self.load_sector_24h_change()

    def load_sector_24h_change(self):
        dir_path = os.path.dirname(os.path.realpath(__file__))
        file_path = os.path.join(dir_path, '..', '..', 'data', 'cmc', 'cmc_sector_24h_change.json')
        data = json.load(open(file_path))

        sectors_24h_change = from_dict(data_class=CMCSector24hChange, data=data)
        self.sector_24h_change = sectors_24h_change

    @pytest.mark.parametrize('sort_by, sort_direction, limit', [
        ('avg_price_change', 'desc', 10),
        ('avg_price_change', 'asc', 10),
        ('market_cap_change', 'desc', 10),
        ('market_cap_change', 'asc', 10),
    ])
    @pytest.mark.asyncio
    async def test_get_sectors_24h_change(self, sort_by, sort_direction, limit):
        self.service = CryptoStatsService()
        self.service.cmc_service.get_sector_24h_change = AsyncMock(return_value=self.sector_24h_change)
        res = await self.service.get_sectors_24h_change(sort_by=sort_by, sort_direction=sort_direction, limit=limit)
        assert res == self.sector_24h_change.data[:limit]