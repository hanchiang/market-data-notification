import asyncio
import logging

from src.job.crypto.crypto_digest_message_sender import CryptoDigestMessageSender
from src.job.crypto.crypto_signal_digest_message_sender import (
    CryptoSignalDigestMessageSender,
)
from src.job.job_wrapper import JobWrapper
from src.config import config
from src.runtime.runtime_mode import RuntimeMode
from src.type.market_data_type import MarketDataType
from src.util.date_util import get_current_datetime

# TODO: test

logger = logging.getLogger('Crypto notification job')
class CryptoNotificationJob(JobWrapper):
    # run at 8.45am, 4.15pm
    def should_run(self, runtime_mode: RuntimeMode | None = None) -> bool:
        active_runtime_mode = (
            self.runtime_mode if runtime_mode is None else runtime_mode
        )
        if active_runtime_mode.bypass_schedule:
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
            logger.info(
                f'local time: {local}, current time: {now}, local hour to run: {local_hour_int}, local minute to run: {local_minute_int}, current hour {now.hour}, current minute: {now.minute}, delta second: {delta.total_seconds()}, should run: {should_run}')
            if should_run:
                return should_run

        return should_run


    @property
    def message_senders(self):
        return [
            CryptoDigestMessageSender(runtime_mode=self.runtime_mode),
            CryptoSignalDigestMessageSender(runtime_mode=self.runtime_mode),
        ]

    @property
    def market_data_type(self):
        return MarketDataType.CRYPTO

# Usage:
# - Full local test path, including public digest formatting, SQLite signal
#   snapshot persistence, and private/admin signal routing:
#   ENV=dev PYTHONPATH="$(pwd)" poetry run python src/job/crypto/crypto.py --force_run=1 --test_mode=1
# - Production-runtime smoke without Telegram delivery:
#   DISABLE_TELEGRAM=true ENV=dev PYTHONPATH="$(pwd)" poetry run python src/job/crypto/crypto.py --force_run=1
#
# Phase-1 crypto signal output must stay on private/admin routing. The signal
# sender reads CRYPTO_SIGNAL_RECIPIENT_ID, falling back to CRYPTO_TELEGRAM_ADMIN_ID,
# and rejects CRYPTO_TELEGRAM_CHANNEL_ID as an invalid destination.
if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    job = CryptoNotificationJob()
    data = asyncio.run(job.start())
    logger.info(data)
