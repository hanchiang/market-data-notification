import json
from typing import List

from src.config import config
from src.db.redis import Redis
from src.type.trading_view import TradingViewDataType, TradingViewData

class TradingViewService:
    async def get_tradingview_daily_stocks_data(self, type: TradingViewDataType) -> dict:
        try:
            key = self.get_redis_key_for_stocks(type)
            tradingview_data = await Redis.get_client().zrange(key, start=0, end=0, desc=True, withscores=True)
            if (len(tradingview_data) == 0):
                return {"key": key, "data": None, "score": None}
            return {"key": key, "data": json.loads(tradingview_data[0][0]), "score": int(tradingview_data[0][1])}
        except Exception as e:
            print(e)
            return {}

    # score = timestamp of current date(without time)
    # return: [<add count>, <remove count>]
    async def save_tradingview_data(self, data: str, key: str, score: int, test_mode: bool = False):
        tradingview_data = await Redis.get_client().zrange(key, start=score, end=score, desc=True, byscore=True)
        # data for the day is already saved
        if not test_mode and tradingview_data is not None and len(tradingview_data) > 0:
            return [0, 0]

        json_data = {}
        json_data[data] = score
        add_res = await Redis.get_client().zadd(key, json_data)

        # remove old keys
        num_elements = await Redis.get_client().zcard(key)
        if num_elements <= config.get_trading_view_days_to_store():
            return [add_res, 0]

        num_elements_to_remove = num_elements - config.get_trading_view_days_to_store()
        remove_res = await Redis.get_client().zremrangebyrank(key, 0, num_elements_to_remove - 1)

        return [add_res, remove_res]

    def hydrate_data_list(self, data_list: List[dict], type: TradingViewDataType) -> List[TradingViewData]:
        return [TradingViewData(type=type, symbol=x.get('symbol'), timeframe=x.get('timeframe'),
                                close_prices=x.get('close_prices'), ema20s=x.get('ema20s'), volumes=x.get('volumes')
                                ) for x in data_list
                ]

    def get_redis_key_for_stocks(self, type: TradingViewDataType):
        is_testing_telegram = config.get_is_testing_telegram()
        key = f'tradingview-{type.value}'
        if is_testing_telegram:
            key = f'{key}-dev'

        return key

    def get_redis_key_for_crypto(self):
        is_testing_telegram = config.get_is_testing_telegram()
        key = 'tradingview-crypto'
        if is_testing_telegram:
            key = f'{key}-dev'
        # key = f'{key}:{date.year}-{str(date.month).zfill(2)}-{str(date.day).zfill(2)}'
        return key