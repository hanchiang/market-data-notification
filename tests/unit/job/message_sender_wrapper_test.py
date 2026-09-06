from unittest.mock import AsyncMock

import pytest

from src.job.message_sender_wrapper import MessageSenderWrapper
from src.runtime.runtime_mode import RuntimeMode
from src.type.market_data_type import MarketDataType


class ExplodingSender(MessageSenderWrapper):
    """A sender whose `format_message` raises, which is the common job failure."""

    def __init__(self, runtime_mode: RuntimeMode | None = None):
        super().__init__()
        self.runtime_mode = runtime_mode

    @property
    def data_source(self) -> str:
        return 'Exploding'

    @property
    def market_data_type(self) -> MarketDataType:
        return MarketDataType.CRYPTO

    async def format_message(self):
        raise RuntimeError('format_message exploded')


@pytest.mark.asyncio
async def test_sender_failure_alert_survives_disable_telegram(monkeypatch):
    """A sender's own crash must not be muted by `DISABLE_TELEGRAM`.

    `MessageSenderWrapper.start` swallows the exception and returns None, so
    this alert is the only notice the failure ever gets -- the wrapper above it
    never sees a raise. It used to go through `send_message_to_channel`
    addressed to the admin chat, which returns early on `DISABLE_TELEGRAM`, a
    deployed secret. Operator ruling 2026-09-06: errors in production must
    surface. Point the call back at `send_message_to_channel` and this goes red.

    The flag itself is not exercised here because the sender is stubbed; that
    half lives in
    `tests/unit/notification_destination/telegram_notification_test.py::test_disable_telegram_does_not_silence_admin_alerts`.
    """
    sent = []

    async def _record(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(
        'src.job.message_sender_wrapper.send_message_to_admin', _record
    )
    monkeypatch.setenv('DISABLE_TELEGRAM', 'true')

    sender = ExplodingSender(runtime_mode=RuntimeMode.from_test_mode(True))
    result = await sender.start()

    assert result is None
    assert len(sent) == 1
    assert sent[0]['market_data_type'] is MarketDataType.CRYPTO
    assert sent[0]['runtime_mode'] == RuntimeMode.from_test_mode(True)
    # The body is MarkdownV2-escaped, so `format\_message` is what actually
    # lands; assert on the parts escaping leaves alone.
    assert 'RuntimeError' in sent[0]['message']
    assert 'exploded' in sent[0]['message']


@pytest.mark.asyncio
async def test_alert_delivery_failure_does_not_replace_the_reported_failure(
    monkeypatch,
):
    """A dead Telegram must not turn a swallowed job failure into a raise.

    `start` returns None on failure and its caller relies on that. Before the
    alert moved onto `send_message_to_admin` the sender swallowed its own
    delivery errors; this sender re-raises them, so the guard lives here.
    """
    monkeypatch.setattr(
        'src.job.message_sender_wrapper.send_message_to_admin',
        AsyncMock(side_effect=RuntimeError('telegram is down')),
    )

    sender = ExplodingSender()
    result = await sender.start()

    assert result is None
