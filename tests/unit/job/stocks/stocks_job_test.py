from src.job.stocks.stocks import StocksNotificationJob
from src.job.stocks.stocks_digest_message_sender import StocksDigestMessageSender


class TestStocksNotificationJob:
    def test_message_senders_use_single_digest_sender(self) -> None:
        job = StocksNotificationJob()

        senders = job.message_senders

        assert len(senders) == 1
        assert isinstance(senders[0], StocksDigestMessageSender)
