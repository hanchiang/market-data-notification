"""Capture the dossier's fixtures at one pinned block.

Committed rather than hand-written so a collector change can RECAPTURE them
(design, Testing). A fixture nobody can regenerate stops being evidence about
the provider the moment the provider's shape moves: the recorded bodies would
still parse, the tests would still pass, and the client would still be wrong.

What it captures, per project named on the command line:

* the DEX provider's pair record and its every-pool-for-this-token record,
  verbatim;
* the explorer's address, contract and creation-transaction records;
* the JSON-RPC responses behind identity and contract safety at ONE pinned
  block -- the block is written into the manifest, because a fixture without the
  height it was read at cannot be re-derived;
* the creation log the identity collector reads to recover a pool's key -- the
  v4 `Initialize` or the v3 `PoolCreated`. Without it the fixture replays only
  part of the collector, and the criterion it exists to re-run offline (A1) is
  the one that needs the whole of it.

It reads only. Nothing here writes to the store, sends anything, or touches a
keyed endpoint's URL: the captured JSON-RPC bodies carry `endpoint_kind` and
never a URL, exactly as the evidence table does.

Usage:
  PYTHONPATH="$(pwd)" poetry run python scripts/capture_onchain_fixtures.py \\
      --project touch-grass --out tests/unit/service/onchain/fixtures
"""
import argparse
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from market_data_library.core.crypto.blockscout import BlockscoutNotFound, BlockscoutService
from market_data_library.core.crypto.dexscreener import DexscreenerService

from src.runtime.runtime_mode import RuntimeMode
from src.service.onchain import chain as chain_module
from src.service.onchain.collectors import uniswap
from src.service.onchain.config import (
    get_dexscreener_slug,
    get_registry_path,
)
from src.service.onchain.explorer import build_explorer_service
from src.service.onchain.registry import load_registry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('Capture onchain fixtures')

DEFAULT_OUT = Path('tests/unit/service/onchain/fixtures')
# A whole-history creation-log scan on the public endpoint is unbounded: it walks
# from block 0, and on a chain with tens of millions of blocks a topic-filtered
# query can time the node out at every width -- predict-fwa's did, for over ten
# minutes. Capture is an operator command, not a job, so it gives up rather than
# hanging. An empty log set is the same fixture the collector's own `unavailable`
# produces, so the criterion still re-runs offline against what the chain gave.
CREATION_LOG_BUDGET_SECONDS = 240.0


async def capture(project_key: str, out_dir: Path, test_mode: bool) -> Dict[str, Any]:
    registry = load_registry(get_registry_path())
    project = registry.projects[project_key]
    chain_entry = registry.chain_for(project)
    slug = get_dexscreener_slug(chain_entry.chain_id)

    manifest: Dict[str, Any] = {
        'project': project_key,
        'chain_id': chain_entry.chain_id,
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'files': [],
    }
    out_dir.mkdir(parents=True, exist_ok=True)

    dexscreener = DexscreenerService()
    # Through the factory, so this is keyed like the build job. It matters more
    # here: the script reads the service directly rather than through
    # `ExplorerUnit`, so it has no degradation path, and an unkeyed 403 aborts
    # after the dexscreener and log files are rewritten -- leaving them beside
    # the previous run's explorer bodies, a block-inconsistent fixture set the
    # replay tests load without complaint.
    explorer = build_explorer_service(chain_entry.explorer_api)
    role = chain_module.state_role(RuntimeMode.from_test_mode(test_mode))
    try:
        pairs, pair_body = await dexscreener.get_pairs_raw(
            chain_id=slug, pair_addresses=[project.pool_ref]
        )
        _write(out_dir, f'{project_key}.dexscreener_pair.json', pair_body, manifest)
        if not pairs:
            raise SystemExit(f'the provider has no pair for {project_key}')
        token_address = str(pairs[0].baseToken.address).lower()

        _, token_pairs_body = await dexscreener.get_token_pairs_raw(
            chain_id=slug, token_address=token_address
        )
        _write(out_dir, f'{project_key}.dexscreener_token_pairs.json', token_pairs_body, manifest)

        log_role = chain_module.log_role()
        async with role.client() as client, log_role.client() as log_client:
            head, timestamp, _ = await chain_module.pin_run_block(client)
            log_head, _ = await log_client.block_number()
            head = min(head, log_head)
            manifest['block'] = head
            manifest['block_timestamp'] = timestamp
            manifest['endpoint_kind'] = role.endpoint.kind
            rpc = await _capture_rpc(client, project, chain_entry, token_address, head)
            # The identity collector bounds its creation-log query by searching
            # block headers, so those reads belong in the same fixture: a replay
            # that could not answer them would either fail or, worse, quietly
            # take a different window than the build does.
            recorder = _HeaderRecorder(client, rpc)
            logs = await _capture_creation_logs(
                log_client, client, project, chain_entry, head, timestamp,
                pairs[0], recorder,
            )
            _write(out_dir, f'{project_key}.jsonrpc.json', rpc, manifest)
            _write(out_dir, f'{project_key}.logs.json', logs, manifest)

        explorer_bodies = await _capture_explorer(explorer, token_address)
        _write(out_dir, f'{project_key}.blockscout.json', explorer_bodies, manifest)
    finally:
        await dexscreener.cleanup()
        await explorer.cleanup()

    _write(out_dir, f'{project_key}.manifest.json', manifest, manifest, register=False)
    return manifest


async def _capture_rpc(client, project, chain_entry, token_address: str, block: int) -> List[Dict[str, Any]]:
    """The state reads identity and contract safety issue, with their bodies.

    Stored as `{method, params, endpoint_kind, body}` -- the evidence table's own
    shape, so a fixture can be replayed into the collectors without a translation
    layer that could disagree with the store.
    """
    captured: List[Dict[str, Any]] = []

    async def record(raw):
        captured.append(
            {
                'method': raw.method,
                'params': raw.params,
                'endpoint_kind': raw.endpoint_kind,
                'body': raw.body,
            }
        )

    for name in ('name', 'symbol', 'decimals', 'totalSupply'):
        try:
            _, raw = await client.call(token_address, uniswap.call_data(name), block)
            await record(raw)
        except Exception as exc:  # a token that lacks a getter is a real case
            logger.warning('%s() unavailable: %s', name, type(exc).__name__)

    _, raw = await client.get_code(token_address, block)
    await record(raw)

    if project.pool_ref_kind == 'pool_address':
        for name in ('factory', 'token0', 'token1', 'fee', 'tickSpacing', 'liquidity'):
            _, raw = await client.call(project.pool_ref, uniswap.call_data(name), block)
            await record(raw)
    else:
        state_view = chain_entry.uniswap['v4_state_view']
        for name, args in (('getSlot0', ['bytes32']), ('getLiquidity', ['bytes32'])):
            _, raw = await client.call(
                state_view, uniswap.call_data(name, args, [project.pool_ref]), block
            )
            await record(raw)
    return captured


class _HeaderRecorder:
    """A client proxy that captures every `eth_getBlockByNumber` it serves.

    `find_block_at_or_before` returns a count of header reads and not their
    bodies, so the only way to record exactly the reads the collector will make
    is to watch the client while the same search runs.
    """

    def __init__(self, client, captured: List[Dict[str, Any]]):
        self._client = client
        self._captured = captured

    async def get_block_by_number(self, block: int):
        header, raw = await self._client.get_block_by_number(block)
        self._captured.append(
            {
                'method': raw.method,
                'params': raw.params,
                'endpoint_kind': raw.endpoint_kind,
                'body': raw.body,
            }
        )
        return header, raw


async def _capture_creation_logs(
    log_client, state_client, project, chain_entry, block: int,
    block_timestamp: int, pair: Any, recorder: Any,
) -> Dict[str, Any]:
    """`{query name: [log, ...]}` for the creation log identity reads.

    Keyed by the collector's own query name so the replay can answer
    `fetch_window` by name and cannot accidentally serve one pool's logs to
    another's query.
    """
    from market_data_library.core.onchain.evm import abi
    from src.service.project_monitor.logs import LogQuery, fetch_window

    if project.pool_ref_kind == 'pool_address':
        currency0, _ = await state_client.call(
            project.pool_ref, uniswap.call_data('token0'), block
        )
        currency1, _ = await state_client.call(
            project.pool_ref, uniswap.call_data('token1'), block
        )
        fee_data, _ = await state_client.call(
            project.pool_ref, uniswap.call_data('fee'), block
        )
        query = LogQuery(
            name=f'pool_created:{project.key}',
            addresses=[chain_entry.uniswap['v3_factory']],
            topics=[
                uniswap.V3_POOL_CREATED.topic0,
                '0x' + '0' * 24 + str(abi.decode_single('address', currency0)).removeprefix('0x'),
                '0x' + '0' * 24 + str(abi.decode_single('address', currency1)).removeprefix('0x'),
                '0x' + f'{int(abi.decode_single("uint24", fee_data)):064x}',
            ],
            spec=uniswap.V3_POOL_CREATED,
        )
    else:
        query = LogQuery(
            name=f'v4_initialize:{project.key}',
            addresses=[chain_entry.uniswap['v4_pool_manager']],
            topics=[uniswap.V4_INITIALIZE.topic0, project.pool_ref],
            spec=uniswap.V4_INITIALIZE,
        )
    # An empty list is a CAPTURE, not a failure to record: the collector reads an
    # unreachable creation log as `unavailable` and stays `partial`, and a fixture
    # that refuses to be written for that case would leave the criterion it exists
    # to prove with no fixture at all. Observed on predict-fwa, whose
    # `PoolCreated` query the public endpoint answers with a timeout.
    from market_data_library.core.onchain.evm import EvmClientError

    from src.service.onchain.collectors.identity import creation_search_bounds
    from src.service.onchain.config import get_chain_constants

    from_block, to_block, reads = await creation_search_bounds(
        recorder,
        getattr(pair, 'pairCreatedAt', None),
        head=block,
        head_timestamp=block_timestamp,
        constants=get_chain_constants(chain_entry.chain_id),
    )
    logger.info(
        '%s searched over blocks %s-%s after %s header reads',
        query.name, from_block, to_block, reads,
    )
    try:
        logs, _ = await asyncio.wait_for(
            fetch_window(log_client, query, from_block, to_block),
            timeout=CREATION_LOG_BUDGET_SECONDS,
        )
    except (EvmClientError, asyncio.TimeoutError) as exc:
        logger.warning(
            '%s unavailable (%s); capturing an empty log set',
            query.name,
            type(exc).__name__,
        )
        logs = []
    return {query.name: logs}


async def _capture_explorer(explorer: BlockscoutService, token_address: str) -> Dict[str, Any]:
    """Every explorer route the dossier reads, with 404s recorded as 404s.

    A `not_found` entry is a fixture worth having: it is the ordinary answer for
    an unverified contract, and the collector has to read it as `unavailable`
    rather than as a failure.
    """
    bodies: Dict[str, Any] = {}
    address = await _route(bodies, 'address', explorer.get_address(token_address))
    await _route(bodies, 'smart_contract', explorer.get_smart_contract(token_address))
    creation_tx = getattr(address, 'creation_transaction', None)
    if creation_tx:
        await _route(bodies, 'transaction', explorer.get_transaction(creation_tx))
    return bodies


async def _route(bodies: Dict[str, Any], key: str, awaitable: Any) -> Any:
    """Record one explorer route, INCLUDING its failure.

    This deployment answers 500 most nights, and a capture that aborts on the
    first one can never produce a fixture for the projects that need one most.
    A recorded `api_error` replays as the same `BlockscoutApiError` the build
    sees, which is what makes the replayed section `partial` rather than `ok` --
    the fixture then proves the criterion holds through the failure, not only
    when the explorer happens to be up.
    """
    from market_data_library.util.exception import BlockscoutApiError

    try:
        result = await awaitable
    except BlockscoutApiError:
        bodies[key] = 'api_error'
        logger.warning('explorer route %s failed; captured as api_error', key)
        return None
    if isinstance(result, BlockscoutNotFound):
        bodies[key] = 'not_found'
        return None
    bodies[key] = result.raw
    return result


def _write(out_dir: Path, name: str, payload: Any, manifest: Dict[str, Any], register: bool = True) -> None:
    path = out_dir / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
    if register:
        manifest['files'].append(name)
    logger.info('wrote %s', path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', action='append', required=True)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    parser.add_argument('--test_mode', type=int, default=1)
    args = parser.parse_args()
    for project in args.project:
        asyncio.run(capture(project, args.out, bool(args.test_mode)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
