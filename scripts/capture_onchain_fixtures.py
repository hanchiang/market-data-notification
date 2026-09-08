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
  height it was read at cannot be re-derived.

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
from src.service.onchain.config import get_dexscreener_slug, get_registry_path
from src.service.onchain.registry import load_registry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('Capture onchain fixtures')

DEFAULT_OUT = Path('tests/unit/service/onchain/fixtures')


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
    explorer = BlockscoutService(chain_entry.explorer_api)
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

        async with role.client() as client:
            head, timestamp, _ = await chain_module.pin_run_block(client)
            manifest['block'] = head
            manifest['block_timestamp'] = timestamp
            manifest['endpoint_kind'] = role.endpoint.kind
            rpc = await _capture_rpc(client, project, chain_entry, token_address, head)
            _write(out_dir, f'{project_key}.jsonrpc.json', rpc, manifest)

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


async def _capture_explorer(explorer: BlockscoutService, token_address: str) -> Dict[str, Any]:
    """Every explorer route the dossier reads, with 404s recorded as 404s.

    A `not_found` entry is a fixture worth having: it is the ordinary answer for
    an unverified contract, and the collector has to read it as `unavailable`
    rather than as a failure.
    """
    bodies: Dict[str, Any] = {}
    address = await explorer.get_address(token_address)
    bodies['address'] = (
        'not_found' if isinstance(address, BlockscoutNotFound) else address.raw
    )
    contract = await explorer.get_smart_contract(token_address)
    bodies['smart_contract'] = (
        'not_found' if isinstance(contract, BlockscoutNotFound) else contract.raw
    )
    creation_tx = None if isinstance(address, BlockscoutNotFound) else address.creation_transaction
    if creation_tx:
        transaction = await explorer.get_transaction(creation_tx)
        bodies['transaction'] = (
            'not_found' if isinstance(transaction, BlockscoutNotFound) else transaction.raw
        )
    return bodies


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
