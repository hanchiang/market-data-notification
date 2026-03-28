from src.job.crypto.crypto import CryptoNotificationJob
from src.job.crypto.sentiment_message_sender import SentimentMessageSender
from src.job.crypto.top_coins_message_sender import TopCoinsMessageSender
from src.job.crypto.top_sectors_message_sender import TopSectorsMessageSender


class TestCryptoNotificationJob:
    def test_message_senders_excludes_cryptoquant_sender(self) -> None:
        job = CryptoNotificationJob()

        senders = job.message_senders

        assert len(senders) == 8
        assert sum(isinstance(sender, TopSectorsMessageSender) for sender in senders) == 4
        assert sum(isinstance(sender, TopCoinsMessageSender) for sender in senders) == 3
        assert sum(isinstance(sender, SentimentMessageSender) for sender in senders) == 1
