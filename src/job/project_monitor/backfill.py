"""One-shot backfill: deploy blocks, epoch boundaries, log history, epoch samples.

Run by hand, never scheduled. Paced through the same budgets as live reads and
holding the same advisory lock, so backfill and a live run never overlap.

Resumable by construction: each step writes what it found before the next
starts, and step 4 skips a boundary that already has a backfill sample. A
backfill that dies halfway is re-run, not unwound.

Usage:
  PYTHONPATH="$(pwd)" poetry run python src/job/project_monitor/backfill.py --steps 1,2,3,4
"""
import argparse
import asyncio
import logging
from typing import List, Optional

from market_data_library.core.onchain.evm import EvmClient, alchemy_budget, public_rpc_budget

from src.runtime.runtime_mode import RuntimeMode
from src.service.project_monitor import logs as log_plane
from src.service.project_monitor import recorder
from src.service.project_monitor.config import (
    NETNET,
    get_archive_endpoint,
    get_project_monitor_database_url,
    get_public_endpoint,
)
from src.service.project_monitor.read_plan import build_read_plan
from src.service.project_monitor.repository import ProjectMonitorRepository

logger = logging.getLogger('Project monitor backfill')

JOB_NAME = 'project_monitor.backfill'


async def find_deploy_block(client: EvmClient, address: str, head: int) -> Optional[int]:
    """Binary search `eth_getCode` for the first block with code (~26 calls).

    `0x` is a real answer for `eth_getCode` -- an address with no code yet --
    which is why the client does not raise on it: raising would turn the whole
    left half of this search into an error.
    """
    code, _ = await client.get_code(address, head)
    if code == '0x':
        return None
    low, high = 0, head
    while low < high:
        mid = (low + high) // 2
        code, _ = await client.get_code(address, mid)
        if code == '0x':
            low = mid + 1
        else:
            high = mid
    return low


async def step_deploy_blocks(
    repository: ProjectMonitorRepository, client: EvmClient, head: int
) -> str:
    project = NETNET
    contracts = {read.contract: read.to for read in build_read_plan(project)}
    found = 0
    for name, address in contracts.items():
        deploy_block = await find_deploy_block(client, address, head)
        repository.set_contract_deploy_block(project.name, name, address, deploy_block)
        found += 1
    staking_block = await find_deploy_block(client, project.address('staking'), head)
    if staking_block is not None:
        repository.set_project_value(project.name, 'launch_block', str(staking_block))
    repository.commit()
    return f'step 1: deploy blocks for {found} contracts; launch_block={staking_block}'


async def step_epoch_boundaries(
    repository: ProjectMonitorRepository, client: EvmClient, head: int
) -> str:
    """Each rebase mint is one epoch transition at an exact block.

    A gap in the boundary sequence is stored as a gap, not smoothed: the
    requirement treats an epoch longer than 8 h as a signal that no rebase
    fired, so a missing boundary is information.
    """
    project = NETNET
    launch = repository.get_project_value(project.name, 'launch_block')
    from_block = int(launch) if launch else 0
    query = {q.name: q for q in log_plane.build_log_queries(project)}['net_mints']
    entries, _ = await log_plane.fetch_window(client, query, from_block, head)
    rows = log_plane.build_mint_rows(project.name, project, entries, 9)
    boundaries = log_plane.rebase_boundaries(project, rows)
    for boundary in sorted(boundaries, key=lambda b: b['first_block']):
        # No epoch number: a rebase log does not carry one, and inventing an
        # ordinal index here would silently disagree with the chain's counter.
        # Step 4's sample at that block reads `Staking.epoch()` and fills it in.
        repository.upsert_epoch_boundary(
            project.name, boundary['first_block'], boundary['rebase_tx']
        )
    repository.commit()
    return f'step 2: {len(boundaries)} epoch boundaries from rebase mints'


async def step_log_history(
    repository: ProjectMonitorRepository, client: EvmClient, head: int
) -> str:
    """Fill logs from launch up to where live coverage already begins.

    Stopping at the earliest live window's origin rather than re-fetching to
    head: the forward pass already committed those, and `(tx_hash, log_index)`
    uniqueness would drop them anyway -- but not fetching them is cheaper than
    fetching and discarding.
    """
    project = NETNET
    launch = repository.get_project_value(project.name, 'launch_block')
    from_block = int(launch) if launch else 0
    live_cursor = repository.get_live_cursor(project.name)
    to_block = live_cursor if live_cursor is not None else head

    window = await recorder.read_log_window(
        client, project, project.name, from_block, to_block,
        net_decimals=9, usdg_decimals=6,
    )
    mints = repository.insert_mints(window['mints'])
    flows = repository.insert_flows(window['flows'])
    events = repository.insert_events(window['events'])
    repository.set_project_value(project.name, 'cursor_origin', str(to_block))
    repository.commit()
    return f'step 3: +{mints} mints, +{flows} flows, +{events} events to block {to_block}'


async def step_epoch_samples(
    repository: ProjectMonitorRepository,
    client: EvmClient,
    run_id: int,
    max_samples: Optional[int] = None,
) -> str:
    """A state-only sample at `b - 1` for every boundary block `b`.

    No log window: logs were never what was lost -- they are served for the
    whole history and the forward cursor has already passed the gap. The sample
    is flagged `backfill` so a late-filled row stays distinguishable from one
    recorded live.
    """
    project = NETNET
    boundaries = repository.fetch_all(
        'SELECT first_block FROM epoch_boundary WHERE project = %s '
        'ORDER BY first_block DESC',
        (project.name,),
    )
    existing = {
        row['block']
        for row in repository.fetch_all(
            "SELECT block FROM sample WHERE project = %s AND kind = 'backfill'",
            (project.name,),
        )
    }
    deploy_blocks = repository.get_deploy_blocks(project.name)
    filled = 0
    for boundary in boundaries:
        block = int(boundary['first_block']) - 1
        if block in existing or block < 0:
            continue
        try:
            sample = await recorder.read_state(
                client, project, block, deploy_blocks=deploy_blocks
            )
            recorder.commit_sample(
                repository, run_id=run_id, project_name=project.name,
                sample=sample, kind=recorder.KIND_BACKFILL,
            )
            # The sample sits at `first_block - 1`, so it observes the epoch the
            # boundary CLOSES. The boundary's number is the epoch it opens --
            # one more. Writing `sample.epoch_number` here would disagree by one
            # with what the live writer records for the same boundary, and
            # `COALESCE` would then freeze whichever ran first.
            opening_epoch = (
                sample.epoch_number + 1 if sample.epoch_number is not None else None
            )
            repository.upsert_epoch_boundary(
                project.name, block + 1, None, epoch_number=opening_epoch
            )
            repository.commit()
            filled += 1
            if max_samples is not None and filled >= max_samples:
                break
        except Exception as exc:
            # A failed backfill sample is skipped and retried on the next run,
            # never left half-written.
            repository.rollback()
            logger.warning('backfill sample at %s failed: %s', block, type(exc).__name__)
    return f'step 4: {filled} backfill samples'


async def main(
    steps: List[int], test_mode: bool = False, max_samples: Optional[int] = None
) -> int:
    runtime_mode = RuntimeMode.from_test_mode(test_mode)
    archive = get_archive_endpoint()
    if archive is None:
        print('backfill needs the archive endpoint; ROBINHOOD_CHAIN_RPC_URL is unset')
        return 1

    notes = []
    with ProjectMonitorRepository(
        get_project_monitor_database_url(runtime_mode)
    ) as repository:
        run_id = repository.start_run(JOB_NAME)
        with repository.advisory_lock():
            async with EvmClient(archive, alchemy_budget()) as state_client:
                head, _ = await state_client.block_number()
                if 1 in steps:
                    notes.append(await step_deploy_blocks(repository, state_client, head))
            async with EvmClient(
                get_public_endpoint(), public_rpc_budget()
            ) as log_client:
                if 2 in steps:
                    notes.append(await step_epoch_boundaries(repository, log_client, head))
                if 3 in steps:
                    notes.append(await step_log_history(repository, log_client, head))
            async with EvmClient(archive, alchemy_budget()) as state_client:
                if 4 in steps:
                    notes.append(
                        await step_epoch_samples(
                            repository, state_client, run_id, max_samples
                        )
                    )
        repository.finish_run(run_id, outcome='ok', notes='; '.join(notes))
    print('\n'.join(notes))
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--steps', default='1,2,3,4')
    # Paced and resumable: a capped run is how a launch backfill is spread over
    # several invocations rather than one long burst against the free tier.
    parser.add_argument('--max_samples', type=int, default=None)
    parser.add_argument('--test_mode', type=int, default=0)
    args = parser.parse_args()
    raise SystemExit(
        asyncio.run(
            main(
                [int(s) for s in args.steps.split(',')],
                test_mode=bool(args.test_mode),
                max_samples=args.max_samples,
            )
        )
    )
