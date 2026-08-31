"""One-shot backfill: deploy blocks, epoch boundaries, log history, epoch samples.

Run by hand, never scheduled. Paced through the same budgets as live reads and
holding the same advisory lock, so backfill and a live run never overlap.

Two endpoints, split by plane. Steps 1 and 4 read STATE at archive depth, which
only the keyed endpoint serves, so they go there on `alchemy_budget()`. Steps 2
and 3 read LOGS, which the keyed endpoint's free tier refuses beyond a ten-block
range, so they go to the public RPC on `public_rpc_budget()` -- see `logs.py`'s
module docstring for the measurement. One client per endpoint, not one per step:
a fresh `EvmClient` restarts its budget's rolling window at zero, so a per-step
client could burst above the intended rate at every step boundary.

Resumable by construction. Step 1 writes what it found before step 2 starts and
step 4 skips a boundary that already has a backfill sample. The two LOG SWEEPS,
steps 2 and 3, each commit a watermark per segment under their own key, so an
interrupted sweep resumes where it stopped rather than at the launch block --
the 2026-08-30 run lost 36 minutes of work for want of that. A backfill that
dies halfway is re-run, not unwound.

Usage:
  PYTHONPATH="$(pwd)" poetry run python src/job/project_monitor/backfill.py --steps 1,2,3,4
"""
import argparse
import asyncio
import logging
from typing import List, Optional

from market_data_library.core.onchain.evm import (
    EvmClient,
    alchemy_budget,
    public_rpc_budget,
)

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

# Resume keys, one per sweeping step. They are deliberately NOT shared: step 2
# sweeps to `head` while step 3 stops at the live cursor, and `--steps` lets an
# operator run either alone, so a single key would let one step's progress skip
# blocks the other has never read.
EPOCH_BOUNDARY_WATERMARK_KEY = 'epoch_boundary_watermark'
LOG_HISTORY_WATERMARK_KEY = 'log_history_watermark'

# How much of the chain one segment covers, for both sweeping steps. UNMEASURED
# against a full sweep -- no sweep has ever finished -- so this is a judgement
# between two costs, stated rather than implied.
#
# Larger segments waste less: a segment narrower than `MAX_LOG_WINDOW_BLOCKS`
# (1.5M) clips the log window, so where the chain is sparse enough to serve a
# full-width query, 500,000 costs three calls per query where one would have
# done. Over ~50M blocks that is ~101 segments x 9 queries = ~909 calls at the
# floor instead of ~306.
#
# Smaller segments lose less: a segment that fails part-way is re-fetched from
# its start. Near the head, where 25,000 blocks a call is what the endpoint
# serves, 500,000 blocks is 20 calls x 9 queries = 180 calls -- comparable to
# the ~36 minutes the 2026-08-30 run threw away, and the ceiling on what any
# single interruption can now cost.
#
# 500,000 optimises the failure case, because that is the case with evidence:
# the one real sweep this job has attempted was interrupted. Revisit with a real
# end-to-end measurement, which is the only thing that settles it.
#
# The same size is conservative for step 2, which issues ONE query per segment
# where step 3 issues nine: its per-segment cost, and so its loss ceiling, is a
# ninth of step 3's at the same width.
LOG_SWEEP_SEGMENT_BLOCKS = 500_000


def _resume_point(
    repository: ProjectMonitorRepository, project_name: str, watermark_key: str
) -> int:
    """Where a sweeping step starts: after its own watermark, or at launch.

    `max` rather than the watermark alone, because `launch_block` can be written
    (or corrected) by step 1 AFTER a sweep has already run, and re-reading blocks
    that predate the contract is pure waste.
    """
    launch = repository.get_project_value(project_name, 'launch_block')
    from_block = int(launch) if launch else 0
    watermark = repository.get_project_value(project_name, watermark_key)
    if watermark is not None:
        from_block = max(from_block, int(watermark) + 1)
    return from_block


def _segments(from_block: int, to_block: int, segment_blocks: int):
    """The half-open sweep as inclusive `[start, end]` pairs.

    Yields nothing when the range is already covered -- which is what a re-run of
    a finished step looks like, and is why it is not an error.
    """
    start = from_block
    while start <= to_block:
        end = min(start + segment_blocks - 1, to_block)
        yield start, end
        start = end + 1


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
    repository: ProjectMonitorRepository,
    client: EvmClient,
    head: int,
    *,
    segment_blocks: int = LOG_SWEEP_SEGMENT_BLOCKS,
) -> str:
    """Each rebase mint is one epoch transition at an exact block.

    A gap in the boundary sequence is stored as a gap, not smoothed: the
    requirement treats an epoch longer than 8 h as a signal that no rebase
    fired, so a missing boundary is information.

    Segmented and watermarked on the same terms as step 3, because it is the
    same shape of job: one log query over the whole chain, hours long at the
    pace the public endpoint tolerates, and it runs FIRST in a combined run --
    so before this, an interruption anywhere in it discarded everything and the
    re-run started at the launch block again. The `-32000 log query timed out`
    that first exposed the ratchet was measured in this step.

    Boundaries never span a segment: a rebase mint is a single log at a single
    block, so a segment's rows depend on nothing outside it.

    The raw responses are stored, same as step 3's (R2, and the 2026-08-30
    ruling that the backfill persists each `eth_getLogs` response verbatim). An
    `epoch_boundary` row is a figure like any other, and over `(live_cursor,
    head]` -- step 2's range beyond step 3's -- no other stored read covers it.
    """
    project = NETNET
    from_block = _resume_point(repository, project.name, EPOCH_BOUNDARY_WATERMARK_KEY)
    query = {q.name: q for q in log_plane.build_log_queries(project)}['net_mints']

    found = raws = segments = 0
    for start, end in _segments(from_block, head, segment_blocks):
        try:
            entries, responses = await log_plane.fetch_window(
                client, query, start, end
            )
            rows = log_plane.build_mint_rows(project.name, project, entries, 9)
            boundaries = log_plane.rebase_boundaries(project, rows)
            for boundary in sorted(boundaries, key=lambda b: b['first_block']):
                # No epoch number: a rebase log does not carry one, and inventing
                # an ordinal index here would silently disagree with the chain's
                # counter. Step 4's sample at that block reads `Staking.epoch()`
                # and fills it in.
                repository.upsert_epoch_boundary(
                    project.name, boundary['first_block'], boundary['rebase_tx']
                )
            # Step 2 and step 3 both query `net_mints`, over ranges that overlap
            # wherever step 3 reaches, and they share one table keyed on
            # `(project, query_name, from_block, to_block)`. Two cases, both
            # intended:
            #
            # - SAME bounds (the common one -- a fresh run of both steps starts
            #   each at `launch_block` and segments identically, so neither
            #   narrowing means byte-identical spans). The key collides,
            #   `ON CONFLICT DO NOTHING` keeps whichever step ran first, and that
            #   is right rather than lossy: both steps derived their rows from
            #   the same query over the same span, so one stored body
            #   re-derives both. What first-write-wins does discard is a genuine
            #   difference between two reads taken at different times -- a
            #   reorg, say -- which is the same property already accepted for a
            #   step-3 re-run.
            # - DIFFERENT bounds, whenever either step's `fetch_window` narrowed
            #   where the other did not. Both rows are stored, and that is the
            #   honest record: two reads happened, at different spans, each
            #   holding what it returned.
            raws += repository.insert_backfill_log_raw_responses(
                project.name, [(query.name, raw) for raw in responses]
            )
            repository.set_project_value(
                project.name, EPOCH_BOUNDARY_WATERMARK_KEY, str(end)
            )
        except Exception:
            # The boundaries, the raws and the watermark move together or not at
            # all.
            repository.rollback()
            raise
        repository.commit()
        found += len(boundaries)
        segments += 1

    return (
        f'step 2: {found} epoch boundaries from rebase mints, '
        f'+{raws} raw log responses over {segments} segments to block {head}'
    )


async def step_log_history(
    repository: ProjectMonitorRepository,
    client: EvmClient,
    head: int,
    *,
    segment_blocks: int = LOG_SWEEP_SEGMENT_BLOCKS,
) -> str:
    """Fill logs from launch up to where live coverage already begins.

    Stopping at the earliest live window's origin rather than re-fetching to
    head: the forward pass already committed those, and `(tx_hash, log_index)`
    uniqueness would drop them anyway -- but not fetching them is cheaper than
    fetching and discarding.

    Swept in segments with a committed watermark per segment, because at the
    request pace the public endpoint tolerates a full sweep runs for hours and
    the endpoint has already been observed to stop serving mid-run. Without the
    watermark the whole run is the unit of atomicity, and an interruption at
    hour three throws away three hours (2026-08-30).

    AC7 still holds INSIDE a segment: a segment that fails part-way rolls back,
    so it contributes no rows and leaves the watermark on the previous segment.
    The re-run then repeats exactly that segment and nothing already done.
    """
    project = NETNET
    from_block = _resume_point(repository, project.name, LOG_HISTORY_WATERMARK_KEY)
    live_cursor = repository.get_live_cursor(project.name)
    to_block = live_cursor if live_cursor is not None else head

    mints = flows = events = raws = segments = 0
    for start, end in _segments(from_block, to_block, segment_blocks):
        try:
            window = await recorder.read_log_window(
                client, project, project.name, start, end,
                net_decimals=9, usdg_decimals=6,
            )
            mints += repository.insert_mints(window['mints'])
            flows += repository.insert_flows(window['flows'])
            events += repository.insert_events(window['events'])
            # R2's raw-response half of this step: no sample exists to hang
            # these on (a backfill log sweep pins no single block), so they go
            # in block-keyed rather than sample-keyed. See
            # `backfill_log_raw_response`'s DDL comment.
            raws += repository.insert_backfill_log_raw_responses(
                project.name, window['raw_responses_by_query']
            )
            repository.set_project_value(
                project.name, LOG_HISTORY_WATERMARK_KEY, str(end)
            )
        except Exception:
            # The rows and the watermark move together or not at all. Committing
            # rows without the watermark would re-fetch them; moving the
            # watermark without the rows would skip them forever.
            repository.rollback()
            raise
        repository.commit()
        segments += 1

    repository.set_project_value(project.name, 'cursor_origin', str(to_block))
    repository.commit()
    return (
        f'step 3: +{mints} mints, +{flows} flows, +{events} events, '
        f'+{raws} raw log responses to block {to_block} '
        f'over {segments} segments'
    )


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
            # Split by plane, not by step. The keyed endpoint is the only one
            # with archive depth, so the state steps (1 and 4) need it; its free
            # tier refuses `eth_getLogs` above a ten-block range, so the log
            # steps (2 and 3) cannot use it at all and go to the public RPC.
            # One client per endpoint rather than one per step: a fresh
            # `EvmClient` restarts its budget's rolling window at zero, so the
            # step boundary is exactly where a burst above the intended rate
            # would slip through unaccounted for.
            async with EvmClient(archive, alchemy_budget()) as state_client, EvmClient(
                get_public_endpoint(supports_batch=True), public_rpc_budget()
            ) as log_client:
                head, _ = await state_client.block_number()
                if 1 in steps:
                    notes.append(
                        await step_deploy_blocks(repository, state_client, head)
                    )
                if 2 in steps:
                    notes.append(
                        await step_epoch_boundaries(repository, log_client, head)
                    )
                if 3 in steps:
                    notes.append(
                        await step_log_history(repository, log_client, head)
                    )
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
