import asyncio

from src.job.crypto.chain_analysis_message_sender import ChainAnalysisMessageSender
from src.job.crypto.messari_message_sender import MessariMessageSender
from src.job.job_wrapper import JobWrapper
from src.config import config
from src.type.market_data_type import MarketDataType
from src.util.date_util import get_current_datetime

# TODO: test

class CryptoNotificationJob(JobWrapper):
    # run at 8.45am, 4.15pm
    def should_run(self) -> bool:
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

    @property
    def message_senders(self):
        return [MessariMessageSender(), ChainAnalysisMessageSender()]

    @property
    def market_data_type(self):
        return MarketDataType.CRYPTO

# ENV=dev poetry run python src/job/crypto/crypto.py --force_run=1 --test_mode=1
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    job = CryptoNotificationJob()
    data = asyncio.run(job.start())
    print(data)