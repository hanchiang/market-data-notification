"""Backfill's search and resume logic.

This is the code that runs the moment the archive endpoint becomes usable --
once, unattended, over the whole chain history. Its two failure modes are silent
ones: a binary search that returns a block a contract did not exist at, and a
resume that re-samples work already done (or worse, skips work it has not).
Neither shows up as an exception, so both are pinned here.
"""
import asyncio

import pytest

from src.job.project_monitor import backfill
from src.service.project_monitor import recorder
from src.service.project_monitor.config import NETNET

PROJECT = NETNET.name


class FakeCodeClient:
    """`eth_getCode` over a chain where `address` gained code at `deploy`."""

    def __init__(self, deploy):
        self.deploy = deploy
        self.calls = 0

    async def get_code(self, address, block):
        self.calls += 1
        has_code = self.deploy is not None and block >= self.deploy
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
    tests can see, since those never touch the read plan or the repository."""
    from src.service.project_monitor.read_plan import build_read_plan

    plan_contracts = {read.contract for read in build_read_plan(NETNET)}

    client = FakeCodeClient(deploy=1_000)
    asyncio.run(backfill.step_deploy_blocks(repository, client, head=2_000))

    deploy_blocks = repository.get_deploy_blocks(PROJECT)
    assert plan_contracts <= set(deploy_blocks), (
        f'missing from the store: {plan_contracts - set(deploy_blocks)}'
    )
    assert all(deploy_blocks[name] == 1_000 for name in plan_contracts)
    # Staking is read separately from the plan loop, for `launch_block`, which
    # the report and every other backfill step key off of.
    assert repository.get_project_value(PROJECT, 'launch_block') == '1000'


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
            'boundaries': [],
        }

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
