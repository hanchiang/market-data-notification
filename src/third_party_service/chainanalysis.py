from src.http_client import HttpClient

class ThirdPartyChainanalysisService:
    BASE_URL = 'https://markets.chainalysis.com'

    def __init__(self, http_client: HttpClient):
        self.http_client = http_client

    async def cleanup(self):
        await self.http_client.cleanup()

    # result object: { highlight, data -> main, secondary }
    async def get_trade_intensity(self, symbol: str):
        res = await self.http_client.get(url='/api/trade-intensity', params={'asset': symbol})
        res_json = await res.json()
        return res_json