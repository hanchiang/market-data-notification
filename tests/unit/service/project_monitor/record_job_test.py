"""The record entrypoint: the failure-alert path and the log-window narrowing."""
import asyncio

import pytest

from market_data_library.core.onchain.evm.errors import EvmRpcError, EvmTransportError
from market_data_library.core.onchain.evm.types import Endpoint

from src.job.project_monitor import record as record_job
from src.runtime.runtime_mode import RuntimeMode
from src.service.project_monitor import recorder
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
    asyncio.run(
        record_job._alert(
            run_id=7, error_class='EvmRateLimitError', endpoint_kind='alchemy'
        )
    )

    assert len(sent) == 1
    message, _ = sent[0]
    assert 'EvmRateLimitError' in message
    assert '7' in message
    # The endpoint kind is in the payload because it is what tells the operator
    # whether to look at the metered account or the public node. It is the
    # 'alchemy'/'public' label, never the URL.
    assert 'alchemy' in message
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


def _fake_sample(block, epoch=132):
    return recorder.SampleResult(
        block=block,
        block_timestamp=1,
        epoch_number=epoch,
        endpoint_kind='public',
        readings=[],
        raw_responses=[],
        failed_peripheral=[],
    )


def test_a_core_batch_failure_on_the_archive_falls_back_to_the_public_endpoint(
    monkeypatch,
):
    """P1-1. The failover exists for a read that fails PART WAY through the
    plan, which is what an archive outage looks like; a failure in the head pin
    is the rarer case. The recorder wraps an exhausted core batch in
    `CoreReadFailedError`, which is not an `EvmClientError` -- so a failover
    clause catching only the latter falls back for the head pin and for nothing
    after it, and every hourly sample is lost for the duration of the outage.

    Asserting on the SECOND call's endpoint, not merely that no exception
    escaped: 'the sample succeeded' would also be true if the archive attempt
    had never been made.
    """
    calls = []

    async def fake_read_state_on(endpoint, budget, repository):
        calls.append(endpoint.kind)
        if endpoint.kind == 'alchemy':
            raise recorder.CoreReadFailedError('core batch failed: EvmRateLimitError')
        return _fake_sample(500), 500

    monkeypatch.setattr(record_job, '_read_state_on', fake_read_state_on)
    monkeypatch.setattr(
        record_job, 'get_archive_endpoint',
        lambda: Endpoint(kind='alchemy', url='https://example.invalid/k'),
    )
    monkeypatch.setattr(
        record_job.recorder, 'resolve_window_start', lambda *a, **k: (400, None)
    )

    async def fake_window(*args, **kwargs):
        return {'raw_responses': [], 'mints': [], 'flows': [], 'events': [],
                'boundaries': [], 'queries': []}

    monkeypatch.setattr(record_job.recorder, 'read_log_window', fake_window)
    monkeypatch.setattr(
        record_job.recorder, 'commit_sample', lambda *a, **k: 1
    )

    progress = {}
    result = asyncio.run(
        record_job.run_sample(
            None, 1, runtime_mode=RuntimeMode.from_test_mode(False), progress=progress
        )
    )

    assert calls == ['alchemy', 'public'], calls
    assert any('falling back to the public endpoint' in n for n in result['notes'])
    # The alert must name the endpoint the run ended up on, not the one it
    # started on.
    assert progress['endpoint_kind'] == 'public'


def test_a_programming_error_is_not_treated_as_an_endpoint_failure(monkeypatch):
    """The counterpart to the fix above: widening the failover catch to bare
    `Exception` would route a bug into a fallback retry and then report it as an
    endpoint problem. A `TypeError` must propagate untouched."""
    calls = []

    async def fake_read_state_on(endpoint, budget, repository):
        calls.append(endpoint.kind)
        raise TypeError('a bug, not an outage')

    monkeypatch.setattr(record_job, '_read_state_on', fake_read_state_on)
    monkeypatch.setattr(
        record_job, 'get_archive_endpoint',
        lambda: Endpoint(kind='alchemy', url='https://example.invalid/k'),
    )

    with pytest.raises(TypeError):
        asyncio.run(
            record_job.run_sample(
                None, 1, runtime_mode=RuntimeMode.from_test_mode(False)
            )
        )
    assert calls == ['alchemy'], 'no fallback attempt for a programming error'


class _FakeLogClient:
    """Records the `Endpoint` and budget the log-window client was actually
    constructed with. `logs.py`'s module docstring now states a universal --
    the live job's log client never touches the metered endpoint -- and a
    universal needs a check at the boundary it names or it only holds until
    the next edit silently moves it: metered billing on an hourly job, with no
    symptom until an invoice arrives."""

    instances = []

    def __init__(self, endpoint, budget):
        self.endpoint = endpoint
        self.budget = budget
        _FakeLogClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


def test_run_sample_builds_the_log_window_client_on_the_public_endpoint(
    monkeypatch,
):
    """P1-1 (review of 49a827d, which routed BACKFILL's log plane to the
    archive endpoint). The live job's log-window client must not follow it.
    Runs the archive-succeeds state path (`_read_state_on` returns as if
    'alchemy' served the state plan) and still asserts the LOG client landed
    on 'public' with `public_rpc_budget()` -- the state plane's endpoint
    choice must never leak into the log plane's, which is exactly the
    regression a shared-variable refactor could introduce with no other
    visible symptom.
    """
    _FakeLogClient.instances.clear()

    async def fake_read_state_on(endpoint, budget, repository):
        return _fake_sample(500), 500

    monkeypatch.setattr(record_job, '_read_state_on', fake_read_state_on)
    monkeypatch.setattr(
        record_job, 'get_archive_endpoint',
        lambda: Endpoint(kind='alchemy', url='https://example.invalid/k'),
    )
    monkeypatch.setattr(
        record_job.recorder, 'resolve_window_start', lambda *a, **k: (400, None)
    )
    monkeypatch.setattr(record_job, 'EvmClient', _FakeLogClient)

    async def fake_window(client, *args, **kwargs):
        return {'raw_responses': [], 'mints': [], 'flows': [], 'events': [],
                'boundaries': [], 'queries': []}

    monkeypatch.setattr(record_job.recorder, 'read_log_window', fake_window)
    monkeypatch.setattr(record_job.recorder, 'commit_sample', lambda *a, **k: 1)

    asyncio.run(
        record_job.run_sample(
            None, 1, runtime_mode=RuntimeMode.from_test_mode(False), progress={}
        )
    )

    # `record_job.EvmClient` was patched globally, so if the state plane had
    # ALSO gone through it (rather than through the monkeypatched
    # `_read_state_on`), it would show up here too -- this is the one and
    # only client the log plane built.
    assert len(_FakeLogClient.instances) == 1
    log_client = _FakeLogClient.instances[0]
    assert log_client.endpoint.kind == 'public'
    assert log_client.budget.endpoint_kind == 'public'


def _patch_entrypoint(monkeypatch, repository, database_url):
    """Point `main()` at the test database and silence the alert send."""
    monkeypatch.setattr(
        record_job, 'get_project_monitor_database_url', lambda mode: database_url
    )
    monkeypatch.setattr(record_job, 'init_telegram_bots', lambda: None)
    sent = []

    async def fake_send(message, market_data_type):
        sent.append(message)

    monkeypatch.setattr(record_job, 'send_message_to_admin', fake_send)
    return sent


def test_a_rejecting_endpoint_exits_non_zero_and_writes_no_sample(
    repository, database_url, monkeypatch
):
    """AC2, end to end through `main()`: an endpoint that rejects the request
    makes the run fail loudly and leave NO record -- not a record with absent
    fields. Driven through the entrypoint rather than the module, because the
    wiring is the part that was untested: exception -> rollback -> run row ->
    non-zero exit. Every one of those is a separate chance to lose the property.
    """
    sent = _patch_entrypoint(monkeypatch, repository, database_url)

    async def rejecting(*args, **kwargs):
        raise EvmTransportError(
            'endpoint refused the request', endpoint_kind='public', status_code=403
        )

    async def fake_manifest(repo):
        return 'manifest skipped'

    monkeypatch.setattr(record_job, 'run_sample', rejecting)
    monkeypatch.setattr(record_job, 'run_manifest_snapshot', fake_manifest)

    exit_code = asyncio.run(record_job.main())

    assert exit_code == 1, 'a failed sample must exit non-zero'
    assert repository.fetch_all('SELECT id FROM sample') == []
    assert repository.fetch_all('SELECT id FROM reading') == []
    run = repository.fetch_all(
        'SELECT outcome, error_class, notes FROM run ORDER BY id DESC LIMIT 1'
    )[0]
    assert run['outcome'] == 'failed'
    assert run['error_class'] == 'EvmTransportError'
    # The run row names the failure even though no sample exists -- the record
    # of the attempt is what tells the operator the hour was not simply skipped.
    assert 'EvmTransportError' in run['notes']
    assert len(sent) == 1 and 'EvmTransportError' in sent[0]


def test_a_second_run_while_one_holds_the_lock_is_skipped_not_failed(
    repository, second_repository, database_url, monkeypatch
):
    """AC7's neighbour: an overlapping run must record `skipped` and exit ZERO.
    Exiting non-zero would page the operator every hour that a long backfill is
    running, which is the reliable way to make the alert channel ignored.
    """
    sent = _patch_entrypoint(monkeypatch, repository, database_url)
    ran = []

    async def should_not_run(*args, **kwargs):
        ran.append(1)
        return {'sample_id': 1, 'block': 1, 'epoch': 1, 'notes': []}

    monkeypatch.setattr(record_job, 'run_sample', should_not_run)

    with second_repository.advisory_lock():
        exit_code = asyncio.run(record_job.main())

    assert exit_code == 0
    assert ran == [], 'the guarded body must not run while the lock is held'
    run = repository.fetch_all(
        'SELECT outcome FROM run ORDER BY id DESC LIMIT 1'
    )[0]
    assert run['outcome'] == 'skipped'
    assert sent == [], 'a skipped run is not a failure and must not alert'


def test_a_failed_peripheral_read_is_named_on_the_run_row(
    repository, database_url, monkeypatch
):
    """AC7's second half, the clause nothing exercised: "the run row names it".

    Round-1 test review, finding 5: the only run-row assertion for this lived
    in `repository_test.py`, where the TEST composed the
    `'failed peripheral reads: ...'` string and handed it to `finish_run` --
    so it could not fail if `record.py` stopped producing it. The wiring
    (`run_sample` appending the note -> `finish_run` writing it) is exactly
    where P1-1 escaped three review rounds, so it is asserted here through
    `main()` against the real run row.
    """
    sent = _patch_entrypoint(monkeypatch, repository, database_url)

    sample = _fake_sample(700)
    sample.failed_peripheral = ['Mark.NVDA.latestRoundData', 'Morpho.market']

    async def fake_read_state_on(endpoint, budget, repo):
        return sample, 700

    monkeypatch.setattr(record_job, '_read_state_on', fake_read_state_on)
    monkeypatch.setattr(record_job, 'get_archive_endpoint', lambda: None)
    monkeypatch.setattr(
        record_job.recorder, 'resolve_window_start', lambda *a, **k: (600, None)
    )

    async def fake_window(*args, **kwargs):
        return {'raw_responses': [], 'mints': [], 'flows': [], 'events': [],
                'boundaries': [], 'queries': []}

    monkeypatch.setattr(record_job.recorder, 'read_log_window', fake_window)
    monkeypatch.setattr(record_job.recorder, 'commit_sample', lambda *a, **k: 1)

    async def fake_manifest(repo):
        return 'manifest skipped'

    monkeypatch.setattr(record_job, 'run_manifest_snapshot', fake_manifest)

    exit_code = asyncio.run(record_job.main())

    assert exit_code == 0, 'a peripheral failure is not a failed run'
    run = repository.fetch_all(
        'SELECT outcome, notes FROM run ORDER BY id DESC LIMIT 1'
    )[0]
    assert run['outcome'] == 'ok'
    # Both names, not merely the prefix: a note that says some reads failed
    # without saying which sends the operator looking through fifty readings.
    assert 'failed peripheral reads' in run['notes']
    assert 'Mark.NVDA.latestRoundData' in run['notes']
    assert 'Morpho.market' in run['notes']
    assert sent == [], 'a run that committed its sample must not alert'
