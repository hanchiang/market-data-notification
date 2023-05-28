import argparse
import asyncio
import sys

from src.config import config
from src.db.redis import Redis
from src.dependencies import Dependencies
from src.job.service.messari import format_messari_metrics
from src.job.service.chainanalysis import format_chainanalysis
from src.notification_destination.telegram_notification import send_message_to_channel
from src.type.market_data_type import MarketDataType
from src.util.context_manager import TimeTrackerContext
from src.util.date_util import get_current_datetime
from src.util.my_telegram import format_messages_to_telegram, escape_markdown

# TODO: test. abstract class
async def crypto_data_notification_job():
    parser = argparse.ArgumentParser(description='Sends daily crypto data notification to telegram')
    parser.add_argument('--force_run', type=int, choices=[0, 1], default=0,
                        help='Run regardless of the timing it is scheduled to run at')
    parser.add_argument('--test_mode', type=int, choices=[0, 1], default=0, help='Run in test mode for dev testing')
    cli_args = parser.parse_args()

    force_run: bool = cli_args.force_run == 1
    test_mode: bool = cli_args.test_mode == 1

    if test_mode:
        config.set_is_testing_telegram('true')

    if not force_run and not should_run():
        return

    symbol = 'BTC'
    with TimeTrackerContext('crypto_notification_job'):
        # TODO: May need a lock in the future
        # TODO: Split messari and chainanalysis
        messages = []
        if config.get_is_testing_telegram():
            messages.insert(0, '*THIS IS A TEST MESSAGE: Parameters have been adjusted*')
        elif config.get_simulate_tradingview_traffic():
            messages.insert(0, '*SIMULATING TRAFFIC FROM TRADING VIEW*')

        try:
            await Redis.start_redis()
            await Dependencies.build()

            curr = get_current_datetime()
            messages.append(f"*Crypto market data at {escape_markdown(curr.strftime('%Y-%m-%d'))}*")

            messari_service = Dependencies.get_messari_service()
            messari_res = await messari_service.get_asset_metrics()
            messari_message = format_messari_metrics(messari_res)
            if messari_message is not None:
                messages.append(messari_message)

            thirdparty_chainanalysis_service = Dependencies.get_thirdparty_chainanalysis_service()
            chainanalysis_service = Dependencies.get_chainanalysis_service()
            chainanalysis_trade_intensity = await thirdparty_chainanalysis_service.get_trade_intensity(symbol=symbol)
            chainanalysis_fees = await chainanalysis_service.get_fees(symbol=symbol)

            thirdparty_chainanalysis_message = format_chainanalysis(trade_intensity=chainanalysis_trade_intensity, fees=chainanalysis_fees)
            if thirdparty_chainanalysis_message is not None:
                messages.append(thirdparty_chainanalysis_message)

            telegram_message = format_messages_to_telegram(messages)

            res = await send_message_to_channel(message=telegram_message, chat_id=config.get_telegram_crypto_channel_id(), market_data_type=MarketDataType.CRYPTO)
            return res
        except Exception as e:
            print(e)
            messages.append(f"{escape_markdown(str(e))}")
            message = format_messages_to_telegram(messages)
            await send_message_to_channel(message=message, chat_id=config.get_telegram_crypto_admin_id(), market_data_type=MarketDataType.CRYPTO)
            return None
        finally:
            await Redis.stop_redis()
            await messari_service.cleanup()

# run at 8.45am, 4.15pm
def should_run() -> bool:
    if config.get_is_testing_telegram():
        return True

    now = get_current_datetime()
    local = get_current_datetime()
    start_local_hours = config.get_crypto_job_start_local_hours().split(',')
    start_local_minutes = config.get_crypto_job_start_local_minutes().split(',')

    if len(start_local_hours) != len(start_local_minutes):
        raise RuntimeError("start local hours and start local minutes are not configured properly")

    for i in range(0, len(start_local_hours)):
        local_hour_int = int(start_local_hours[i])
        local_minute_int = int(start_local_minutes[i])

        local = local.replace(hour=local_hour_int, minute=local_minute_int)
        delta = now - local
        should_run = abs(delta.total_seconds()) <= config.get_job_delay_tolerance_second()
        if should_run:
            print(
                f'local time: {local}, current time: {now}, local hour to run: {local_hour_int}, local minute to run: {local_minute_int}, current hour {now.hour}, current minute: {now.minute}, delta second: {delta.total_seconds()}, should run: {should_run}')
            return should_run

    print(
        f'local time: {local}, current time: {now}, local hour to run: {local_hour_int}, local minute to run: {local_minute_int}, current hour {now.hour}, current minute: {now.minute}, delta second: {delta.total_seconds()}, should run: {should_run}')
    return should_run

# ENV=dev poetry run python src/job/crypto.py --force_run=1 --test_mode=1
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    data = asyncio.run(crypto_data_notification_job())
    print(data)