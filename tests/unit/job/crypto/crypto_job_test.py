from src.runtime.runtime_mode import RuntimeMode
from src.job.crypto.crypto import CryptoNotificationJob
from src.job.crypto.crypto_digest_message_sender import CryptoDigestMessageSender
from src.job.crypto.crypto_signal_digest_message_sender import (
    CryptoSignalDigestMessageSender,
)


class TestCryptoNotificationJob:
    def test_message_senders_use_public_and_operator_crypto_senders(self) -> None:
        job = CryptoNotificationJob()

        senders = job.message_senders

        assert len(senders) == 2
        assert isinstance(senders[0], CryptoDigestMessageSender)
        assert isinstance(senders[1], CryptoSignalDigestMessageSender)

    def test_should_run_bypasses_schedule_in_test_mode(self) -> None:
        job = CryptoNotificationJob()

        assert job.should_run(RuntimeMode.from_test_mode(True)) is True
