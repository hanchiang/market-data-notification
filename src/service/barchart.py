from typing import List

from market_data_library.types import barchart_type
from src.third_party_service.barchart import ThirdPartyBarchartService


class BarchartService:
    def __init__(self, third_party_service=ThirdPartyBarchartService):
        self.third_party_service = third_party_service

    async def cleanup(self):
        await self.third_party_service.cleanup()

    # TODO: Cache results. Data type
    async def get_stock_price(self, symbol: str, num_days = 30):
        # TODO: format data in market data library
        data: List[barchart_type.StockPrice] = await self.third_party_service.get_stock_price(symbol=symbol, num_days=num_days)
        return data
