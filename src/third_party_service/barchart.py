from market_data_library import MarketDataAPI

class ThirdPartyBarchartService:
    def __init__(self):
        self.barchart_stocks = MarketDataAPI().barchart_stocks

    async def cleanup(self):
        # TODO:
        pass

    async def get_stock_price(self, symbol: str, num_days = 30):
        data = await self.barchart_stocks.get_stock_prices(symbol=symbol, max_records=num_days)
        return data
