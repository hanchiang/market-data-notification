import asyncio
import sys

from src.config import config
from src.db.redis import Redis
from src.dependencies import Dependencies
from src.job.service.tradingview import get_tradingview_data, format_tradingview_message, get_datetime_from_redis_key
from src.job.service.vix_central import format_vix_central_message
from src.notification_destination.telegram_notification import send_message_to_channel
from src.type.market_data_type import MarketDataType
from src.util.context_manager import TimeTrackerContext
from src.util.date_util import get_current_datetime, get_datetime_from_timestamp
from src.util.my_telegram import format_messages_to_telegram, escape_markdown

# TODO: test. abstract class
async def stocks_data_notification_job(argv):
    force_run = argv[1] == 'true' if len(argv) > 1 else False
    if not force_run and not should_run():
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

            if tradingview_data.get('data', None) is not None:
                tradingview_message = format_tradingview_message(tradingview_data['data'].get('data', []))
                if tradingview_message is not None:
                    tradingview_date = get_datetime_from_timestamp(tradingview_data['score']).strftime("%Y-%m-%d")
                    tradingview_message = f"*Trading view market data at {escape_markdown(tradingview_date)}:*{tradingview_message}"
                    messages.append(tradingview_message)

            vix_central_service = Dependencies.get_vix_central_service()
            vix_central_data = await vix_central_service.get_recent_values()
            vix_central_message = format_vix_central_message(vix_central_data)
            if vix_central_message is not None:
                messages.append(vix_central_message)

            telegram_message = format_messages_to_telegram(messages)

            res = await send_message_to_channel(message=telegram_message, chat_id=config.get_telegram_stocks_channel_id(), market_data_type=MarketDataType.STOCKS)
            return res
        except Exception as e:
            print(e)
            messages.append(f"{escape_markdown(str(e))}")
            message = format_messages_to_telegram(messages)
            await send_message_to_channel(message=message, chat_id=config.get_telegram_stocks_admin_id(), market_data_type=MarketDataType.STOCKS)
            return None
        finally:
            await Redis.stop_redis()
            await vix_central_service.cleanup()

# run at 8.45am
def should_run() -> bool:
    if config.get_is_testing_telegram():
        return True

    now = get_current_datetime()
    local = get_current_datetime()
    local = local.replace(hour=config.get_stocks_job_start_local_hour(), minute=config.get_stocks_job_start_local_minute())
    delta = now - local

    should_run = abs(delta.total_seconds()) <= config.get_job_delay_tolerance_second()
    print(
        f'local time: {local}, current time: {now}, local hour to run: {config.get_stocks_job_start_local_hour()}, local minute to run: {config.get_stocks_job_start_local_minute()}, current hour {now.hour}, current minute: {now.minute}, delta second: {delta.total_seconds()}, should run: {should_run}')
    return should_run


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    data = asyncio.run(stocks_data_notification_job(sys.argv))
    print(data)