from src.http_client import HttpClient

class ThirdPartyChainAnalysisService:
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

    # object of { demand, risk, supply, generation, trading }
    # generation: { name(BTC fees), time, current, change, percentChange, unit }
    async def get_summary(self, symbol: str):
        res = await self.http_client.get(url='/api/summaries', params={'asset': symbol})
        res_json = await res.json()
        return res_json

    # result array: { highlight, path }
    # path: regional-flows, origin-of-fees-redemptions, age-of-held
    async def get_highlights(self, symbol: str):
        res = await self.http_client.get(url='/api/highlights', params={'asset': symbol})
        res_json = await res.json()
        return res_json

    # result object: { highlight, data: { level, change } }
    # level: array of { name, values: array of array of 3 float }
    # change: object of keys(30, 90, 180) -> array of { name, values: array of array of 3 float }
    # 3 x values: 0-2 weeks, 2-52 weeks, 52+ weeks
    async def get_age_of_held(self, symbol: str):
        res = await self.http_client.get(url='/api/age-of-held', params={'asset': symbol})
        res_json = await res.json()
        return res_json

    # result object: { highlight, data: { level, change } }
    # level: array of { name, values: array of array of 3 float }
    # change: object of keys(30, 90, 180) -> array of { name, values: array of array of 3 float }
    # 3 x values: highly liquid, liquid, illiquid
    async def get_liquidity_of_held(self, symbol: str):
        res = await self.http_client.get(url='/api/liquidity-of-held', params={'asset': symbol})
        res_json = await res.json()
        return res_json

    # result object: { highlight, data: { level, change } }
    # level: array of { name, values: array of array of 9 float }
    # change: object of keys(30, 90, 180) -> array of { name, values: array of array of 9 float }
    # 9 x values: -100% to -75%, -75% to -50%, -50% to -25%, -25% to -5%, -5% to 5%, 5% to 25%, 25% to 50%, 50% to 100%, 100%+
    async def unrealised_usd_gain_of_held(self, symbol: str):
        res = await self.http_client.get(url='/api/liquidity-of-held', params={'asset': symbol})
        res_json = await res.json()
        return res_json

    # result object: { highlight, data: { main, secondary } }
    # main: object of keys(unix timestamp milliseconds) -> array of { name, values: array of 2 floats(usd, asset amount) }
    # secondary: array of { name, values: array of array of 4 floats(7d avg, 30d avg, 90d avg, 180d avg) }
    async def get_fees(self, symbol: str):
        res = await self.http_client.get(url='/api/origin-of-fees-redemptions', params={'asset': symbol})
        res_json = await res.json()
        return res_json