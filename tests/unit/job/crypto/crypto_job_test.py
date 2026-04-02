from src.runtime.runtime_mode import RuntimeMode
from src.job.crypto.crypto import CryptoNotificationJob
from src.job.crypto.crypto_digest_message_sender import CryptoDigestMessageSender


class TestCryptoNotificationJob:
    def test_message_senders_use_single_digest_sender(self) -> None:
        job = CryptoNotificationJob()

        senders = job.message_senders

        assert len(senders) == 1
        assert isinstance(senders[0], CryptoDigestMessageSender)

    def test_should_run_bypasses_schedule_in_test_mode(self) -> None:
        job = CryptoNotificationJob()

        assert job.should_run(RuntimeMode.from_test_mode(True)) is True
