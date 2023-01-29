import json
import urllib.parse

import re

from src.config import config
from src.http_client import HttpClient


class ThirdPartyMessariService:
    BASE_URL = 'https://graphql.messari.io'

    exchanges = ['Bitfinex', 'Bitmex', 'Binance', 'Bitstamp', 'Bittrex', 'Gemini', 'Huobi', 'Kraken', 'Poloniex']

    symbol_to_slug = {
        'BTC': 'bitcoin',
        'ETH': 'ethereum',
        'ATOM': 'cosmos',
        'AVAX': 'avalanche',
        'SOL': 'solana',
        'ARB': 'arbitrum'
    }

    def __init__(self, http_client: HttpClient):
        self.http_client = http_client

    async def cleanup(self):
        await self.http_client.cleanup()

    # result object: { data -> asset -> metrics }
    # keys under metrics: ranks, pricing, allTimeHigh, cycleLow, returnOnInvestment, marketcap, volume, risk,
    # supply, issuance, supplyActivity, supplyDistribution, miningHashrate, miningSupply, miningDifficulty, addressesActivity,
    # addressesDistribution, exchangeNetFlows, exchangeSupply, exchangeDeposits, exchangeWithdrawals,
    # fees, revenue, transactionsActivity, contractsActivity, blocks, utxos, lendingActivity, lendingRates,
    # developerActivity, tokenSale, miscellaneous
    # exchangeSupply: id, supplyOnExchangesUsd, supplyOnExchangesNative(quantity), supplyOn<exchange name>Usd, supplyOn<exchange name>Native
    # exchangeNetFlows: id, netFlowsExchangesUsd, netFlowsExchangesNative(quantity), netFlows<exchange name>Usd, netFlows<exchange name>Native
    # list of exchanges: Bitfinex, Bitmex, Binance, Bitstamp, Bittrex, Gemini, huobi, Kraken, Poloniex

    # slug example: bitcoin
    async def get_metrics(self, symbol: str):
        slug = ThirdPartyMessariService.symbol_to_slug.get(symbol, None)
        if slug is None:
            raise RuntimeError(f"[get metrics] Slug for {symbol} is not found")

        operation_name = "AssetMetrics"
        variables = {"slug": slug}
        extensions = {
            "persistedQuery": {
                "version" : 1,
                "sha256Hash" : config.get_messari_asset_metrics_sha256()
            }
        }

        space_regex = re.compile(f'\s+')
        variables_cleaned = re.sub(space_regex, '', json.dumps(variables))
        extensions_cleaned = re.sub(space_regex, '', json.dumps(extensions))

        url = '/query?'
        safe_chars = '{},=:'
        query = urllib.parse.urlencode({
                'operationName': operation_name,
                'variables': variables_cleaned,
                'extensions': extensions_cleaned
            }, safe=safe_chars)
        url = f"{url}{query}"

        res = await self.http_client.get(url=url)

        if res.status != 200:
            print(res.text)
            res.raise_for_status()
        res_json = await res.json()
        return res_json