"""The record entrypoint: the failure-alert path and the log-window narrowing."""
import asyncio

import pytest

from market_data_library.core.onchain.evm.errors import EvmRpcError

from src.job.project_monitor import record as record_job
from src.service.project_monitor.logs import (
    MIN_LOG_WINDOW_BLOCKS,
    _is_window_too_wide,
    build_log_queries,
    fetch_window,
)
from src.service.project_monitor.config import NETNET


def test_the_alert_carries_the_exception_class_and_never_its_text(monkeypatch):
    """The operator asked for alerts so failures are not swallowed; the payload
    is deliberately run id + class name only, because an exception's MESSAGE can
    carry the keyed URL and this send is not on the redacting path."""
    sent = []

    async def fake_send(message, market_data_type):
        sent.append((message, market_data_type))

    monkeypatch.setattr(record_job, 'send_message_to_admin', fake_send)
    asyncio.run(record_job._alert(run_id=7, error_class='EvmRateLimitError'))

    assert len(sent) == 1
    message, _ = sent[0]
    assert 'EvmRateLimitError' in message
    assert '7' in message
    # MarkdownV2-escaped, because a class name carrying `.` or `_` is otherwise
    # rejected by Telegram and the alert silently fails.
    assert '\\' in message or '.' not in message


def test_a_failing_alert_never_masks_the_run_outcome(monkeypatch):
    """The run row is written before the alert is attempted; the send is guarded
    so its failure cannot propagate and turn a recorded outcome into a crash."""

    async def exploding_send(message, market_data_type):
        raise RuntimeError('telegram is down')

    monkeypatch.setattr(record_job, 'send_message_to_admin', exploding_send)
    # No exception escapes.
    asyncio.run(record_job._alert(run_id=1, error_class='EvmTransportError'))


def test_window_too_wide_is_recognised_from_the_endpoints_own_wording():
    """The endpoint returns a generic -32000 for a timeout, so the message is
    the only thing that distinguishes "ask for fewer blocks" from a real fault.
    The first string is the one measured on 2026-08-30."""
    assert _is_window_too_wide(
        EvmRpcError('log query timed out', endpoint_kind='public', code=-32000)
    )
    assert _is_window_too_wide(
        EvmRpcError('block range too large', endpoint_kind='public', code=-32600)
    )
    # And it must NOT narrow for an unrelated fault, or a real error becomes an
    # infinite halving loop.
    assert not _is_window_too_wide(
        EvmRpcError('execution reverted', endpoint_kind='public', code=-32000)
    )


class _NarrowingClient:
    """Refuses any window wider than `serves`, the way the endpoint does."""

    def __init__(self, serves: int):
        self.serves = serves
        self.requested = []

    async def get_logs(self, log_filter):
        width = log_filter.to_block - log_filter.from_block + 1
        self.requested.append(width)
        if width > self.serves:
            raise EvmRpcError(
                'log query timed out', endpoint_kind='public', code=-32000
            )
        return [], object()


def test_the_window_narrows_until_the_endpoint_accepts_it():
    query = build_log_queries(NETNET)[0]
    client = _NarrowingClient(serves=200_000)
    logs, _ = asyncio.run(
        fetch_window(client, query, 0, 400_000, max_window=1_500_000)
    )
    assert logs == []
    # It halved from 1.5M until a width the endpoint serves, then covered the
    # range at that width rather than re-widening.
    assert client.requested[0] == 400_001
    assert max(client.requested[-2:]) <= 200_000
    assert sum(w for w in client.requested if w <= 200_000) >= 400_001


def test_narrowing_stops_at_the_floor_and_raises_rather_than_looping():
    """An endpoint refusing even a small window is broken, not busy. Halving
    forever would turn one outage into an unbounded request storm."""
    client = _NarrowingClient(serves=1)
    query = build_log_queries(NETNET)[0]
    with pytest.raises(EvmRpcError):
        asyncio.run(fetch_window(client, query, 0, 10_000, max_window=1_500_000))
    assert min(client.requested) >= MIN_LOG_WINDOW_BLOCKS


def test_a_window_the_endpoint_serves_is_not_narrowed():
    """The narrowing must not fire on correct input, or every backfill pays for
    a refusal that never happened."""
    client = _NarrowingClient(serves=1_500_000)
    query = build_log_queries(NETNET)[0]
    asyncio.run(fetch_window(client, query, 0, 100_000, max_window=1_500_000))
    assert client.requested == [100_001]
