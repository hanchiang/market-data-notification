"""One sample: pin a block, read state, fetch the log window, write once.

The atomicity rule (R8) is the shape of this module. Every write for a sample --
the sample row, its raw responses, its readings, its mints, its flows, its
events, the cursor it implies -- happens inside one Postgres transaction that is
committed after the last decode. A core failure before that commit rolls the
whole thing back, so the cursor does not move and no partial sample exists to be
mistaken for a real one.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from market_data_library.core.onchain.evm import (
    EvmClient,
    EvmClientError,
    abi,
)

from . import logs as log_plane
from .attribution import build_flow_rows
from .config import BLOCKS_PER_HOUR, ProjectConfig
from .read_plan import (
    CORE,
    Read,
    borrow_rate_view_calldata,
    build_read_plan,
)
from .repository import ProjectMonitorRepository

logger = logging.getLogger('Project monitor recorder')

STATE_OK = 'ok'
STATE_NOT_DEPLOYED = 'not_deployed'
STATE_FAILED = 'failed'

KIND_LIVE = 'live'
KIND_BACKFILL = 'backfill'


class CoreReadFailedError(RuntimeError):
    """A core read failed after retries: the sample writes nothing (R8)."""


@dataclass
class SampleResult:
    block: int
    block_timestamp: int
    epoch_number: Optional[int]
    endpoint_kind: str
    readings: List[Dict[str, Any]] = field(default_factory=list)
    raw_responses: List[Any] = field(default_factory=list)
    failed_peripheral: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


async def read_state(
    client: EvmClient,
    project: ProjectConfig,
    block: int,
    *,
    deploy_blocks: Optional[Dict[str, Optional[int]]] = None,
) -> SampleResult:
    """Issue every read in the plan at one explicit block.

    Core reads go first and raise on failure. Peripheral reads are issued in
    their own batches, and a failure there is recorded on the reading rather
    than propagated -- a dead third-party price feed must not blank the treasury
    series.
    """
    deploy_blocks = deploy_blocks or {}
    plan = build_read_plan(project)

    block_body, block_raw = await client.get_block_by_number(block)
    result = SampleResult(
        block=block,
        block_timestamp=int(block_body['timestamp'], 16),
        epoch_number=None,
        endpoint_kind=client.endpoint.kind,
        raw_responses=[block_raw],
    )

    decoded: Dict[str, Any] = {}
    for tier in (CORE, 'peripheral'):
        tier_reads = [r for r in plan if r.tier == tier]
        for batch in sorted({r.batch for r in tier_reads}):
            batch_reads = [r for r in tier_reads if r.batch == batch]
            await _issue_batch(
                client, batch_reads, block, result, decoded, deploy_blocks, tier
            )

    # The IRM read cannot be encoded until this sample's Market tuple exists, so
    # it is a follow-up rather than a plan entry.
    await _read_borrow_rate(client, project, block, result, decoded)

    _resolve_decimals(result, decoded)

    epoch = decoded.get('Staking.epoch')
    if epoch is not None:
        # epoch() returns (length, number, end, distribute); the number is what
        # assigns the sample to an epoch. Clock time never does (R5).
        result.epoch_number = int(epoch[1])
    return result


def _resolve_decimals(result: SampleResult, decoded: Dict[str, Any]) -> None:
    """Fill each reading's decimals once every read in the sample has returned.

    Must run after the whole plan, not per batch: a token's amount and its
    `decimals()` are issued in the same batch and the amount comes first, so a
    per-read lookup finds nothing. The decimals stored are still the ones THIS
    sample observed (R5) -- the pass only defers the lookup, it does not reach
    for a cached or constant value.
    """
    for reading in result.readings:
        source = reading.pop('_decimals_from', None)
        if reading['state'] != STATE_OK or source is None:
            continue
        if source.startswith('const:'):
            reading['decimals'] = int(source.split(':', 1)[1])
            continue
        sibling = decoded.get(source)
        reading['decimals'] = int(sibling) if sibling is not None else None


async def _issue_batch(
    client: EvmClient,
    batch_reads: Sequence[Read],
    block: int,
    result: SampleResult,
    decoded: Dict[str, Any],
    deploy_blocks: Dict[str, Optional[int]],
    tier: str,
) -> None:
    issuable: List[Read] = []
    for read in batch_reads:
        deploy_block = deploy_blocks.get(read.contract)
        if deploy_block is not None and block < deploy_block:
            # A contract that did not exist at this block is a defined
            # observation, not a failure -- and no call is issued for it.
            result.readings.append(
                {
                    'name': read.name, 'contract': read.contract, 'tier': read.tier,
                    'raw_hex': None, 'value_int': None, 'value_json': None,
                    'decimals': None, 'state': STATE_NOT_DEPLOYED, 'error_class': None,
                }
            )
            continue
        issuable.append(read)

    if not issuable:
        return

    try:
        results = await client.batch_call(
            [(r.to, r.calldata) for r in issuable], block
        )
    except EvmClientError as exc:
        if tier == CORE:
            raise CoreReadFailedError(
                f'core batch failed: {type(exc).__name__}'
            ) from exc
        # Peripheral: the batch is retried one call at a time, so one bad read
        # does not cost the other nine. Without this, a single reverting getter
        # would fail every reading that happened to share its batch.
        await _issue_individually(client, issuable, block, result, decoded)
        return

    for read, (raw_hex, raw) in zip(issuable, results):
        result.raw_responses.append(raw)
        _record_decoded(read, raw_hex, result, decoded)


async def _issue_individually(
    client: EvmClient,
    reads: Sequence[Read],
    block: int,
    result: SampleResult,
    decoded: Dict[str, Any],
) -> None:
    for read in reads:
        try:
            raw_hex, raw = await client.call(read.to, read.calldata, block)
        except EvmClientError as exc:
            result.readings.append(
                {
                    'name': read.name, 'contract': read.contract, 'tier': read.tier,
                    'raw_hex': None, 'value_int': None, 'value_json': None,
                    'decimals': None, 'state': STATE_FAILED,
                    'error_class': type(exc).__name__,
                }
            )
            result.failed_peripheral.append(read.name)
            continue
        result.raw_responses.append(raw)
        _record_decoded(read, raw_hex, result, decoded)


def _record_decoded(
    read: Read, raw_hex: str, result: SampleResult, decoded: Dict[str, Any]
) -> None:
    value = abi.decode_single(read.result_type, raw_hex)
    decoded[read.name] = value
    # A scalar goes in `value_int` for arithmetic; a tuple goes in `value_json`
    # as strings, because a uint256 exceeds what JSON numbers represent exactly.
    is_scalar = isinstance(value, (int, bool)) and not isinstance(value, tuple)
    result.readings.append(
        {
            'name': read.name,
            'contract': read.contract,
            'tier': read.tier,
            'raw_hex': raw_hex,
            'value_int': int(value) if is_scalar else None,
            'value_json': None if is_scalar else [str(v) for v in value],
            # Decimals are resolved in a second pass: `NET.totalSupply` is
            # issued before `NET.decimals` in the same batch, so the sibling
            # reading does not exist yet at this point. Filling it here silently
            # produced null decimals on every token amount in the first capture.
            'decimals': None,
            '_decimals_from': read.decimals_from,
            'state': STATE_OK,
            'error_class': None,
        }
    )


async def _read_borrow_rate(
    client: EvmClient,
    project: ProjectConfig,
    block: int,
    result: SampleResult,
    decoded: Dict[str, Any],
) -> None:
    market = decoded.get('Morpho.market')
    if market is None:
        return
    try:
        raw_hex, raw = await client.call(
            project.address('adaptiveCurveIrm'),
            borrow_rate_view_calldata(list(market)),
            block,
        )
    except EvmClientError as exc:
        result.readings.append(
            {
                'name': 'IRM.borrowRateView', 'contract': 'adaptiveCurveIrm',
                'tier': 'peripheral', 'raw_hex': None, 'value_int': None,
                'value_json': None, 'decimals': None, 'state': STATE_FAILED,
                'error_class': type(exc).__name__,
            }
        )
        result.failed_peripheral.append('IRM.borrowRateView')
        return
    result.raw_responses.append(raw)
    result.readings.append(
        {
            'name': 'IRM.borrowRateView', 'contract': 'adaptiveCurveIrm',
            'tier': 'peripheral', 'raw_hex': raw_hex,
            'value_int': abi.decode_single('uint256', raw_hex), 'value_json': None,
            'decimals': 18, 'state': STATE_OK, 'error_class': None,
        }
    )


async def read_log_window(
    client: EvmClient,
    project: ProjectConfig,
    project_name: str,
    from_block: int,
    to_block: int,
    *,
    net_decimals: int,
    usdg_decimals: int,
    use_bond_event: bool = True,
) -> Dict[str, Any]:
    """Every log query for the window, decoded, attributed, ready to write.

    The log window is CORE: a failure here fails the sample, because a sample
    whose flows are missing would be indistinguishable from an epoch in which
    nothing moved.
    """
    queries = {q.name: q for q in log_plane.build_log_queries(project)}
    fetched: Dict[str, List[Dict[str, Any]]] = {}
    raws: List[Any] = []
    for name, query in queries.items():
        entries, query_raws = await log_plane.fetch_window(
            client, query, from_block, to_block
        )
        fetched[name] = entries
        raws.extend(query_raws)

    mint_rows = log_plane.build_mint_rows(
        project_name, project, fetched['net_mints'], net_decimals
    )
    bond_event_rows = log_plane.extract_event_rows(
        project_name, 'bondDepository', 'BondCreated',
        log_plane.BOND_CREATED, fetched['bond_created'],
    )
    flow_rows = build_flow_rows(
        project_name=project_name,
        project=project,
        inflows=[log_plane.decode_transfer(entry) for entry in fetched['usdg_in']],
        outflows=[log_plane.decode_transfer(entry) for entry in fetched['usdg_out']],
        mint_rows=mint_rows,
        bond_event_rows=bond_event_rows,
        usdg_decimals=usdg_decimals,
        use_bond_event=use_bond_event,
    )

    event_rows = list(bond_event_rows)
    event_rows += log_plane.extract_event_rows(
        project_name, 'inverseBond', 'InverseBonded',
        log_plane.INVERSE_BONDED, fetched['inverse_bonded'],
    )
    event_rows += log_plane.extract_event_rows(
        project_name, 'premiumSeller', 'PremiumSold',
        log_plane.PREMIUM_SOLD, fetched['premium_sold'],
    )
    for name, contract in (
        ('sleeve_out', 'managerSleeve'),
        ('net_from_pair', 'canonicalV2Pair'),
        ('net_tax_collector_in', 'taxCollector'),
    ):
        event_rows += log_plane.extract_event_rows(
            project_name, contract, f'Transfer:{name}',
            log_plane.TRANSFER, fetched[name],
        )

    return {
        'mints': mint_rows,
        'flows': flow_rows,
        'events': event_rows,
        'raw_responses': raws,
        'boundaries': log_plane.rebase_boundaries(project, mint_rows),
    }


def resolve_window_start(
    repository: ProjectMonitorRepository,
    project_name: str,
    pinned_block: int,
) -> tuple[int, Optional[str]]:
    """Where this sample's log window begins, and why.

    The cursor is `max(block)` over LIVE samples, never the last row inserted:
    backfill writes older rows later, so the two are different numbers and the
    wrong one rewinds the window over covered ground. With no live sample yet,
    `cursor_origin` (set by backfill) takes over; with neither, the first run
    takes one hour of chain and records that it did so on the run row.
    """
    cursor = repository.get_live_cursor(project_name)
    if cursor is not None:
        return cursor + 1, None

    origin = repository.get_project_value(project_name, 'cursor_origin')
    if origin is not None:
        return int(origin) + 1, f'first live window from backfill cursor_origin {origin}'

    start = max(0, pinned_block - BLOCKS_PER_HOUR)
    return start, (
        f'first run with no backfill: window seeded at pinned_block - '
        f'{BLOCKS_PER_HOUR} ({start}), one hour of chain at 9.898 blocks/s'
    )


def commit_sample(
    repository: ProjectMonitorRepository,
    *,
    run_id: int,
    project_name: str,
    sample: SampleResult,
    kind: str,
    log_window: Optional[Dict[str, Any]] = None,
) -> int:
    """Write everything for one sample in one transaction (R8).

    Nothing is committed until the last row is in. The caller rolls back on a
    core failure, and because the cursor is derived from committed live samples
    rather than stored separately, a rollback rewinds the cursor for free.
    """
    sample_id = repository.insert_sample(
        run_id=run_id,
        project=project_name,
        block=sample.block,
        block_timestamp=sample.block_timestamp,
        epoch_number=sample.epoch_number,
        kind=kind,
        endpoint_kind=sample.endpoint_kind,
    )
    raw_responses = list(sample.raw_responses)
    if log_window:
        raw_responses += log_window['raw_responses']
    repository.insert_raw_responses(sample_id, raw_responses)
    repository.insert_readings(sample_id, sample.readings)

    if log_window:
        repository.insert_mints(log_window['mints'])
        repository.insert_flows(log_window['flows'])
        repository.insert_events(log_window['events'])
        for boundary in log_window['boundaries']:
            # The rebase at the N->N+1 transition lands in the first sample
            # observing N+1, so this sample's epoch number is the one the
            # boundary opens.
            repository.upsert_epoch_boundary(
                project_name,
                boundary['first_block'],
                boundary['rebase_tx'],
                epoch_number=sample.epoch_number,
            )
    repository.commit()
    return sample_id
