"""Identity: what pool and what token the operator's reference actually names (P2).

The operator writes one `pool_ref` into the registry. Everything else in the
dossier hangs off what this collector resolves from it, so the rule here is that
a provider's claim is not identity until the chain confirms it (design D3):

* v3 -- the pool's own `factory()` must equal the registry's verified factory,
  which is what stops a look-alike pool at an address the provider happened to
  return.
* v4 -- a pool has no address, so the id is RECOMPUTED from the `Initialize`
  log's own key fields and compared with the reference (`key_matches`). An
  unknown id is not an error on this chain, it returns zeros, so existence is
  `sqrtPriceX96 != 0`.

**The log reads happen once.** Steps 3 (the v4 `Initialize` log) and 4 (creation)
produce immutable facts, so once a prior identity section holds them they are
carried forward and a nightly identity build issues no log query at all. That is
what keeps four projects inside ~5 public-RPC queries a night rather than four
full-history scans.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

from market_data_library.core.onchain.evm import EvmClientError, abi

from src.service.onchain import chain
from src.service.onchain.chain import decode_string
from src.service.onchain.collectors import uniswap
from src.service.onchain.collectors.base import (
    SECTION_IDENTITY,
    STATUS_OK,
    STATUS_PARTIAL,
    BuildContext,
    SectionResult,
)
from src.service.onchain.config import get_dexscreener_slug
from src.service.onchain.diff import failed_field

logger = logging.getLogger('Onchain identity')

UNAVAILABLE = 'unavailable'

# How far either side of the provider's pair-creation timestamp the creation log
# is searched for. Wide enough to absorb a provider clock that disagrees with the
# chain by hours, narrow enough that the scan is a few hundred windows rather
# than the chain's whole history -- which is what made predict-fwa's
# `PoolCreated` unresolvable: 57M blocks at the public node's window budget
# rate-limits long before it reaches the pool.
CREATION_SEARCH_MARGIN_SECONDS = 6 * 60 * 60

# Carried forward from a prior identity section instead of re-read: each is a
# fact about a pool that was true when it was created and cannot change.
IMMUTABLE_FIELDS = (
    'creation_block',
    'creation_tx',
    'currency0',
    'currency1',
    'fee',
    'tick_spacing',
    'hooks',
    'key_matches',
)


async def collect(context: BuildContext, previous: Optional[Dict[str, Any]] = None) -> SectionResult:
    fields: Dict[str, Any] = {}
    # An immutable fact that could NOT be read is not a fact. Carrying
    # `unavailable` forward made one bad night permanent: predict-fwa's creation
    # block failed once, was carried by every later build, and no re-run could
    # recover it -- which also left the health section walking from genesis,
    # because block 0 is where an unresolved creation block sends it.
    carried = {
        name: value
        for name, value in (previous or {}).items()
        if name in IMMUTABLE_FIELDS and value != UNAVAILABLE
    }

    pair, raw = await _provider_pair(context)
    fields.update(_provider_fields(pair))

    token_address = fields.get('token_address')
    if token_address:
        context.record_http(
            f'dexscreener:latest/dex/pairs/{get_dexscreener_slug(context.chain.chain_id)}'
            f'/{context.project.pool_ref}',
            raw,
            'dexscreener',
        )

    pool_count, per_pool = await _every_pool_for_token(context, token_address)
    fields['pool_count'] = pool_count
    fields['pools'] = per_pool

    version = fields.get('version')
    if version == 'v4':
        fields.update(await _resolve_v4(context, carried, fields))
    else:
        fields.update(await _resolve_v3(context, carried, fields))

    fields.update(await _token_facts(context, token_address))
    explorer_fields = await _deployer_triple(context, token_address)
    fields.update(explorer_fields)

    status = STATUS_PARTIAL if context.explorer.failed else STATUS_OK
    if any(value == UNAVAILABLE for name, value in fields.items() if name in
           ('fee', 'tick_spacing', 'hooks', 'key_matches')):
        status = STATUS_PARTIAL
    # Publishing these to the other three collectors is the BUILDER's job, not
    # this one's -- see `_run_section`. A collector returns a section; nothing
    # else.
    return SectionResult(
        name=SECTION_IDENTITY,
        status=status,
        fields=fields,
        error_class=context.explorer.error_class if context.explorer.failed else None,
        evidence_ids=list(context.evidence_ids),
    )


async def _provider_pair(context: BuildContext) -> Tuple[Any, Any]:
    slug = get_dexscreener_slug(context.chain.chain_id)
    pairs, raw = await context.dexscreener.get_pairs_raw(
        chain_id=slug, pair_addresses=[context.project.pool_ref]
    )
    if not pairs:
        raise ProviderHasNoPairError(
            f'the provider returned no pair for {context.project.key}'
        )
    return pairs[0], raw


class ProviderHasNoPairError(RuntimeError):
    """The provider has no record of the operator's pool reference.

    Its own error class rather than an IndexError, because the operator's next
    action differs: an empty provider answer means the reference is wrong or the
    pool is too new to be indexed, and neither is a transport failure to retry.
    """


def _provider_fields(pair: Any) -> Dict[str, Any]:
    labels = [str(label).lower() for label in (pair.labels or [])]
    version = 'v4' if 'v4' in labels else ('v3' if 'v3' in labels else (labels[0] if labels else UNAVAILABLE))
    info = pair.info
    return {
        'dex': pair.dexId,
        'version': version,
        'pool_ref': pair.pairAddress,
        'token_address': str(pair.baseToken.address).lower(),
        'token_symbol': pair.baseToken.symbol,
        'token_name': pair.baseToken.name,
        'quote_address': str(pair.quoteToken.address).lower(),
        'quote_symbol': pair.quoteToken.symbol,
        'pair_created_at': pair.pairCreatedAt,
        # Stored as CANDIDATE sources by the builder and read by nothing in 1a
        # (design P5): this is the structural hop phase 1b will take.
        'published_links': sorted(
            {link.url for link in (info.websites if info else [])}
            | {link.url for link in (info.socials if info else [])}
        ) if info else [],
    }


async def creation_search_bounds(
    client: Any,
    created_at_ms: Any,
    *,
    head: int,
    head_timestamp: int,
    constants: Any,
) -> Tuple[int, int, int]:
    """`(from_block, to_block, header_reads)` to look for a pool's creation log in.

    A pool is created once, at the moment the provider dates its pair, so the
    creation log sits inside a few hours of that timestamp. Bounding BOTH ends
    matters: a scan bounded only below still walks from the pool's creation to
    head, which is the same tens of millions of blocks it was already failing on.

    The lower bound is found by binary search over block headers (~25 reads, once
    in the pool's life); the upper bound is derived from it arithmetically,
    because the block rate is accurate over the twelve hours between them even
    where it has drifted over the chain's history.

    Falls back to the whole chain when the provider dates nothing, which is the
    old behaviour and the honest one -- a bound invented from no timestamp could
    exclude the very log being looked for.
    """
    if not created_at_ms:
        return 0, head, 0
    target = int(created_at_ms) // 1000 - CREATION_SEARCH_MARGIN_SECONDS
    if target <= 0:
        return 0, head, 0
    from_block, _timestamp, reads = await chain.find_block_at_or_before(
        client,
        target,
        high_block=head,
        high_timestamp=head_timestamp,
        constants=constants,
    )
    span = int(2 * CREATION_SEARCH_MARGIN_SECONDS * constants.blocks_per_second)
    return from_block, min(head, from_block + span), reads


async def _creation_bounds(context: BuildContext, fields: Dict[str, Any]) -> Tuple[int, int]:
    """`creation_search_bounds` for this build, with its header reads billed."""
    from src.service.onchain.config import get_chain_constants

    from_block, to_block, reads = await creation_search_bounds(
        context.state_client,
        fields.get('pair_created_at'),
        head=context.block,
        head_timestamp=context.pinned.timestamp,
        constants=get_chain_constants(context.chain.chain_id),
    )
    if reads:
        context.charge(
            context.state_client.endpoint.kind,
            reads,
            methods=['eth_getBlockByNumber'] * reads,
        )
    logger.info(
        'creation log for %s searched over blocks %s-%s after %s header reads',
        context.project.key, from_block, to_block, reads,
    )
    return from_block, to_block


async def _every_pool_for_token(
    context: BuildContext, token_address: Optional[str]
) -> Tuple[Any, List[Dict[str, Any]]]:
    """Every pool the provider knows for this token.

    Feeds the health section's fragmentation pair: Touch Grass had 21 pools and
    ZZZ 30 on 2026-09-06, most under $10k, so "the pool" is the operator's
    reference and not the token's only market (design DG-7).
    """
    if not token_address:
        return UNAVAILABLE, []
    slug = get_dexscreener_slug(context.chain.chain_id)
    pairs, raw = await context.dexscreener.get_token_pairs_raw(
        chain_id=slug, token_address=token_address
    )
    context.record_http(
        f'dexscreener:token-pairs/v1/{slug}/{token_address}', raw, 'dexscreener'
    )
    return len(pairs), [
        {
            'dex': pair.dexId,
            'version': ('v4' if 'v4' in [str(x).lower() for x in (pair.labels or [])]
                        else 'v3' if 'v3' in [str(x).lower() for x in (pair.labels or [])]
                        else None),
            'reference': pair.pairAddress,
            'liquidity_usd': pair.liquidity.usd if pair.liquidity else None,
        }
        for pair in pairs
    ]


async def _resolve_v3(
    context: BuildContext, carried: Dict[str, Any], provider: Dict[str, Any]
) -> Dict[str, Any]:
    """v3: the pool has an address, so the getters answer directly."""
    pool = context.project.pool_ref
    fields: Dict[str, Any] = {'pool_address': pool.lower(), 'pool_id': None}
    names = ['factory', 'token0', 'token1', 'fee', 'tickSpacing']
    calls = [(pool, uniswap.call_data(name)) for name in names]
    results = await context.state_client.batch_call(calls, context.block)
    decoded: Dict[str, Any] = {}
    for name, (data, raw) in zip(names, results):
        context.record_jsonrpc(raw)
        decoded[name] = abi.decode_single(uniswap.V3_POOL_GETTERS[name][1], data)

    registry_factory = context.chain.uniswap['v3_factory'].lower()
    fields['factory'] = str(decoded['factory']).lower()
    fields['factory_matches'] = fields['factory'] == registry_factory
    fields['currency0'] = str(decoded['token0']).lower()
    fields['currency1'] = str(decoded['token1']).lower()
    fields['fee'] = int(decoded['fee'])
    fields['tick_spacing'] = int(decoded['tickSpacing'])
    fields['hooks'] = None
    fields['key_matches'] = fields['factory_matches']

    if 'creation_block' in carried:
        fields['creation_block'] = carried['creation_block']
        fields['creation_tx'] = carried.get('creation_tx')
    else:
        fields.update(await _v3_creation(context, fields, provider))
    return fields


async def _v3_creation(
    context: BuildContext, fields: Dict[str, Any], provider: Dict[str, Any]
) -> Dict[str, Any]:
    """The factory's `PoolCreated` for this exact (token0, token1, fee).

    One log query, once in the pool's life, bounded to a few hours either side of
    the timestamp the provider dates the pair at (`_creation_bounds`) -- an
    unbounded scan of this chain's history is what left predict-fwa's creation
    block `unavailable` through round 2. The design's fallback -- a binary
    search of `eth_getCode` over the pool address -- is deliberately NOT
    implemented here: it costs ~25 archive reads to answer a question a single
    public-RPC query answers, and `unavailable` is a correct dossier value while
    a wrong creation block would silently truncate every later transfer fetch.
    """
    from src.service.project_monitor.logs import LogQuery, fetch_window

    factory = context.chain.uniswap['v3_factory']
    query = LogQuery(
        name=f'pool_created:{context.project.key}',
        addresses=[factory],
        topics=[
            uniswap.V3_POOL_CREATED.topic0,
            '0x' + '0' * 24 + fields['currency0'].removeprefix('0x'),
            '0x' + '0' * 24 + fields['currency1'].removeprefix('0x'),
            '0x' + f'{int(fields["fee"]):064x}',
        ],
        spec=uniswap.V3_POOL_CREATED,
    )
    from_block, to_block = await _creation_bounds(context, provider)
    try:
        logs, raws = await fetch_window(context.log_client, query, from_block, to_block)
        context.charge_logs(len(raws))
    except EvmClientError as exc:
        logger.warning('v3 creation log unavailable: %s', type(exc).__name__)
        return {'creation_block': UNAVAILABLE, 'creation_tx': UNAVAILABLE}
    if not logs:
        return {'creation_block': UNAVAILABLE, 'creation_tx': UNAVAILABLE}
    return {
        'creation_block': int(logs[0]['blockNumber'], 16),
        'creation_tx': logs[0]['transactionHash'],
    }


async def _resolve_v4(
    context: BuildContext, carried: Dict[str, Any], provider: Dict[str, Any]
) -> Dict[str, Any]:
    """v4: existence from the state view, the key from the `Initialize` log."""
    pool_id = context.project.pool_ref
    state_view = context.chain.uniswap['v4_state_view']
    fields: Dict[str, Any] = {'pool_id': pool_id.lower(), 'pool_address': None}

    slot0_data, slot0_raw = await context.state_client.call(
        state_view, uniswap.call_data('getSlot0', ['bytes32'], [pool_id]), context.block
    )
    context.record_jsonrpc(slot0_raw)
    sqrt_price, tick, _protocol_fee, lp_fee = abi.decode(
        ['uint160', 'int24', 'uint24', 'uint24'], slot0_data
    )
    fields['sqrt_price_x96'] = str(sqrt_price)
    fields['tick'] = int(tick)
    fields['lp_fee'] = int(lp_fee)
    # An unknown pool id returns zeros rather than reverting, so this IS the
    # existence check (design D3).
    fields['exists'] = sqrt_price != 0
    fields['factory'] = context.chain.uniswap['v4_pool_manager'].lower()
    fields['factory_matches'] = fields['exists']

    if all(name in carried for name in ('currency0', 'currency1', 'fee', 'tick_spacing', 'hooks')):
        fields.update({name: carried[name] for name in IMMUTABLE_FIELDS if name in carried})
        return fields

    key = await _v4_key(context, pool_id, provider)
    fields.update(key)
    return fields


async def _v4_key(
    context: BuildContext, pool_id: str, provider: Dict[str, Any]
) -> Dict[str, Any]:
    """The pool key, from the pool manager's `Initialize` log for this id.

    The design names a fallback -- the v4 position manager's `poolKeys(bytes25)`
    getter -- and records it as UNVERIFIED on this deployment. It is not called
    here: a getter whose presence has never been confirmed would answer either a
    key or a revert, and a revert inside the state batch would fail the whole
    identity section for a field that is allowed to read `unavailable`. Without
    the log, the four key fields and `key_matches` read `unavailable` and the
    section is `partial`, which is what the design specifies.
    """
    from src.service.project_monitor.logs import LogQuery, fetch_window

    query = LogQuery(
        name=f'v4_initialize:{context.project.key}',
        addresses=[context.chain.uniswap['v4_pool_manager']],
        topics=[uniswap.V4_INITIALIZE.topic0, pool_id],
        spec=uniswap.V4_INITIALIZE,
    )
    from_block, to_block = await _creation_bounds(context, provider)
    try:
        logs, raws = await fetch_window(context.log_client, query, from_block, to_block)
        context.charge_logs(len(raws))
    except EvmClientError as exc:
        logger.warning('v4 Initialize log unavailable: %s', type(exc).__name__)
        logs = []
    if not logs:
        return {
            'currency0': UNAVAILABLE,
            'currency1': UNAVAILABLE,
            'fee': UNAVAILABLE,
            'tick_spacing': UNAVAILABLE,
            'hooks': UNAVAILABLE,
            'key_matches': UNAVAILABLE,
            'creation_block': UNAVAILABLE,
            'creation_tx': UNAVAILABLE,
        }
    log = logs[0]
    decoded = abi.decode_log(uniswap.V4_INITIALIZE, log)
    recomputed = uniswap.compute_v4_pool_id(
        decoded['currency0'],
        decoded['currency1'],
        int(decoded['fee']),
        int(decoded['tickSpacing']),
        decoded['hooks'],
    )
    return {
        'currency0': str(decoded['currency0']).lower(),
        'currency1': str(decoded['currency1']).lower(),
        'fee': int(decoded['fee']),
        'tick_spacing': int(decoded['tickSpacing']),
        'hooks': str(decoded['hooks']).lower(),
        'key_matches': recomputed.lower() == pool_id.lower(),
        'recomputed_pool_id': recomputed,
        'creation_block': int(log['blockNumber'], 16),
        'creation_tx': log['transactionHash'],
    }


async def _token_facts(
    context: BuildContext, token_address: Optional[str]
) -> Dict[str, Any]:
    """`name`, `symbol`, `decimals` and the runtime code's identity.

    `code_hash` is what makes two projects sharing a launcher visible without
    reading either one's source: Touch Grass and ZZZ share one runtime code, Not
    A Website and Predict FWA another (read 2026-09-06).
    """
    if not token_address:
        return {'code_size': UNAVAILABLE, 'code_hash': UNAVAILABLE}
    from market_data_library.core.onchain.evm.keccak import keccak256

    calls = [
        (token_address, uniswap.call_data('name')),
        (token_address, uniswap.call_data('symbol')),
        (token_address, uniswap.call_data('decimals')),
    ]
    results = await context.state_client.batch_call(calls, context.block)
    for _, raw in results:
        context.record_jsonrpc(raw)
    code, code_raw = await context.state_client.get_code(token_address, context.block)
    context.record_jsonrpc(code_raw)
    body = bytes.fromhex(code.removeprefix('0x'))
    return {
        'onchain_name': decode_string(results[0][0]),
        'onchain_symbol': decode_string(results[1][0]),
        'decimals': int(abi.decode_single('uint8', results[2][0])),
        'code_size': len(body),
        'code_hash': '0x' + keccak256(body).hex(),
    }


async def _deployer_triple(
    context: BuildContext, token_address: Optional[str]
) -> Dict[str, Any]:
    """The explorer sub-unit: who created the token, and is its source readable.

    A triple rather than one "deployer", because a launchpad token is created by
    a launcher CONTRACT in a transaction some person sent: `creator` is what
    executed the create, `creation_tx_from` is the person, and `creation_tx_to`
    is the launcher they called. Collapsing them loses exactly the distinction
    that says whether a project deployed its own token.
    """
    unavailable = context.explorer.field_state()
    blank: Dict[str, Any] = {
        'deployer': (
            failed_field(context.explorer.error_class or 'unknown')
            if context.explorer.failed else UNAVAILABLE
        ),
        'verified': unavailable,
        'contract_name': unavailable,
        'file_path': unavailable,
    }
    if not token_address:
        return blank

    address = await context.explorer.address(token_address)
    if address is None:
        return _explorer_blank(context, blank)
    context.record_http(
        f'blockscout:addresses/{token_address}', address.raw, 'blockscout'
    )

    creation_tx = address.creation_transaction
    tx_from: Any = UNAVAILABLE
    tx_to: Any = UNAVAILABLE
    if creation_tx:
        transaction = await context.explorer.transaction(creation_tx)
        if transaction is not None:
            context.record_http(
                f'blockscout:transactions/{creation_tx}', transaction.raw, 'blockscout'
            )
            tx_from = (transaction.from_address.hash if transaction.from_address else None) or UNAVAILABLE
            # Null `to` is the case this triple exists to see: a raw contract
            # creation, as opposed to a call into a launcher.
            tx_to = (transaction.to_address.hash if transaction.to_address else None) or 'contract_creation'

    contract = await context.explorer.contract(token_address)
    if contract is not None:
        context.record_http(
            f'blockscout:smart-contracts/{token_address}', contract.raw, 'blockscout'
        )

    if context.explorer.failed:
        return _explorer_blank(context, blank)

    return {
        'deployer': {
            'creator': (address.creator_address_hash or UNAVAILABLE),
            'creation_tx': creation_tx or UNAVAILABLE,
            'creation_tx_from': tx_from,
            'creation_tx_to': tx_to,
        },
        'verified': bool(contract.is_verified) if contract is not None else False,
        'contract_name': (contract.name if contract is not None else None) or UNAVAILABLE,
        'file_path': (contract.file_path if contract is not None else None) or UNAVAILABLE,
    }


def _explorer_blank(context: BuildContext, blank: Dict[str, Any]) -> Dict[str, Any]:
    if not context.explorer.failed:
        return blank
    marker = failed_field(context.explorer.error_class or 'unknown')
    return {name: dict(marker) for name in blank}
