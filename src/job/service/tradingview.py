import json
from typing import List, Any

from src.config import config
from src.db.redis import Redis
from src.type.trading_view import TradingViewDataType, TradingViewData
from src.util.my_telegram import escape_markdown

market_indices = ['SPY', 'QQQ', 'IWM', 'DJIA']
market_indices = sorted(market_indices)

market_indices_order_map = {}
for i in range(len(market_indices)):
    market_indices_order_map[market_indices[i]] = i + 1

# TODO: Refactor with job/stocks/
async def get_tradingview_daily_stocks_data(type: TradingViewDataType) -> dict:
    try:
        key = get_redis_key_for_stocks(type)
        tradingview_data = await Redis.get_client().zrange(key, start=0, end=0, desc=True, withscores=True)
        if (len(tradingview_data) == 0):
            return {"key": key, "data": None, "score": None}
        return {"key": key, "data": json.loads(tradingview_data[0][0]), "score": int(tradingview_data[0][1])}
    except Exception as e:
        print(e)
        return {}

# score = timestamp of current date(without time)
# return: [<add count>, <remove count>]
async def save_tradingview_data(data: str, key: str, score: int, test_mode: bool = False):
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

def format_tradingview_message(stocks_payload: dict, economy_indicator_payload: dict):
    if len(stocks_payload.get('data', [])) == 0:
        return None

    stocks_list = hydrate_data_list(data_list=stocks_payload.get('data'), type=TradingViewDataType.STOCKS)
    economy_indicator_list = hydrate_data_list(data_list=economy_indicator_payload.get('data'), type=TradingViewDataType.ECONOMY_INDICATOR)


    sorted_stocks = sorted(stocks_list, key=payload_sorter)
    sorted_economy_indicators = sorted(economy_indicator_list, key=payload_sorter)

    message = format_message_for_stocks(sorted_stocks)
    message = f"{message}\n{format_message_for_economy_indicators(sorted_economy_indicators)}"
    return message

def hydrate_data_list(data_list: List[dict], type: TradingViewDataType) -> List[TradingViewData]:
    return [TradingViewData(type=type, symbol=x.get('symbol'), timeframe=x.get('timeframe'),
                            close_prices=x.get('close_prices'), ema20s=x.get('ema20s'), volumes=x.get('volumes')
                            ) for x in data_list
            ]

def format_message_for_stocks(sorted_payload: List[TradingViewData]):
    message = ''
    for p in sorted_payload:
        symbol = p.symbol.upper()

        # TODO: Compare recent prices/volumes
        close = p.close_prices[0]
        ema20 = p.ema20s[0]
        volumes = p.volumes
        close_ema20_delta_ratio = (close - ema20) / ema20 if close > ema20 else -(ema20 - close) / ema20
        close_ema20_delta_percent = f"{close_ema20_delta_ratio:.2%}"
        potential_overextended_by_symbol = config.get_potential_overextended_by_symbol()

        message = f"{message}\nsymbol: *{symbol}*, close: {escape_markdown(str(close))}, {escape_markdown('ema20(1D)')}: {escape_markdown(str(f'{ema20:.2f}'))}, % diff from ema20: {escape_markdown(close_ema20_delta_percent)}"
        close_ema20_direction = 'above' if close > ema20 else 'below'

        if potential_overextended_by_symbol.get(symbol, None) is not None:
            if potential_overextended_by_symbol.get(symbol, {}).get(close_ema20_direction, None) is not None:
                overextended_threshold = potential_overextended_by_symbol[symbol][close_ema20_direction]
                if abs(close_ema20_delta_ratio) > abs(overextended_threshold):
                    message = f"{message}, *which is greater than the median overextended threshold of {escape_markdown(f'{overextended_threshold:.2%}')} when it is {'above' if close_ema20_direction == 'above' else 'below'} the ema20, watch for potential reversal* ‼️"
    return message

def format_message_for_economy_indicators(sorted_payload: List[TradingViewData]):
    message = ''
    for p in sorted_payload:
        symbol = p.symbol.upper()

        # TODO: Compare recent prices/volumes
        close = p.close_prices[0]
        potential_overextended_by_symbol = config.get_potential_overextended_by_symbol()

        # For VIX, compare close and overextended threshold. For other symbols, compare close_ema20_delta_ratio and overextended threshold
        message = f"{message}\nsymbol: *{symbol}*, close: {escape_markdown(str(close))}"

        if potential_overextended_by_symbol.get(symbol, None) is not None:
            vix_overextended_up_threshold = potential_overextended_by_symbol[symbol].get('above', None)
            vix_overextended_down_threshold = potential_overextended_by_symbol[symbol].get('below', None)
            if vix_overextended_up_threshold is not None and close >= vix_overextended_up_threshold:
                message = f"{message}, *VIX is near the top around {f'{escape_markdown(str(vix_overextended_up_threshold))}'}, market could be near the bottom, watch for potential reversal* ‼️"
            elif vix_overextended_down_threshold is not None and close <= vix_overextended_down_threshold:
                message = f"{message}, *VIX is near the bottom around {f'{escape_markdown(str(vix_overextended_down_threshold))}'}, market could be near the top, watch for potential reversal* ‼️"

    return message


def payload_sorter(item: TradingViewData):
    symbol = item.symbol.upper()

    if item.type == TradingViewDataType.STOCKS:
        # market indices should appear first
        if market_indices_order_map.get(symbol, False):
            return str(market_indices_order_map[symbol])
        return symbol

    # VIX should appear last
    # if symbol == 'VIX':
    #     return 'zzzzzzzzzzzzzzzzzz'

    return symbol


def get_redis_key_for_stocks(type: TradingViewDataType):
    is_testing_telegram = config.get_is_testing_telegram()
    key = f'tradingview-{type.value}'
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