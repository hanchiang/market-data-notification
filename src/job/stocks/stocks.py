import argparse
import asyncio

from src.job.job_wrapper import JobWrapper
from src.job.stocks.VixCentralMessageSender import VixCentralMessageSender
from src.job.stocks.TradingViewMessageSender import TradingViewMessageSender
from src.config import config
from src.util.date_util import get_current_datetime

# TODO: test. abstract class
class StockDataNotificationJob(JobWrapper):
    # run at 8.45am
    def should_run(self) -> bool:
        if config.get_is_testing_telegram():
            return True

        now = get_current_datetime()
        local = get_current_datetime()
        local = local.replace(hour=config.get_stocks_job_start_local_hour(),
                              minute=config.get_stocks_job_start_local_minute())
        delta = now - local

        should_run = abs(delta.total_seconds()) <= config.get_job_delay_tolerance_second()
        print(
            f'local time: {local}, current time: {now}, local hour to run: {config.get_stocks_job_start_local_hour()}, local minute to run: {config.get_stocks_job_start_local_minute()}, current hour {now.hour}, current minute: {now.minute}, delta second: {delta.total_seconds()}, should run: {should_run}')
        return should_run

    @property
    def message_senders(self):
        return [TradingViewMessageSender(), VixCentralMessageSender()]


# ENV=dev poetry run python src/job/stocks/stocks.py --force_run=1 --test_mode=1
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    job = StockDataNotificationJob()
    data = asyncio.run(job.start())
    print(data)