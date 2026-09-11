"""`fetch_window` against the two shapes a too-wide refusal arrives in.

The public RPC refuses a wide window inside an HTTP 200 as a JSON-RPC error
(`EvmRpcError`); Alchemy Pay-As-You-Go refuses it with an HTTP 400 the client
does not retry (`EvmTransportError`). Run 4 on 2026-09-11 lost three projects'
health sections to the second shape propagating out of the fetcher unnarrowed.
"""
import asyncio

import pytest
from market_data_library.core.onchain.evm import (
    EvmRateLimitError,
    EvmRpcError,
    EvmTransportError,
)

from src.service.project_monitor import logs

# Verbatim from run 4's traceback, truncated where the client truncates it.
ALCHEMY_REFUSAL = (
    'endpoint refused the request: {"jsonrpc":"2.0","id":2,"error":{"code":-32602,'
    '"message":"Log response size exceeded. You can make eth_getLogs requests with '
    'up to a 5,000 block range and no limit on the response size, or you can req'
)
ALCHEMY_RANGE_CAP = 5_000

QUERY = logs.LogQuery(name='transfer', addresses=['0x' + '11' * 20], topics=[], spec=logs.TRANSFER)


class _RangeCappedClient:
    """Refuses any window wider than the cap with the refusal it is built with.
    A narrower window returns one synthetic log per third block, keyed on the
    block, so a lost or re-issued sub-window changes the set the caller gets
    back. Every third rather than every block: a 5,000-wide window returning
    exactly 5,000 logs would trip `SUSPECTED_RESULT_CAPS` and narrow again.
    Records every issued width."""

    def __init__(self, refusal):
        self.refusal = refusal
        self.widths = []

    async def get_logs(self, log_filter):
        width = log_filter.to_block - log_filter.from_block + 1
        self.widths.append(width)
        if width > ALCHEMY_RANGE_CAP:
            raise self.refusal
        window = [
            {'blockNumber': hex(b)}
            for b in range(log_filter.from_block, log_filter.to_block + 1)
            if b % 3 == 0
        ]
        return window, {'jsonrpc': '2.0', 'id': 1, 'result': window}


def _blocks(fetched):
    return sorted(int(log['blockNumber'], 16) for log in fetched)


def test_alchemy_400_range_refusal_narrows_the_window_instead_of_failing():
    client = _RangeCappedClient(
        EvmTransportError(ALCHEMY_REFUSAL, endpoint_kind='alchemy', status_code=400)
    )

    fetched, raws = asyncio.run(
        logs.fetch_window(client, QUERY, 0, 39_999, max_window=40_000)
    )

    # 40,000 -> 20,000 -> 10,000 refused, then the whole span at 5,000: every
    # block exactly once, and one stored body per accepted call.
    assert client.widths == [40_000, 20_000, 10_000] + [5_000] * 8
    assert _blocks(fetched) == list(range(0, 40_000, 3))
    assert len(raws) == 8


def test_the_public_rpc_range_refusal_still_narrows():
    """The pre-existing shape, pinned so widening the caught classes did not
    narrow the matcher."""
    client = _RangeCappedClient(
        EvmRpcError('query returned more than 10000 results, block range too wide',
                    endpoint_kind='public', code=-32000)
    )

    fetched, _ = asyncio.run(logs.fetch_window(client, QUERY, 0, 9_999, max_window=10_000))

    assert client.widths == [10_000, 5_000, 5_000]
    assert _blocks(fetched) == list(range(0, 10_000, 3))


@pytest.mark.parametrize(
    'refusal',
    [
        pytest.param(
            EvmRateLimitError('rate limited after retries', endpoint_kind='public',
                              status_code=429),
            id='429 after the retry schedule',
        ),
        pytest.param(
            EvmTransportError('transport failure after retries: ServerTimeoutError',
                              endpoint_kind='alchemy', status_code=None),
            id='post-retry timeout, no status',
        ),
        pytest.param(
            EvmTransportError('transport failure after retries: TimeoutError',
                              endpoint_kind='alchemy', status_code=503),
            id='post-retry timeout after a 5xx',
        ),
        pytest.param(
            EvmTransportError('endpoint returned a non-JSON body', endpoint_kind='public',
                              status_code=200),
            id='HTTP 200 with a non-JSON body',
        ),
        pytest.param(
            EvmTransportError('endpoint refused the request: invalid argument 0: hex '
                              'string without 0x prefix', endpoint_kind='alchemy',
                              status_code=400),
            id='unrelated 400',
        ),
    ],
)
def test_a_node_quota_or_request_fault_is_never_read_as_a_width_problem(refusal):
    """Messages verbatim from the library client. The two timeout shapes match
    the `timeout` marker on text, so only the status gate keeps a dead node
    from driving eleven halvings, each paying the full retry schedule, before
    failing anyway."""
    client = _RangeCappedClient(refusal)

    with pytest.raises(type(refusal)):
        asyncio.run(logs.fetch_window(client, QUERY, 0, 9_999, max_window=10_000))

    assert client.widths == [10_000]
