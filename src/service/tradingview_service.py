import json
import logging
from typing import Optional

from src.config import config
from src.db.redis import Redis
from src.type.trading_view import TradingViewDataType, TradingViewData, TradingViewStocksData, TradingViewRedisData
from src.util.exception import get_exception_message

logger = logging.getLogger('Trading view service')
class TradingViewService:
    async def get_tradingview_daily_stocks_data(self, type: TradingViewDataType) -> Optional[TradingViewRedisData]:
        try:
            key = self.get_redis_key_for_stocks(type)
            tradingview_data = await Redis.get_client().zrange(key, start=0, end=0, desc=True, withscores=True)
            if (len(tradingview_data) == 0):
                return TradingViewRedisData(key=key, data=None, score=None)
            data_parsed = json.loads(tradingview_data[0][0])
            return TradingViewRedisData(key=key, data=self.hydrate_tradingview_data(data_parsed), score=int(tradingview_data[0][1]))
        except Exception as e:
            logger.error(get_exception_message(e, cls=self.__class__.__name__, should_escape_markdown=True))
            return None

    # score = timestamp of current date(without time)
    # return: [<add count>, <remove count>]
    async def save_tradingview_data(self, data: str, key: str, score: int, test_mode: bool = False):
        redis_client = Redis.get_client()
        tradingview_data = await redis_client.zrange(key, start=score, end=score, desc=True, byscore=True)
        # Keep request-local test replay semantics, but let the non-test path replace
        # an existing same-score payload so the latest market-close anchor wins.
        # Queue the remove and add in one Redis transaction to avoid a gap where the
        # old anchor is deleted but the replacement has not been written yet.
        if not test_mode and tradingview_data is not None and len(tradingview_data) > 0:
            pipeline = redis_client.pipeline(transaction=True)
            pipeline.zremrangebyscore(key, min=score, max=score)
            pipeline.zadd(key, {data: score})
            (_, add_res) = await pipeline.execute()
        else:
            add_res = await redis_client.zadd(key, {data: score})

        # remove old keys
        num_elements = await redis_client.zcard(key)
        if num_elements <= config.get_trading_view_days_to_store():
            return [add_res, 0]

        num_elements_to_remove = num_elements - config.get_trading_view_days_to_store()
        remove_res = await redis_client.zremrangebyrank(key, 0, num_elements_to_remove - 1)

        return [add_res, remove_res]

    def hydrate_tradingview_data(self, data) -> TradingViewData:
        return TradingViewData(type=TradingViewDataType(data.get('type')), unix_ms=data.get('unix_ms'), data=[
            TradingViewStocksData(symbol=x.get('symbol'), timeframe=x.get('timeframe'), close_prices=x.get('close_prices'), ema20s=x.get('ema20s'), volumes=x.get('volumes'))
            for x in data.get('data')
        ])

    def get_redis_key_for_stocks(self, type: TradingViewDataType):
        return f'tradingview-{type.value}'

    def get_redis_key_for_crypto(self):
        # key = f'{key}:{date.year}-{str(date.month).zfill(2)}-{str(date.day).zfill(2)}'
        return 'tradingview-crypto'
