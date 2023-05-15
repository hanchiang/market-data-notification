from src.third_party_service.messari import ThirdPartyMessariService

# TODO: type
class AssetMetrics:
    def __init__(self):
        self.symbol = '' # BTC
        self.slug = '' # bitcoin
        self.price_usd: float = None
        # key = exchange, value = { usd, quantity }
        self.exchange_supply = {}
        # key = exchange, value = { usd, quantity }
        self.exchange_net_flows = {}

    def sort_exchange_supply_and_net_flows_descending(self, absolute=False):
        if self.exchange_supply is not None:
            self.exchange_supply = {k: v for k, v in sorted(self.exchange_supply.items(),
                                                               key=lambda exchange_obj: MessariService.exchange_usd_quantity_sorter(exchange_obj=exchange_obj, absolute=absolute),
                                                               reverse=True)}
        if self.exchange_net_flows is not None:
            self.exchange_net_flows = {k: v for k, v in sorted(self.exchange_net_flows.items(),
                                                                  key=lambda exchange_obj: MessariService.exchange_usd_quantity_sorter(exchange_obj=exchange_obj, absolute=absolute),
                                                                  reverse=True)}

class MessariService:
    def __init__(self, third_party_service = ThirdPartyMessariService):
        self.third_party_service = third_party_service

    async def cleanup(self):
        await self.third_party_service.cleanup()

    async def get_asset_metrics(self, symbol='BTC') -> AssetMetrics:
        symbol = symbol.upper()
        res = await self.third_party_service.get_metrics(symbol=symbol)
        return self.transform_third_party_asset_metrics(res=res, symbol=symbol)

    # TODO: test
    def transform_third_party_asset_metrics(self, res, symbol) -> AssetMetrics:
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

        # Net flows
        ret_val.exchange_net_flows = dict(ret_val.exchange_net_flows, **{exchange: {
            'usd': exchange_net_flows.get(f'netFlows{exchange}Usd', None),
            'quantity': exchange_net_flows.get(f'netFlows{exchange}Native', None),
        } for exchange in exchanges})

        return ret_val

    @staticmethod
    def exchange_usd_quantity_sorter(exchange_obj: dict, absolute=False):
        [exchange, exchange_usd_quantity] = exchange_obj
        ret_val = exchange_usd_quantity.get('quantity', 0)
        if absolute:
            return abs(ret_val)
        return ret_val


