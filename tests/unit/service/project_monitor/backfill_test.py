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
from src.service.project_monitor import logs as log_plane
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


def _rebase_mint_log(block):
    """A real `Transfer(0x0 -> staking)` log, not a stand-in.

    `step_epoch_boundaries` decodes what `fetch_window` returns through
    `build_mint_rows` and `rebase_boundaries`, and both stay REAL in the tests
    below -- only the fetch is faked. A pre-decoded row fixture would leave the
    step's own attribution path (mint class `rebase` is what makes a log a
    boundary) unexercised.
    """
    def topic_address(address):
        return '0x' + '0' * 24 + address.lower().removeprefix('0x')

    return {
        'topics': [
            log_plane.TRANSFER.topic0,
            topic_address(log_plane.ZERO_ADDRESS),
            topic_address(NETNET.address('staking')),
        ],
        'data': '0x' + f'{7 * block:064x}',
        'blockNumber': hex(block),
        'transactionHash': f'0xrebase{block:x}',
        'logIndex': '0x0',
    }


def test_step_two_segments_its_sweep_and_resumes_from_its_own_watermark(
    repository, monkeypatch,
):
    """Review finding 1. Step 2 is the same shape of job as step 3 -- one log
    query over the whole chain, hours long at the pace the public endpoint
    tolerates -- and it runs FIRST, so an interruption in it discarded
    everything and restarted at the launch block. The operator's ruling says
    "the sweep... resumes instead of restarting" and scopes it to `backfill.py`,
    not to a step.

    Deliberately NOT passing `segment_blocks`: the production default is what
    the operator's runs will use, so the test drives it. That also makes this
    substantively red on the parent commit, where the whole range is one call,
    rather than red merely because a keyword argument did not exist yet.
    """
    repository.set_project_value(PROJECT, 'launch_block', '0')
    head = 1_200_000
    seen = []

    def fake_fetch(fail_at_start):
        async def fetch_window(client, query, from_block, to_block):
            seen.append((from_block, to_block))
            if from_block == fail_at_start:
                raise RuntimeError('the endpoint stopped serving mid-sweep')
            return [_rebase_mint_log(from_block + 1)], []
        return fetch_window

    monkeypatch.setattr(log_plane, 'fetch_window', fake_fetch(500_000))
    with pytest.raises(RuntimeError):
        asyncio.run(backfill.step_epoch_boundaries(repository, None, head))

    assert seen == [(0, 499_999), (500_000, 999_999)]
    assert repository.get_project_value(
        PROJECT, backfill.EPOCH_BOUNDARY_WATERMARK_KEY
    ) == '499999'
    # Segment 1's boundary survived segment 2's failure. Before this change the
    # single end-of-step commit meant it did not.
    assert _boundaries(repository) == [1]

    seen.clear()
    monkeypatch.setattr(log_plane, 'fetch_window', fake_fetch(None))
    result = asyncio.run(backfill.step_epoch_boundaries(repository, None, head))

    # Resumed at the watermark: segment 1 is not re-fetched.
    assert seen == [(500_000, 999_999), (1_000_000, 1_200_000)]
    assert _boundaries(repository) == [1, 500_001, 1_000_001]
    assert 'over 2 segments' in result, result
    assert repository.get_project_value(
        PROJECT, backfill.EPOCH_BOUNDARY_WATERMARK_KEY
    ) == '1200000'


def test_a_step_two_segment_that_fails_part_way_writes_none_of_its_own_rows(
    repository, monkeypatch,
):
    """AC7 inside the segment, for step 2 as for step 3. The failure is placed
    BETWEEN two boundary writes of the same segment, because that is the only
    way a half-written segment can arise -- a failure in `fetch_window`, which
    the resume test uses, happens before any write and cannot tell a rolled-back
    segment from one that never started.
    """
    repository.set_project_value(PROJECT, 'launch_block', '0')

    async def fetch_window(client, query, from_block, to_block):
        # Two rebase mints per segment, so a segment has an interior.
        return [
            _rebase_mint_log(from_block + 1),
            _rebase_mint_log(from_block + 2),
        ], []

    real_upsert = repository.upsert_epoch_boundary

    def upsert_failing_inside_the_second_segment(project, first_block, tx, **kwargs):
        if first_block == 500_002:
            raise RuntimeError('the store rejected the write')
        return real_upsert(project, first_block, tx, **kwargs)

    monkeypatch.setattr(log_plane, 'fetch_window', fetch_window)
    monkeypatch.setattr(
        repository, 'upsert_epoch_boundary',
        upsert_failing_inside_the_second_segment,
    )
    with pytest.raises(RuntimeError):
        asyncio.run(backfill.step_epoch_boundaries(repository, None, 999_999))

    # 500,001 was written before the failure and must be gone with it; segment
    # 1's two boundaries must not be, or the rollback has swallowed committed
    # work and reintroduced the loss the watermark exists to prevent.
    assert _boundaries(repository) == [1, 2]
    assert repository.get_project_value(
        PROJECT, backfill.EPOCH_BOUNDARY_WATERMARK_KEY
    ) == '499999'


def test_step_two_and_step_three_do_not_share_a_watermark(repository, monkeypatch):
    """The two sweeps cover different ranges -- step 2 runs to `head`, step 3
    stops at the live cursor -- and `--steps` lets an operator run either alone.
    One shared key would let step 2's progress carry step 3 past blocks whose
    flows and events were never read, and the miss would be silent: step 3 would
    report success having skipped them.
    """
    repository.set_project_value(PROJECT, 'launch_block', '0')

    async def fetch_window(client, query, from_block, to_block):
        return [_rebase_mint_log(from_block + 1)], []

    monkeypatch.setattr(log_plane, 'fetch_window', fetch_window)
    asyncio.run(backfill.step_epoch_boundaries(repository, None, 600_000))

    assert repository.get_project_value(
        PROJECT, backfill.EPOCH_BOUNDARY_WATERMARK_KEY
    ) == '600000'
    # Step 3 has read nothing, so it must have no watermark of its own.
    assert repository.get_project_value(
        PROJECT, backfill.LOG_HISTORY_WATERMARK_KEY
    ) is None

    seen = []

    async def read_log_window(
        client, project, project_name, from_block, to_block, *,
        net_decimals, usdg_decimals,
    ):
        seen.append((from_block, to_block))
        return _segment_window(project_name, from_block, to_block)

    monkeypatch.setattr(recorder, 'read_log_window', read_log_window)
    asyncio.run(backfill.step_log_history(repository, None, 600_000))

    # Step 3 starts at the launch block, NOT at step 2's watermark.
    assert seen[0][0] == 0, seen


def _boundaries(repository):
    return [
        int(row['first_block'])
        for row in repository.fetch_all(
            'SELECT first_block FROM epoch_boundary WHERE project = %s '
            'ORDER BY first_block',
            (PROJECT,),
        )
    ]


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
    the same guarantee `(tx_hash, log_index)` gives mint/flow/event.

    The watermark now short-circuits a PLAIN re-run, so it is rewound between
    the two runs to keep this test pointed at the `ON CONFLICT DO NOTHING`
    guarantee rather than at the watermark. That guarantee is still the
    backstop: the watermark is committed AFTER the inserts, so a crash between
    the two re-fetches the segment, and adjacent segments can overlap whenever
    an operator resets the watermark by hand.
    """
    repository.set_project_value(PROJECT, 'launch_block', '100')

    async def fake_read_log_window(
        client, project, project_name, from_block, to_block, *,
        net_decimals, usdg_decimals,
    ):
        return _fake_window(project_name, '0x64', '0x3e7')

    monkeypatch.setattr(recorder, 'read_log_window', fake_read_log_window)
    first = asyncio.run(backfill.step_log_history(repository, None, 999))

    # A plain re-run does no work at all now: the watermark says the range is
    # already covered.
    untouched = asyncio.run(backfill.step_log_history(repository, None, 999))
    assert 'over 0 segments' in untouched, untouched

    repository.set_project_value(PROJECT, backfill.LOG_HISTORY_WATERMARK_KEY, '99')
    second = asyncio.run(backfill.step_log_history(repository, None, 999))

    assert '+2 raw log responses' in first, first
    assert '+0 raw log responses' in second, second
    assert repository.fetch_all(
        'SELECT count(*) AS n FROM backfill_log_raw_response WHERE project = %s',
        (PROJECT,),
    )[0]['n'] == 2


def _segment_window(project_name, from_block, to_block):
    """A one-row-per-table window whose rows are keyed to ITS OWN segment, so a
    segment's contribution to the store is distinguishable from another's."""
    window = _fake_window(project_name, hex(from_block), hex(to_block))
    for key in ('mints', 'flows', 'events'):
        for row in window[key]:
            row['block'] = from_block
            row['tx_hash'] = f'0x{from_block:x}'
    return window


def test_step_log_history_commits_a_watermark_per_segment_and_resumes_from_it(
    repository, monkeypatch,
):
    """The 2026-08-30 loss, and the operator ruling of 2026-08-31. That run
    swept ~50M blocks as ONE unit, hit a Cloudflare interstitial after ~36
    minutes, and committed nothing -- correct under AC7 as it stood, and a
    total loss of the work. At the request pace the public endpoint tolerates a
    full sweep runs for hours, so the run cannot be the unit of atomicity.

    Two halves, both asserted: a completed segment survives a LATER segment's
    failure, and the re-run starts at the watermark rather than at the launch
    block. The second half is what makes this more than a commit-more-often
    change -- resuming is the point, and a version that committed per segment
    but still recomputed `from_block` from `launch_block` would pass the first
    half alone.
    """
    repository.set_project_value(PROJECT, 'launch_block', '0')
    seen = []

    async def failing_read(
        client, project, project_name, from_block, to_block, *,
        net_decimals, usdg_decimals,
    ):
        seen.append((from_block, to_block))
        if from_block == 1000:
            raise RuntimeError('the endpoint stopped serving mid-sweep')
        return _segment_window(project_name, from_block, to_block)

    monkeypatch.setattr(recorder, 'read_log_window', failing_read)
    with pytest.raises(RuntimeError):
        asyncio.run(
            backfill.step_log_history(repository, None, 2999, segment_blocks=1000)
        )

    assert seen == [(0, 999), (1000, 1999)]
    assert repository.get_project_value(
        PROJECT, backfill.LOG_HISTORY_WATERMARK_KEY
    ) == '999'
    # Segment 1's rows survived segment 2's failure -- the whole point.
    assert [r['block'] for r in repository.fetch_all(
        'SELECT block FROM mint WHERE project = %s ORDER BY block', (PROJECT,)
    )] == [0]
    # ...and the sweep is not marked finished, because it is not.
    assert repository.get_project_value(PROJECT, 'cursor_origin') is None

    seen.clear()

    async def working_read(
        client, project, project_name, from_block, to_block, *,
        net_decimals, usdg_decimals,
    ):
        seen.append((from_block, to_block))
        return _segment_window(project_name, from_block, to_block)

    monkeypatch.setattr(recorder, 'read_log_window', working_read)
    result = asyncio.run(
        backfill.step_log_history(repository, None, 2999, segment_blocks=1000)
    )

    # Resumed at the watermark. Segment 1 is NOT re-fetched: on a real sweep
    # that is the difference between minutes and hours of repeated work.
    assert seen == [(1000, 1999), (2000, 2999)]
    assert 'over 2 segments' in result, result
    assert [r['block'] for r in repository.fetch_all(
        'SELECT block FROM mint WHERE project = %s ORDER BY block', (PROJECT,)
    )] == [0, 1000, 2000]
    assert repository.get_project_value(PROJECT, 'cursor_origin') == '2999'


def test_a_segment_that_fails_part_way_writes_none_of_its_own_rows(
    repository, monkeypatch,
):
    """AC7 inside the segment, which the operator's ruling explicitly left
    binding: the watermark moves the unit of atomicity from the run to the
    segment, it does not weaken what atomicity means.

    The failure is placed BETWEEN two writes of the same segment -- after its
    mints are inserted, before its events are -- because that is the only way
    a half-written segment can arise. A failure in `read_log_window`, which the
    resume test uses, happens before any write and so cannot tell a rolled-back
    segment from one that never started.
    """
    repository.set_project_value(PROJECT, 'launch_block', '0')

    async def read(
        client, project, project_name, from_block, to_block, *,
        net_decimals, usdg_decimals,
    ):
        return _segment_window(project_name, from_block, to_block)

    real_insert_events = repository.insert_events

    def insert_events_failing_on_the_second_segment(rows):
        prepared = list(rows)
        if prepared and prepared[0]['block'] == 1000:
            raise RuntimeError('the store rejected the write')
        return real_insert_events(prepared)

    monkeypatch.setattr(recorder, 'read_log_window', read)
    monkeypatch.setattr(
        repository, 'insert_events', insert_events_failing_on_the_second_segment
    )
    with pytest.raises(RuntimeError):
        asyncio.run(
            backfill.step_log_history(repository, None, 1999, segment_blocks=1000)
        )

    # Segment 2's mints were inserted before the failure and must be gone.
    # Segment 1's must not be: rolling the whole run back would reintroduce
    # exactly the loss the watermark exists to prevent.
    assert [r['block'] for r in repository.fetch_all(
        'SELECT block FROM mint WHERE project = %s ORDER BY block', (PROJECT,)
    )] == [0]
    assert [r['block'] for r in repository.fetch_all(
        'SELECT block FROM flow WHERE project = %s ORDER BY block', (PROJECT,)
    )] == [0]
    # The watermark stayed on segment 1, so the re-run repeats segment 2 and
    # nothing already done.
    assert repository.get_project_value(
        PROJECT, backfill.LOG_HISTORY_WATERMARK_KEY
    ) == '999'


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


def test_backfill_main_splits_the_state_and_log_planes_across_two_endpoints(
    monkeypatch, database_url,
):
    """The routing this job's whole failure history has turned on.

    Steps 1 and 4 read STATE at archive depth, which only the keyed endpoint
    serves. Steps 2 and 3 read LOGS, which the keyed endpoint's FREE tier
    refuses beyond a ten-block range -- measured 2026-08-31 at four spans, all
    HTTP 400 / -32600 "you can make eth_getLogs requests with up to a 10 block
    range". Ten is two orders below `MIN_LOG_WINDOW_BLOCKS`, so a log step
    routed to the keyed endpoint does not degrade, it fails on its first call.
    The 2026-08-30 reroute sent all four steps there and this test asserted
    exactly that; it is inverted here rather than deleted, because the pin is
    the same one and only the correct answer moved.

    Asserts the actual `endpoint.kind` each step's client carries, not merely a
    clean exit -- and the BUDGET alongside it, because `EvmClient(public,
    alchemy_budget())` would carry the right endpoint with compute-unit pacing
    against an endpoint that bills requests, and every endpoint assertion would
    still pass.
    """
    _FakeEndpointClient.instances.clear()
    seen = {}

    async def fake_step_deploy_blocks(repository, client, head):
        seen['step1'] = client
        return 'step 1: ok'

    async def fake_step_epoch_boundaries(repository, client, head):
        seen['step2'] = client
        return 'step 2: ok'

    async def fake_step_log_history(repository, client, head):
        seen['step3'] = client
        return 'step 3: ok'

    async def fake_step_epoch_samples(repository, client, run_id, max_samples):
        seen['step4'] = client
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
    assert {name: c.endpoint.kind for name, c in seen.items()} == {
        'step1': 'alchemy', 'step2': 'public', 'step3': 'public', 'step4': 'alchemy',
    }
    assert {name: c.budget.endpoint_kind for name, c in seen.items()} == {
        'step1': 'alchemy', 'step2': 'public', 'step3': 'public', 'step4': 'alchemy',
    }
    # Two clients, one per endpoint -- not one per step. A fresh `EvmClient`
    # restarts its budget's rolling window at zero, so a per-step client would
    # let a burst above the intended rate through at every step boundary. Steps
    # 1 and 4 must therefore be the SAME object, and so must 2 and 3.
    assert len(_FakeEndpointClient.instances) == 2
    assert seen['step1'] is seen['step4']
    assert seen['step2'] is seen['step3']
    assert seen['step1'] is not seen['step2']


def test_backfill_main_returns_early_when_the_archive_endpoint_is_unconfigured(
    monkeypatch, database_url,
):
    """The archive-endpoint check must still gate the run: since every step now
    depends on that endpoint, a missing key can no longer be a partial-failure
    mode -- it has to stop before touching the database or any client.

    `get_project_monitor_database_url` is monkeypatched to the test database
    too (as the sibling routing test does): without it, a regression that
    removed this gate would make `main()` fall through to the DEFAULT
    (production/dev) database URL and commit a `run` row there before ever
    reaching a client -- dirtying the operator's dev store in exactly the
    case this test exists to catch.
    """
    monkeypatch.setattr(backfill, 'get_archive_endpoint', lambda: None)
    monkeypatch.setattr(
        backfill, 'get_project_monitor_database_url',
        lambda runtime_mode: database_url,
    )
    result = asyncio.run(backfill.main([1, 2, 3, 4]))
    assert result == 1
