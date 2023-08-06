import asyncio

from src.job.job_wrapper import JobWrapper
from src.job.stocks.sentiment_message_sender import StocksSentimentMessageSender
from src.job.stocks.vix_central_message_sender import VixCentralMessageSender
from src.job.stocks.tradingview_message_sender import TradingViewMessageSender
from src.config import config
from src.util.date_util import get_current_datetime
from src.type.market_data_type import MarketDataType

# TODO: test
class StocksNotificationJob(JobWrapper):
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
        return [TradingViewMessageSender(), VixCentralMessageSender(), StocksSentimentMessageSender()]

    @property
    def market_data_type(self):
        return MarketDataType.STOCKS


# ENV=dev poetry run python src/job/stocks/stocks.py --force_run=1 --test_mode=1
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    job = StocksNotificationJob()
    data = asyncio.run(job.start())
    print(data)