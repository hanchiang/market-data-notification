import datetime
import json
from typing import List, Any

from src.config import config
from src.db.redis import Redis
from src.util.date_util import get_current_datetime
from src.util.my_telegram import escape_markdown


async def get_tradingview_data() -> dict:
    now = get_current_datetime()
    try:
        key = get_redis_key(now)
        tradingview_data = await Redis.get_client().zrange(key, start=0, end=0, desc=True)
        return {"key": key, "data": json.loads(tradingview_data[0])}
    except Exception as e:
        print(e)
        return {}

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
            message = f"{message}\nsymbol: {symbol}, close: {escape_markdown(str(close))}, {escape_markdown('ema20(1D)')}: {escape_markdown(str(f'{ema20:.2f}'))}, % change from ema20: {escape_markdown(close_ema20_delta_percent)}"
            close_ema20_direction = 'up' if close > ema20 else 'down'
        else:
            message = f"{message}\nsymbol: {symbol}, close: {escape_markdown(str(close))}"

        if potential_overextended_by_symbol.get(symbol, None) is not None:
            if symbol != 'VIX':
                if potential_overextended_by_symbol[symbol].get(close_ema20_direction) is not None:
                    overextended_threshold = potential_overextended_by_symbol[symbol][close_ema20_direction]
                    if abs(close_ema20_delta_ratio) > abs(overextended_threshold):
                        message = f"{message}, *greater than overextended threshold {escape_markdown(f'{overextended_threshold:.2%}')} when it is {'above' if close_ema20_direction == 'up' else 'below'} the ema20, watch for potential rebound* ‼️"
            else:
                vix_overextended_up_threshold = potential_overextended_by_symbol[symbol]['up']
                vix_overextended_down_threshold = potential_overextended_by_symbol[symbol]['down']
                if close >= vix_overextended_up_threshold:
                    message = f"{message}, *VIX is near the top around {f'{escape_markdown(str(vix_overextended_up_threshold))}'}, meaning market is near the bottom, watch for potential rebound* ‼️"
                elif close <= vix_overextended_down_threshold:
                    message = f"{message}, *VIX is near the bottom around {f'{escape_markdown(str(vix_overextended_down_threshold))}'}, meaning market is near the top, watch for potential rebound* ‼️"
    return message

def payload_sorter(item):
    symbol = item['symbol'].upper()
    # VIX should appear last
    return symbol if symbol != 'VIX' else 'zzzzzzzzzzzzzz'

# key: <source>:<yyyy>-<mm>-<dd>
def get_redis_key(date: datetime.datetime):
    is_testing_telegram = config.get_is_testing_telegram()
    key = 'tradingview'
    if is_testing_telegram:
        key = f'{key}-dev'
    key = f'{key}:{date.year}-{str(date.month).zfill(2)}-{str(date.day).zfill(2)}'
    return key

def get_datetime_from_redis_key(key: str):
    return key.split(':')[-1]