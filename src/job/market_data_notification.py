import asyncio
import datetime
import json
from functools import reduce
from typing import List, Any

from src.config import config
from src.db.redis import Redis
from src.dependencies import Dependencies
from src.notification_destination.telegram_notification import send_message_to_channel
from src.service.vix_central import RecentVixFuturesValues
from src.util.context_manager import TimeTrackerContext
from src.util.date_util import get_current_datetime
from src.util.my_telegram import format_messages_to_telegram, escape_markdown

async def market_data_notification_job():
    if not should_run():
        return

    with TimeTrackerContext('market_data_notification_job'):
        # TODO: May need a lock in the future
        messages = []
        if config.get_is_testing_telegram():
            messages.insert(0, '*THIS IS A TEST MESSAGE: Parameters have been adjusted*')
        elif config.get_simulate_tradingview_traffic():
            messages.insert(0, '*SIMULATING TRAFFIC FROM TRADING VIEW*')

        try:
            await Redis.start_redis()
            await Dependencies.build()
            tradingview_data = await get_tradingview_data()

            if tradingview_data is not None:
                tradingview_message = format_tradingview_message(tradingview_data.get('data', []))
                tradingview_message = f"*Trading view market data:*{tradingview_message}"
                messages.append(tradingview_message)

            vix_central_service = Dependencies.get_vix_central_service()
            vix_central_data = await vix_central_service.get_recent_values()
            vix_central_message = format_vix_central_message(vix_central_data)

            messages.append(vix_central_message)
            telegram_message = format_messages_to_telegram(messages)

            res = await send_message_to_channel(message=telegram_message, chat_id=config.get_telegram_channel_id())
            return res
        except Exception as e:
            print(e)
            messages.append(f"{escape_markdown(str(e))}")
            message = format_messages_to_telegram(messages)
            await send_message_to_channel(message=message, chat_id=config.get_telegram_admin_id())
            return None
        finally:
            await Redis.stop_redis()

# run 1 hour before market open at 9.30am
def should_run() -> bool:
    now = get_current_datetime()
    local = get_current_datetime()
    local = local.replace(hour=config.get_notification_job_start_local_hour(), minute=config.get_notification_job_start_local_minute())
    delta = now - local

    should_run = abs(delta.total_seconds()) <= config.get_notification_job_delay_tolerance_second()
    print(
        f'local hour to run: {config.get_notification_job_start_local_hour()}, local minute to run: {config.get_notification_job_start_local_minute()}, current hour {now.hour}, current minute: {now.minute}, delta second: {delta.total_seconds()}, should run: {should_run}')
    return should_run

async def get_tradingview_data():
    now = get_current_datetime()
    try:
        # Usually this job is run before market open the next day, so we need to get data from the previous day.
        # If this job is run on the same day. Then we will get the data from that day.
        # key: <source>-<yyyymmdd>
        key = f'tradingview-{now.year}{str(now.month).zfill(2)}{str(now.day).zfill(2)}'
        tradingview_data = await Redis.get_client().get(key)
        if tradingview_data is not None:
            return json.loads(tradingview_data)
        now = get_current_datetime() - datetime.timedelta(days=1)
        key = f'tradingview-{now.year}{str(now.month).zfill(2)}{str(now.day).zfill(2)}'
        tradingview_data = await Redis.get_client().get(key)
        return json.loads(tradingview_data)
    except Exception as e:
        print(e)
        return {}

# TODO: cleanup trading view webhook code
def format_vix_central_message(vix_central_value: RecentVixFuturesValues):
    message = reduce(format_vix_futures_values, vix_central_value.vix_futures_values,
                     f"*VIX central data for {vix_central_value.vix_futures_values[0].futures_date} futures:*")
    if vix_central_value.is_contango_decrease_for_past_n_days:
        message = f"{message}\n*Contango has been decreasing for the past {vix_central_value.contango_decrease_past_n_days} days ‼️*"
    return message


def format_vix_futures_values(res, curr):
    message = f"{res}\ndate: {escape_markdown(curr.current_date)}, contango %: {escape_markdown(curr.formatted_contango)}"
    if curr.is_contango_single_day_decrease_alert:
        threshold = f"{curr.contango_single_day_decrease_alert_ratio:.1%}"
        message = f"{message}{escape_markdown('.')} *Contango changed by more than {escape_markdown(threshold)} from the previous day* ‼️"
    return message


def format_tradingview_message(payload: List[Any]):
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
                        message = f"{message}, *greater than {escape_markdown(f'{overextended_threshold:.2%}')} when it is {'above' if close_ema20_direction == 'up' else 'below'} the ema20, watch for potential rebound* ‼️"
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

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    data = asyncio.run(market_data_notification_job())
    print(data)