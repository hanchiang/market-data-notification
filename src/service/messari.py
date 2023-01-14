from src.third_party_service.messari import ThirdPartyMessariService

class AssetMetrics:
    def __init__(self):
        self.symbol = '' # BTC
        self.slug = '' # bitcoin
        self.price_usd: float = None
        self.exchange_supply = {}
        self.exchange_net_flows = {}

class MessariService:
    def __init__(self, third_party_service = ThirdPartyMessariService):
        self.third_party_service = third_party_service

    async def cleanup(self):
        await self.third_party_service.cleanup()

    async def get_asset_metrics(self, symbol='BTC') -> AssetMetrics:
        symbol = symbol.upper()
        res = await self.third_party_service.get_metrics(symbol=symbol)
        return self.format_third_party_asset_metrics(res=res, symbol=symbol)

    # TODO: test
    def format_third_party_asset_metrics(self, res, symbol) -> AssetMetrics:
        ret_val = AssetMetrics()

        metrics = res['data']['asset']['metrics']

        slug = ThirdPartyMessariService.symbol_to_slug.get(symbol, None)
        ret_val.symbol = symbol
        ret_val.slug = slug

        pricing = metrics.get('pricing', {})
        exchange_supply = metrics.get('exchangeSupply', {})
        exchange_net_flows = metrics.get('exchangeNetFlows', {})

        ret_val.price_usd = pricing.get('priceUsd')

        exchanges = ThirdPartyMessariService.exchanges[:]
        # Exchange supply
        ret_val.exchange_supply = dict(ret_val.exchange_supply, **{'Total': {
            'usd': exchange_supply.get(f'supplyOnExchangesUsd', None),
            'quantity': exchange_supply.get(f'supplyOnExchangesNative', None),
        }})
        ret_val.exchange_supply = dict(ret_val.exchange_supply, **{exchange: {
            'usd': exchange_supply.get(f'supplyOn{exchange}Usd', None),
            'quantity': exchange_supply.get(f'supplyOn{exchange}Native', None),
        } for exchange in exchanges})
        ret_val.exchange_supply = {k: v for k, v in sorted(ret_val.exchange_supply.items(),
                                                           key=MessariService.exchange_usd_quantity_sorter,
                                                           reverse=True)}

        # Net flows
        ret_val.exchange_net_flows = dict(ret_val.exchange_net_flows, **{exchange: {
            'usd': exchange_net_flows.get(f'netFlows{exchange}Usd', None),
            'quantity': exchange_net_flows.get(f'netFlows{exchange}Native', None),
        } for exchange in exchanges})
        ret_val.exchange_net_flows = { k: v for k, v in sorted(ret_val.exchange_net_flows.items(), key=MessariService.exchange_usd_quantity_sorter, reverse=True)}

        return ret_val

    @staticmethod
    def exchange_usd_quantity_sorter(exchange_obj: dict):
        [exchange, exchange_usd_quantity] = exchange_obj
        return exchange_usd_quantity.get('quantity', 0)


