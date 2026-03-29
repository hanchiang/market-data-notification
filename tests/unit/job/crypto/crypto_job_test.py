from src.job.crypto.crypto import CryptoNotificationJob
from src.job.crypto.crypto_digest_message_sender import CryptoDigestMessageSender


class TestCryptoNotificationJob:
    def test_message_senders_use_single_digest_sender(self) -> None:
        job = CryptoNotificationJob()

        senders = job.message_senders

        assert len(senders) == 1
        assert isinstance(senders[0], CryptoDigestMessageSender)
