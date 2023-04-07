import json
from typing import List, Any

from src.config import config
from src.db.redis import Redis
from src.util.my_telegram import escape_markdown

market_indices = ['SPY', 'QQQ', 'IWM', 'DJIA']
market_indices = sorted(market_indices)

market_indices_order_map = {}
for i in range(len(market_indices)):
    market_indices_order_map[market_indices[i]] = i + 1

async def get_tradingview_daily_stocks_data() -> dict:
    try:
        key = get_redis_key_for_stocks()
        tradingview_data = await Redis.get_client().zrange(key, start=0, end=0, desc=True, withscores=True)
        if (len(tradingview_data) == 0):
            return {"key": key, "data": None, "score": None}
        return {"key": key, "data": json.loads(tradingview_data[0][0]), "score": int(tradingview_data[0][1])}
    except Exception as e:
        print(e)
        return {}

# score = timestamp of current date(without time)
# return: [<add count>, <remove count>]
async def save_tradingview_data(data: str, score: int):
    key = get_redis_key_for_stocks()
    tradingview_data = await Redis.get_client().zrange(key, start=score, end=score, desc=True, byscore=True)
    # data for the day is already saved
    if tradingview_data is not None and len(tradingview_data) > 0:
        print(f"trading view data for {score} already exist. skip saving to redis")
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

def format_tradingview_message(payload: List[Any]):
    if len(payload) == 0:
        return None
    message = ''
    potential_overextended_by_symbol = config.get_potential_overextended_by_symbol()

    sorted_payload = sorted(payload, key=payload_sorter)

    for p in sorted_payload:
        symbol = p['symbol'].upper()

        close = p['close']
        ema20 = p['ema20']
        close_ema20_delta_ratio = (close - ema20) / ema20 if close > ema20 else -(ema20 - close) / ema20

        # For VIX, compare close and overextended threshold. For other symbols, compare close_ema20_delta_ratio and overextended threshold
        close_ema20_delta_percent = f"{close_ema20_delta_ratio:.2%}"

        if symbol != 'VIX':
            message = f"{message}\nsymbol: *{symbol}*, close: {escape_markdown(str(close))}, {escape_markdown('ema20(1D)')}: {escape_markdown(str(f'{ema20:.2f}'))}, % diff from ema20: {escape_markdown(close_ema20_delta_percent)}"
            close_ema20_direction = 'above' if close > ema20 else 'below'
        else:
            message = f"{message}\nsymbol: *{symbol}*, close: {escape_markdown(str(close))}"

        if potential_overextended_by_symbol.get(symbol, None) is not None:
            if symbol != 'VIX':
                if potential_overextended_by_symbol[symbol].get(close_ema20_direction) is not None:
                    overextended_threshold = potential_overextended_by_symbol[symbol][close_ema20_direction]
                    if abs(close_ema20_delta_ratio) > abs(overextended_threshold):
                        message = f"{message}, *which is greater than the median overextended threshold of {escape_markdown(f'{overextended_threshold:.2%}')} when it is {'above' if close_ema20_direction == 'above' else 'below'} the ema20, watch for potential reversal* ‼️"
            else:
                vix_overextended_up_threshold = potential_overextended_by_symbol[symbol]['above']
                vix_overextended_down_threshold = potential_overextended_by_symbol[symbol]['below']
                if close >= vix_overextended_up_threshold:
                    message = f"{message}, *VIX is near the top around {f'{escape_markdown(str(vix_overextended_up_threshold))}'}, market could be near the bottom, watch for potential reversal* ‼️"
                elif close <= vix_overextended_down_threshold:
                    message = f"{message}, *VIX is near the bottom around {f'{escape_markdown(str(vix_overextended_down_threshold))}'}, market could be near the top, watch for potential reversal* ‼️"
    return message

def payload_sorter(item):
    symbol = item['symbol'].upper()

    # market indices should appear first
    if market_indices_order_map.get(symbol, False):
        return str(market_indices_order_map[symbol])

    # VIX should appear last
    if symbol == 'VIX':
        return 'zzzzzzzzzzzzzzzzzz'

    return symbol


def get_redis_key_for_stocks():
    is_testing_telegram = config.get_is_testing_telegram()
    key = 'tradingview-stocks'
    if is_testing_telegram:
        key = f'{key}-dev'
    return key

def get_redis_key_for_crypto():
    is_testing_telegram = config.get_is_testing_telegram()
    key = 'tradingview-crypto'
    if is_testing_telegram:
        key = f'{key}-dev'
    # key = f'{key}:{date.year}-{str(date.month).zfill(2)}-{str(date.day).zfill(2)}'
    return key