from src.runtime.runtime_mode import RuntimeMode
from src.job.stocks.stocks import StocksNotificationJob
from src.job.stocks.stocks_digest_message_sender import StocksDigestMessageSender


class TestStocksNotificationJob:
    def test_message_senders_use_single_digest_sender(self) -> None:
        job = StocksNotificationJob()

        senders = job.message_senders

        assert len(senders) == 1
        assert isinstance(senders[0], StocksDigestMessageSender)

    def test_should_run_bypasses_schedule_in_test_mode(self) -> None:
        job = StocksNotificationJob()

        assert job.should_run(RuntimeMode.from_test_mode(True)) is True
