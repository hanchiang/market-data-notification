
from typing import List
from src.data_source.market_data_library import get_tradfi_api
from market_data_library.types import barchart_type
class ThirdPartyBarchartService:
    def __init__(self): 
        self.barchart_stocks = get_tradfi_api().barchart.barchart_stocks

    async def cleanup(self):
        await self.barchart_stocks.cleanup()

    async def get_stock_price(self, symbol: str, num_days = 30) -> List[barchart_type.StockPrice]:
        data = await self.barchart_stocks.get_stock_prices(symbol=symbol, max_records=num_days)
        return data
