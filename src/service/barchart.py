from src.third_party_service.barchart import ThirdPartyBarchartService


class BarchartService:
    def __init__(self, third_party_service=ThirdPartyBarchartService):
        self.third_party_service = third_party_service

    async def cleanup(self):
        await self.third_party_service.cleanup()

    # TODO: Cache results. Data type
    async def get_stock_price(self, symbol: str, num_days = 30):
        # TODO: format data in market data library
        data = await self.third_party_service.get_stock_price(symbol=symbol, num_days=num_days)
        data = data.rstrip()
        formatted_prices = list(map(BarchartService.format_stock_price_object, data.split('\n')))
        data = formatted_prices
        return data

    @staticmethod
    def format_stock_price_object(item):
        (symbol, date, open, high, low, close, volume) = item.split(',')
        return {
            'symbol': symbol,
            'date': date,
            'open': float(open),
            'high': float(high),
            'low': float(low),
            'close': float(close),
            'volume': float(volume)
        }