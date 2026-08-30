"""Backfill's search and resume logic.

This is the code that runs the moment the archive endpoint becomes usable --
once, unattended, over the whole chain history. Its two failure modes are silent
ones: a binary search that returns a block a contract did not exist at, and a
resume that re-samples work already done (or worse, skips work it has not).
Neither shows up as an exception, so both are pinned here.
"""
import asyncio

import pytest

from market_data_library.core.onchain.evm.types import Endpoint

from src.job.project_monitor import backfill
from src.service.project_monitor import recorder
from src.service.project_monitor.config import NETNET

PROJECT = NETNET.name


class FakeCodeClient:
    """`eth_getCode` over a chain where each address gained code at its own block.

    `deploy` is either one block for every address, or a mapping of
    lowercased address to its own deploy block. The per-address form exists
    because a fake that ignores its `address` argument makes an
    address/name mismatch uncatchable: every contract would report the same
    deploy block, so step 1 writing contract A's address under contract B's
    name produces byte-identical store content (round-1 test review,
    finding 6 -- the same class as a claimed check that does not exist).
    """

    def __init__(self, deploy):
        self.deploy = deploy
        self.calls = 0
        self.addresses_seen = []

    def _deploy_for(self, address):
        if isinstance(self.deploy, dict):
            return self.deploy.get(address.lower())
        return self.deploy

    async def get_code(self, address, block):
        self.calls += 1
        self.addresses_seen.append(address.lower())
        deploy = self._deploy_for(address)
        has_code = deploy is not None and block >= deploy
        return ('0xdeadbeef' if has_code else '0x'), {'block': block}


@pytest.mark.parametrize(
    'deploy', [0, 1, 2, 999, 500_000, 49_000_000, 49_999_999, 50_000_000]
)
def test_the_deploy_block_search_finds_the_exact_first_block_with_code(deploy):
    """The FIRST block with code, not merely a block that has some. An off-by-one
    here reads a contract one block before it exists, which returns `0x` and is
    then recorded as a failed read rather than as a boundary of the data."""
    client = FakeCodeClient(deploy)
    head = 50_000_000
    found = asyncio.run(backfill.find_deploy_block(client, '0xabc', head))
    assert found == deploy
    # Binary search, not a scan: a linear walk over 50M blocks would be 50M
    # calls and would never finish inside any budget.
    assert client.calls <= 30, client.calls


def test_an_address_with_no_code_at_head_has_no_deploy_block():
    """`0x` at head means "not deployed yet", which is a fact about the chain and
    must not be reported as block 0 -- the read plan gates on this value, so a
    0 would make every historical read for that contract look issuable."""
    client = FakeCodeClient(None)
    assert asyncio.run(backfill.find_deploy_block(client, '0xabc', 50_000_000)) is None
    # One call, then it stops: no search is attempted.
    assert client.calls == 1


def _sample(block, epoch):
    return recorder.SampleResult(
        block=block, block_timestamp=1, epoch_number=epoch,
        endpoint_kind='public', readings=[], raw_responses=[], failed_peripheral=[],
    )


def test_step_four_skips_boundaries_that_already_have_a_backfill_sample(
    repository, monkeypatch
):
    """Resume, the property the docstring claims. A backfill that dies halfway is
    re-run; without the skip, the second run re-reads every boundary it already
    paid for and the job never converges on a long history."""
    run_id = repository.start_run('test')
    for first_block in (100, 200, 300):
        repository.upsert_epoch_boundary(PROJECT, first_block, None)
    # 199 is the sample block for the boundary at 200.
    recorder.commit_sample(
        repository, run_id=run_id, project_name=PROJECT,
        sample=_sample(199, 41), kind=recorder.KIND_BACKFILL,
    )
    repository.commit()

    read = []

    async def fake_read_state(client, project, block, deploy_blocks=None):
        read.append(block)
        return _sample(block, 40)

    monkeypatch.setattr(recorder, 'read_state', fake_read_state)
    result = asyncio.run(
        backfill.step_epoch_samples(repository, None, run_id, max_samples=None)
    )

    assert 199 not in read, 'a boundary already sampled was re-read'
    assert sorted(read) == [99, 299]
    assert '2 backfill samples' in result


def test_step_four_stops_at_max_samples(repository, monkeypatch):
    """The pacing control. Backfill is run by hand against a metered endpoint, so
    an operator must be able to take it a few epochs at a time."""
    run_id = repository.start_run('test')
    for first_block in (100, 200, 300, 400):
        repository.upsert_epoch_boundary(PROJECT, first_block, None)
    repository.commit()

    read = []

    async def fake_read_state(client, project, block, deploy_blocks=None):
        read.append(block)
        return _sample(block, 40)

    monkeypatch.setattr(recorder, 'read_state', fake_read_state)
    asyncio.run(backfill.step_epoch_samples(repository, None, run_id, max_samples=2))
    assert len(read) == 2


def test_a_backfill_boundary_is_stamped_with_the_epoch_it_opens(
    repository, monkeypatch
):
    """P2-1. The sample sits at `first_block - 1`, so it observes the epoch the
    boundary CLOSES; the boundary's number is the one it OPENS, which is one
    more. Writing the sample's own number here disagrees by one with the live
    writer for the same boundary, and `COALESCE` then freezes whichever landed
    first -- a silent off-by-one in the report's epoch keying.
    """
    run_id = repository.start_run('test')
    repository.upsert_epoch_boundary(PROJECT, 500, None)
    repository.commit()

    async def fake_read_state(client, project, block, deploy_blocks=None):
        assert block == 499
        return _sample(block, epoch=41)  # 499 is still in epoch 41

    monkeypatch.setattr(recorder, 'read_state', fake_read_state)
    asyncio.run(backfill.step_epoch_samples(repository, None, run_id, max_samples=None))

    row = repository.fetch_all(
        'SELECT epoch_number FROM epoch_boundary WHERE project = %s AND first_block = 500',
        (PROJECT,),
    )[0]
    assert row['epoch_number'] == 42


def test_step_deploy_blocks_records_every_contract_in_the_read_plan(repository):
    """Step 1's own orchestration, not just `find_deploy_block` in isolation:
    P2-4 named all four backfill steps as untested, and a step that silently
    dropped a contract from the plan -- or wrote the wrong address alongside
    the right name -- would show up nowhere `find_deploy_block`'s own unit
    tests can see, since those never touch the read plan or the repository.

    Every contract is given a DIFFERENT deploy block, derived from its own
    address, so that second claim is actually testable: with one shared block
    for everything, a step that read contract A's address under contract B's
    name would produce identical store content and stay green (round-1 test
    review, finding 6).
    """
    from src.service.project_monitor.read_plan import build_read_plan

    plan = build_read_plan(NETNET)
    address_of = {read.contract: read.to for read in plan}
    staking = NETNET.address('staking')

    # A distinct, reproducible deploy block per address.
    deploy_of_address = {
        address.lower(): 1_000 + 7 * index
        for index, address in enumerate(
            sorted({a.lower() for a in list(address_of.values()) + [staking]})
        )
    }

    client = FakeCodeClient(deploy=deploy_of_address)
    asyncio.run(backfill.step_deploy_blocks(repository, client, head=2_000))

    deploy_blocks = repository.get_deploy_blocks(PROJECT)
    assert set(address_of) <= set(deploy_blocks), (
        f'missing from the store: {set(address_of) - set(deploy_blocks)}'
    )
    # Each name carries the block belonging to ITS OWN address, so a name/
    # address mix-up in step 1 shows up as a wrong number rather than as
    # nothing at all.
    for name, address in address_of.items():
        assert deploy_blocks[name] == deploy_of_address[address.lower()], name

    # The distinct blocks are only load-bearing if they really are distinct.
    assert len(set(deploy_of_address.values())) == len(deploy_of_address)

    # Staking is read separately from the plan loop, for `launch_block`, which
    # the report and every other backfill step key off of -- and it must come
    # from Staking's address, not from whichever contract was read last.
    assert repository.get_project_value(PROJECT, 'launch_block') == str(
        deploy_of_address[staking.lower()]
    )


class _FakeRawResponse:
    """Stands in for `market_data_library`'s `RawResponse` (a frozen dataclass
    with `method`/`params`/`body`/`endpoint_kind`), which the repository layer
    accesses by attribute, not by key -- a dict fixture would pass a test that
    the real client's return value fails against."""

    def __init__(self, from_block_hex, to_block_hex, body):
        self.method = 'eth_getLogs'
        self.params = [{'fromBlock': from_block_hex, 'toBlock': to_block_hex}]
        self.body = body
        self.endpoint_kind = 'public'


def _fake_window(project_name, from_block_hex, to_block_hex, seq_body='0x1'):
    return {
        'mints': [{
            'project': project_name, 'block': 150, 'tx_hash': '0xm',
            'log_index': 0, 'recipient': '0x1', 'amount': 5, 'decimals': 9,
            'class': 'bond',
        }],
        'flows': [{
            'project': project_name, 'block': 150, 'tx_hash': '0xm',
            'log_index': 1, 'direction': 'in', 'counterparty': '0x2',
            'amount': 5, 'decimals': 6, 'label': 'bond', 'rule': 'bond:BondCreated',
        }],
        'events': [{
            'project': project_name, 'block': 150, 'tx_hash': '0xm',
            'log_index': 0, 'contract': 'bondDepository', 'name': 'BondCreated',
            'fields_json': {'marketId': '2'},
        }],
        'raw_responses': [],
        'raw_responses_by_query': [
            ('net_mints', _FakeRawResponse(
                from_block_hex, to_block_hex, {'jsonrpc': '2.0', 'id': 1, 'result': [seq_body]}
            )),
            ('usdg_in', _FakeRawResponse(
                from_block_hex, to_block_hex, {'jsonrpc': '2.0', 'id': 2, 'result': []}
            )),
        ],
        'boundaries': [],
    }


def test_step_log_history_persists_mints_flows_and_events_and_advances_the_origin(
    repository, monkeypatch,
):
    """Step 3's own orchestration: it must actually reach the repository with
    what `read_log_window` decodes, and it must record where it stopped so
    step 4 and the live cursor both start from the right place."""
    repository.set_project_value(PROJECT, 'launch_block', '100')

    captured = {}

    async def fake_read_log_window(
        client, project, project_name, from_block, to_block, *,
        net_decimals, usdg_decimals,
    ):
        captured['from_block'] = from_block
        captured['to_block'] = to_block
        return _fake_window(project_name, hex(from_block), hex(to_block))

    monkeypatch.setattr(recorder, 'read_log_window', fake_read_log_window)
    result = asyncio.run(backfill.step_log_history(repository, None, 999))

    assert captured['from_block'] == 100
    assert captured['to_block'] == 999
    assert '+1 mints' in result and '+1 flows' in result and '+1 events' in result

    assert repository.fetch_all(
        'SELECT count(*) AS n FROM mint WHERE project = %s', (PROJECT,)
    )[0]['n'] == 1
    assert repository.fetch_all(
        'SELECT count(*) AS n FROM flow WHERE project = %s', (PROJECT,)
    )[0]['n'] == 1
    assert repository.fetch_all(
        'SELECT count(*) AS n FROM event WHERE project = %s', (PROJECT,)
    )[0]['n'] == 1
    assert repository.get_project_value(PROJECT, 'cursor_origin') == '999'


def test_step_log_history_stores_the_raw_log_responses_block_keyed(
    repository, monkeypatch,
):
    """R2 on the backfill path (operator ruling, test-plan.md "Escalation:
    backfill step 3 discards its raw responses"): the raw `eth_getLogs` bodies
    step 3 fetches must land somewhere re-derivable, not be discarded because
    there is no sample to key them under.

    Asserts real content -- method, decoded block bounds, the actual body,
    endpoint_kind -- not just a row count, which a table holding the wrong
    query's raw under the right count would still satisfy."""
    repository.set_project_value(PROJECT, 'launch_block', '100')

    async def fake_read_log_window(
        client, project, project_name, from_block, to_block, *,
        net_decimals, usdg_decimals,
    ):
        return _fake_window(project_name, '0x64', '0x3e7', seq_body='0xdeadbeef')

    monkeypatch.setattr(recorder, 'read_log_window', fake_read_log_window)
    result = asyncio.run(backfill.step_log_history(repository, None, 999))

    assert '+2 raw log responses' in result, result

    rows = repository.fetch_all(
        'SELECT query_name, from_block, to_block, method, params_json, '
        'body_json, endpoint_kind FROM backfill_log_raw_response '
        'WHERE project = %s ORDER BY query_name',
        (PROJECT,),
    )
    assert [r['query_name'] for r in rows] == ['net_mints', 'usdg_in']

    net_mints_row = rows[0]
    assert net_mints_row['from_block'] == 100
    assert net_mints_row['to_block'] == 999
    assert net_mints_row['method'] == 'eth_getLogs'
    assert net_mints_row['endpoint_kind'] == 'public'
    # The body is what a re-derivation actually needs back -- not just present,
    # but the SAME result the query returned.
    assert net_mints_row['body_json']['result'] == ['0xdeadbeef']
    assert net_mints_row['params_json'][0]['fromBlock'] == '0x64'


def test_step_log_history_keys_narrowed_sub_windows_by_their_own_span(
    repository, monkeypatch,
):
    """`fetch_window` narrows on a too-wide-window RPC error, so ONE query can
    issue several `eth_getLogs` calls over disjoint sub-spans inside a single
    step-3 run. The stored span must come from each call's own
    `raw.params[0]['fromBlock']`/`['toBlock']`, not from the caller's outer
    `[from_block, to_block]` -- the two previous tests never separate them
    (both raws there share one span that also equals the outer window), so a
    version that stored the outer range for every row would still pass those."""
    repository.set_project_value(PROJECT, 'launch_block', '100')

    async def fake_read_log_window(
        client, project, project_name, from_block, to_block, *,
        net_decimals, usdg_decimals,
    ):
        window = _fake_window(project_name, '0x64', '0x3e7')
        window['raw_responses_by_query'] = [
            ('net_mints', _FakeRawResponse(
                '0x64', '0x1f3', {'jsonrpc': '2.0', 'id': 1, 'result': []}
            )),
            ('net_mints', _FakeRawResponse(
                '0x1f4', '0x3e7', {'jsonrpc': '2.0', 'id': 2, 'result': []}
            )),
        ]
        return window

    monkeypatch.setattr(recorder, 'read_log_window', fake_read_log_window)
    result = asyncio.run(backfill.step_log_history(repository, None, 999))

    assert '+2 raw log responses' in result, result

    rows = repository.fetch_all(
        'SELECT from_block, to_block FROM backfill_log_raw_response '
        'WHERE project = %s AND query_name = %s ORDER BY from_block',
        (PROJECT, 'net_mints'),
    )
    assert [(r['from_block'], r['to_block']) for r in rows] == [
        (100, 499), (500, 999),
    ]


def test_step_log_history_re_run_does_not_duplicate_raw_responses(
    repository, monkeypatch,
):
    """A backfill that dies halfway is re-run, not unwound (module docstring).
    Re-issuing the same step-3 calls must not double the raw-response rows --
    the same guarantee `(tx_hash, log_index)` gives mint/flow/event."""
    repository.set_project_value(PROJECT, 'launch_block', '100')

    async def fake_read_log_window(
        client, project, project_name, from_block, to_block, *,
        net_decimals, usdg_decimals,
    ):
        return _fake_window(project_name, '0x64', '0x3e7')

    monkeypatch.setattr(recorder, 'read_log_window', fake_read_log_window)
    first = asyncio.run(backfill.step_log_history(repository, None, 999))
    second = asyncio.run(backfill.step_log_history(repository, None, 999))

    assert '+2 raw log responses' in first, first
    assert '+0 raw log responses' in second, second
    assert repository.fetch_all(
        'SELECT count(*) AS n FROM backfill_log_raw_response WHERE project = %s',
        (PROJECT,),
    )[0]['n'] == 2


class _FakeEndpointClient:
    """Records the `Endpoint` and budget `main()` actually constructed it
    with, and how many times construction happened -- the two things a
    routing regression would get wrong with no other visible symptom until a
    multi-hour production run trips over it (see this task's run log)."""

    instances = []

    def __init__(self, endpoint, budget):
        self.endpoint = endpoint
        self.budget = budget
        _FakeEndpointClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def block_number(self):
        return 12_345, {'result': '0x3039'}


def test_backfill_main_routes_every_step_to_the_archive_endpoint(
    monkeypatch, database_url,
):
    """The public RPC's bot protection killed a real full-history sweep after
    ~36 minutes (Cloudflare interstitial, HTTP 403, 2026-08-30 run log); the
    keyed Alchemy endpoint was verified the same night to have full archive
    depth. All four backfill steps must build their client on THAT endpoint
    now, not just steps 1 and 4 as before.

    Asserts the actual `endpoint.kind` each step's client was constructed
    with, not merely that the run completed -- a stub client with no real
    `.endpoint` attribute, or one still defaulting to 'public', would pass a
    weaker test that only checked for a clean exit code.
    """
    _FakeEndpointClient.instances.clear()
    seen_kinds = {}

    async def fake_step_deploy_blocks(repository, client, head):
        seen_kinds['step1'] = client.endpoint.kind
        return 'step 1: ok'

    async def fake_step_epoch_boundaries(repository, client, head):
        seen_kinds['step2'] = client.endpoint.kind
        return 'step 2: ok'

    async def fake_step_log_history(repository, client, head):
        seen_kinds['step3'] = client.endpoint.kind
        return 'step 3: ok'

    async def fake_step_epoch_samples(repository, client, run_id, max_samples):
        seen_kinds['step4'] = client.endpoint.kind
        return 'step 4: ok'

    monkeypatch.setattr(backfill, 'step_deploy_blocks', fake_step_deploy_blocks)
    monkeypatch.setattr(backfill, 'step_epoch_boundaries', fake_step_epoch_boundaries)
    monkeypatch.setattr(backfill, 'step_log_history', fake_step_log_history)
    monkeypatch.setattr(backfill, 'step_epoch_samples', fake_step_epoch_samples)
    monkeypatch.setattr(backfill, 'EvmClient', _FakeEndpointClient)
    monkeypatch.setattr(
        backfill, 'get_archive_endpoint',
        # A fake, non-secret URL: never the real keyed endpoint, per this
        # task's constraint against reading or echoing it.
        lambda: Endpoint(kind='alchemy', url='https://example.invalid/k'),
    )
    monkeypatch.setattr(
        backfill, 'get_project_monitor_database_url',
        lambda runtime_mode: database_url,
    )

    result = asyncio.run(backfill.main([1, 2, 3, 4]))

    assert result == 0
    assert seen_kinds == {
        'step1': 'alchemy', 'step2': 'alchemy', 'step3': 'alchemy', 'step4': 'alchemy',
    }
    # One shared client for the whole run, not a fresh one per step: a fresh
    # `alchemy_budget()` per step would restart its rolling rate-limit window
    # at zero at each step boundary, which is exactly where a burst above the
    # intended CU/s rate would slip through unaccounted for.
    assert len(_FakeEndpointClient.instances) == 1


def test_backfill_main_returns_early_when_the_archive_endpoint_is_unconfigured(
    monkeypatch,
):
    """The archive-endpoint check must still gate the run: since every step now
    depends on that endpoint, a missing key can no longer be a partial-failure
    mode -- it has to stop before touching the database or any client."""
    monkeypatch.setattr(backfill, 'get_archive_endpoint', lambda: None)
    result = asyncio.run(backfill.main([1, 2, 3, 4]))
    assert result == 1
