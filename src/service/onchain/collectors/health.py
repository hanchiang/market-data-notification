"""Onchain health: every gameable metric beside the counterpart that guards it (A4).

The rule this section exists to enforce is the requirement's Signal quality
constraint: no gameable metric appears without its paired counterpart on the same
row, each pair names the gaming mode it guards against, and the liquidity row
states the pool type its custody read is defined for. That is why the section
stores PAIRS as its unit rather than fields -- a metric here cannot be rendered
without its counterpart, because there is nowhere to put it.

Both members of a pair are measured over the **same trailing 24 hours**, as the
block interval between the run's binary-searched boundary block and its pinned
block. The provider's figures are trailing-24h figures at query time, so a
counterpart measured over "since the last build" would compare a day against
whatever the cron interval happened to be -- measuring cadence, not gaming.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

from market_data_library.core.onchain.evm import EvmClientError, abi

from src.service.onchain.collectors import transfers, uniswap
from src.service.onchain.collectors.base import (
    SECTION_ONCHAIN_HEALTH,
    STATUS_OK,
    BuildContext,
    SectionResult,
)
from src.service.onchain.config import get_dexscreener_slug

logger = logging.getLogger('Onchain health')

UNAVAILABLE = 'unavailable'


class HolderDerivationMismatchError(RuntimeError):
    """A derived balance disagreed with `balanceOf` at the pinned block.

    Fails the whole section rather than one field: the derivation is the base of
    the holder table, the top-ten share, the new-versus-returning split and the
    activity counts, so a mismatch means none of them is trustworthy. Nothing
    downstream is reported from a base that is known wrong (design, failure units).
    """


async def collect(context: BuildContext) -> SectionResult:
    token_address = context.identity.get('token_address')
    if not token_address:
        from src.service.onchain.collectors.contract_safety import IdentityUnresolvedError

        raise IdentityUnresolvedError('onchain health needs a resolved token address')

    token_entity_id = context.token_entity_id()
    pool_entity_id = context.pool_entity_id()
    creation_block = _creation_block(context)

    outcome = await transfers.advance_transfers(
        context.repository,
        context.log_client,
        token_entity_id=token_entity_id,
        token_address=token_address,
        creation_block=creation_block,
        to_block=context.block,
    )

    holders = transfers.holder_summary(context.repository, token_entity_id)
    ok, mismatches = await transfers.check_derivation(
        context.state_client,
        token_address=token_address,
        block=context.block,
        holders=holders['top_holders'],
    )
    if not ok:
        logger.error(
            'holder derivation mismatch for %s on %s address(es)',
            context.project.key,
            len(mismatches),
        )
        raise HolderDerivationMismatchError(
            f'{len(mismatches)} derived balances disagree with balanceOf'
        )
    # Token economics reads this before it sums the same rows. Not committed
    # here: the builder commits the section as one unit, so a health failure
    # after this point takes the mark back with it.
    context.repository.set_fetch_cursor(
        token_entity_id, transfers.STREAM_DERIVATION_VERIFIED, context.block
    )

    provider = await _provider_snapshot(context)
    window_start = context.pinned.window_start_block
    window_end = context.block

    active = context.repository.active_addresses(token_entity_id, window_start, window_end)
    pool_addresses = _pool_touching_addresses(context)
    counterparties = context.repository.pool_counterparties(
        token_entity_id, pool_addresses, window_start, window_end
    )
    split = context.repository.new_versus_returning(token_entity_id, window_start, window_end)
    custody = await _custody(context, pool_entity_id, creation_block)

    fields: Dict[str, Any] = {
        'window': {
            'from_block': window_start,
            'to_block': window_end,
            'from_timestamp': context.pinned.window_start_timestamp,
            'to_timestamp': context.pinned.timestamp,
            'basis': 'trailing_24h_block_interval',
        },
        'transfer_rows': context.repository.transfer_row_count(token_entity_id),
        'transfer_fetch': {
            'from_block': outcome.from_block,
            'to_block': outcome.to_block,
            'fetched': outcome.fetched,
            'inserted': outcome.inserted,
            'windows': outcome.windows,
            'resumed': outcome.resumed,
        },
        # `holder_count` and `top_holders` are deliberately NOT stored at this
        # level. Both are gameable, A4 forbids a gameable metric appearing
        # without its counterpart on the same row, and the report renders every
        # non-`pairs` field on a line of its own -- so storing them here put an
        # unguarded holder count in the dossier beside the guarded one. They live
        # inside the `holder_count` pair, which carries the new-versus-returning
        # split and the top-ten share that guard them.
        'derivation_check': 'ok',
        # Price and FDV are lone metrics and stay OUT of `pairs` by design; they
        # are stored here so the page has a valuation tile and the diff carries
        # them (UX brief, ruled 2026-09-12). The provider cannot backfill them,
        # so a build that drops them loses the point for good.
        'price_usd': _float_or_none(provider.get('price_usd')),
        'fdv_usd': _float_or_none(provider.get('fdv')),
        'pairs': _pairs(
            provider=provider,
            active=active,
            counterparties=counterparties,
            holders=holders,
            split=split,
            custody=custody,
            context=context,
        ),
    }
    return SectionResult(
        name=SECTION_ONCHAIN_HEALTH,
        status=STATUS_OK,
        fields=fields,
        evidence_ids=list(context.evidence_ids),
    )


def _float_or_none(value: Any) -> Optional[float]:
    """The provider quotes `priceUsd` as a string and `fdv` as a number."""
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _creation_block(context: BuildContext) -> int:
    """Where a first transfer fetch starts.

    Falls back to block 0 when identity could not resolve one: fetching from
    genesis is slow and correct, while guessing a later start would silently drop
    every transfer before it and leave a holder table that passes no check but
    the one it would then also fail.
    """
    value = context.identity.get('creation_block')
    return int(value) if isinstance(value, int) else 0


async def _provider_snapshot(context: BuildContext) -> Dict[str, Any]:
    slug = get_dexscreener_slug(context.chain.chain_id)
    pairs, raw = await context.dexscreener.get_pairs_raw(
        chain_id=slug, pair_addresses=[context.project.pool_ref]
    )
    context.record_http(
        f'dexscreener:latest/dex/pairs/{slug}/{context.project.pool_ref}',
        raw,
        'dexscreener',
    )
    if not pairs:
        return {}
    pair = pairs[0]
    txns = pair.txns.h24 if pair.txns else None
    return {
        'volume_h24_usd': pair.volume.h24 if pair.volume else None,
        'txns_h24': (txns.buys + txns.sells) if txns else None,
        'liquidity_usd': pair.liquidity.usd if pair.liquidity else None,
        'price_usd': pair.priceUsd,
        'fdv': pair.fdv,
    }


def _pool_touching_addresses(context: BuildContext) -> List[str]:
    """What "touching the pool" means for this pool type (companion doc).

    v3: the pool has an address and is the counterparty of every swap. v4: the
    pool has no address, so the singleton pool manager holds the tokens and the
    hook, when there is one, can also move them -- both are the addresses a swap
    against this pool shows up against in the token's transfer log.
    """
    version = context.identity.get('version')
    if version == 'v4':
        addresses = [context.chain.uniswap['v4_pool_manager']]
        hooks = context.identity.get('hooks')
        if isinstance(hooks, str) and hooks.startswith('0x') and int(hooks, 16) != 0:
            addresses.append(hooks)
        return addresses
    address = context.identity.get('pool_address')
    return [address] if address else []


async def _custody(
    context: BuildContext, pool_entity_id: int, creation_block: int
) -> Dict[str, Any]:
    """On-chain liquidity and who holds it, per pool type (design D5)."""
    version = context.identity.get('version')
    if version == 'v4':
        return await _custody_v4(context, pool_entity_id, creation_block)
    return await _custody_v3(context, pool_entity_id, creation_block)


async def _fetch_events(
    context: BuildContext, query, from_block: int, to_block: Optional[int] = None
) -> List[Dict[str, Any]]:
    from market_data_library.core.onchain.evm import EvmClientError
    from src.service.project_monitor.logs import fetch_window

    try:
        logs, _ = await fetch_window(
            context.log_client, query, from_block, context.block if to_block is None else to_block
        )
        return logs
    except EvmClientError as exc:
        logger.warning('%s log window failed: %s', query.name, type(exc).__name__)
        raise


# The owner join reads the position manager, which is CHAIN-WIDE: every project's
# mints land in the same ERC-721 Transfer stream. Scanning it from the pool's
# creation block to head is what a naive implementation does, and on this chain it
# does not finish -- the launchpad's own volume drives `eth_getLogs` into its
# 10,000-match cap, so the adaptive window narrows to a few thousand blocks and
# then has millions to walk at that width.
#
# It is also unnecessary. The join is "the NFT minted in the SAME TRANSACTION as
# this pool's liquidity event", so a Transfer in a block with no such event cannot
# match by construction. We therefore fetch the NFT stream only over the blocks the
# pool-specific events actually occupy. Blocks within NFT_RANGE_GAP_BLOCKS of each
# other are coalesced into one range, trading a few irrelevant logs for far fewer
# round trips; a pool with no liquidity events in the window issues no query at all.
NFT_RANGE_GAP_BLOCKS = 5_000


def _event_ranges(events: List[Dict[str, Any]]) -> List[tuple]:
    """Coalesce the blocks these logs sit in into (from, to) fetch ranges."""
    blocks = sorted({int(log['blockNumber'], 16) for log in events})
    ranges: List[tuple] = []
    for block in blocks:
        if ranges and block - ranges[-1][1] <= NFT_RANGE_GAP_BLOCKS:
            ranges[-1] = (ranges[-1][0], block)
        else:
            ranges.append((block, block))
    return ranges


async def _fetch_owner_transfers(
    context: BuildContext, name: str, manager: str, events: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """ERC-721 Transfers on `manager`, restricted to the blocks `events` occupy."""
    from src.service.project_monitor.logs import LogQuery

    query = LogQuery(name, [manager], [uniswap.ERC721_TRANSFER.topic0], uniswap.ERC721_TRANSFER)
    collected: List[Dict[str, Any]] = []
    for low, high in _event_ranges(events):
        collected.extend(await _fetch_events(context, query, low, high))
    return collected


async def _v3_token_ids(
    context: BuildContext, manager: str, events: List[Dict[str, Any]]
) -> Dict[str, List[Tuple[int, int]]]:
    """`{transaction hash: [(log index, NFT token id), ...]}` from the v3
    manager's own liquidity events.

    Restricted to the blocks the pool's events occupy for the same reason the
    ERC-721 fetch is (see `_fetch_owner_transfers`): the manager is chain-wide,
    and only a transaction carrying this pool's `Mint`/`Burn` can contribute.

    Every event is kept with its log index, not one per transaction: a multicall
    minting two positions emits two `IncreaseLiquidity`, and one id per
    transaction would net both pool events under the later of them.
    `attribute_owners` matches each pool event to the manager event that follows
    it.
    """
    from src.service.project_monitor.logs import LogQuery

    found: Dict[str, List[Tuple[int, int]]] = {}
    for spec, label in (
        (uniswap.V3_INCREASE_LIQUIDITY, 'increase'),
        (uniswap.V3_DECREASE_LIQUIDITY, 'decrease'),
    ):
        query = LogQuery(
            f'v3_{label}:{context.project.key}', [manager], [spec.topic0], spec
        )
        for low, high in _event_ranges(events):
            for log in await _fetch_events(context, query, low, high):
                fields = abi.decode_log(spec, log)
                found.setdefault(str(log['transactionHash']).lower(), []).append(
                    (int(log['logIndex'], 16), int(fields['tokenId']))
                )
    return {tx: sorted(entries) for tx, entries in found.items()}


async def _custody_v3(
    context: BuildContext, pool_entity_id: int, creation_block: int
) -> Dict[str, Any]:
    from src.service.project_monitor.logs import LogQuery

    pool = context.identity.get('pool_address')
    if not pool:
        return {'pool_type': 'v3', 'state': UNAVAILABLE}
    cursor = context.repository.get_fetch_cursor(pool_entity_id, transfers.STREAM_POSITION)
    from_block = creation_block if cursor is None else cursor + 1

    mints = await _fetch_events(
        context,
        LogQuery(f'v3_mint:{context.project.key}', [pool], [uniswap.V3_MINT.topic0], uniswap.V3_MINT),
        from_block,
    )
    burns = await _fetch_events(
        context,
        LogQuery(f'v3_burn:{context.project.key}', [pool], [uniswap.V3_BURN.topic0], uniswap.V3_BURN),
        from_block,
    )
    manager = context.chain.uniswap['v3_position_manager']
    nft = await _fetch_owner_transfers(
        context, f'v3_nft:{context.project.key}', manager, mints + burns
    )
    # v3's pool events name only the manager, and a decrease emits no ERC-721
    # transfer, so the manager's own token-id events are the only thing that ties
    # a withdrawal back to the position it came out of.
    token_id_by_tx = await _v3_token_ids(context, manager, mints + burns)
    rows = uniswap.attribute_owners(
        uniswap.normalise_v3_events(mints, burns), nft, token_id_by_tx
    )
    context.repository.insert_position_events(pool_entity_id, [row.as_row() for row in rows])
    context.repository.set_fetch_cursor(
        pool_entity_id, transfers.STREAM_POSITION, context.block
    )

    liquidity_data, raw = await context.state_client.call(
        pool, uniswap.call_data('liquidity'), context.block
    )
    context.record_jsonrpc(raw)
    return await _custody_shape(
        context,
        pool_entity_id,
        'v3',
        int(abi.decode_single('uint128', liquidity_data)),
        manager,
    )


async def _custody_v4(
    context: BuildContext, pool_entity_id: int, creation_block: int
) -> Dict[str, Any]:
    from src.service.project_monitor.logs import LogQuery

    pool_id = context.identity.get('pool_id')
    if not pool_id:
        return {'pool_type': 'v4', 'state': UNAVAILABLE}
    cursor = context.repository.get_fetch_cursor(pool_entity_id, transfers.STREAM_POSITION)
    from_block = creation_block if cursor is None else cursor + 1

    modify = await _fetch_events(
        context,
        LogQuery(
            f'v4_modify:{context.project.key}',
            [context.chain.uniswap['v4_pool_manager']],
            [uniswap.V4_MODIFY_LIQUIDITY.topic0, pool_id],
            uniswap.V4_MODIFY_LIQUIDITY,
        ),
        from_block,
    )
    nft = await _fetch_owner_transfers(
        context,
        f'v4_nft:{context.project.key}',
        context.chain.uniswap['v4_position_manager'],
        modify,
    )
    rows = uniswap.attribute_owners(
        uniswap.normalise_v4_events(modify, context.chain.uniswap['v4_position_manager']),
        nft,
    )
    context.repository.insert_position_events(pool_entity_id, [row.as_row() for row in rows])
    context.repository.set_fetch_cursor(
        pool_entity_id, transfers.STREAM_POSITION, context.block
    )

    data, raw = await context.state_client.call(
        context.chain.uniswap['v4_state_view'],
        uniswap.call_data('getLiquidity', ['bytes32'], [pool_id]),
        context.block,
    )
    context.record_jsonrpc(raw)
    return await _custody_shape(
        context,
        pool_entity_id,
        'v4',
        int(abi.decode_single('uint128', data)),
        context.chain.uniswap['v4_position_manager'],
    )


async def _custody_shape(
    context: BuildContext,
    pool_entity_id: int,
    pool_type: str,
    pool_liquidity: int,
    position_manager: str,
) -> Dict[str, Any]:
    stored = context.repository.get_position_events(pool_entity_id)
    rows = uniswap.rows_from_store(stored)
    positions = uniswap.net_positions(rows)
    positions = uniswap.apply_resolved_owners(
        positions, await _resolve_owners(context, position_manager, positions)
    )
    shares = uniswap.custody_shares(positions, _owner_classes(context, positions))
    return {
        'pool_type': pool_type,
        'pool_liquidity': str(pool_liquidity),
        'open_positions': len(positions),
        **shares,
    }


async def _resolve_owners(
    context: BuildContext, manager: str, positions
) -> Dict[int, str]:
    """`ownerOf(tokenId)` at the pinned block for every open position's NFT.

    This is what makes "the position moved when the NFT was sold" true. The
    log-derived holder cannot be trusted for it: the ERC-721 stream is fetched
    only over blocks carrying a liquidity event, so a sale in any other block is
    invisible. One batched read over the OPEN positions is cheap -- twelve for
    touch-grass, not one per historical event.

    A burned NFT REVERTS -- "ERC721: owner query for nonexistent token" -- and a
    revert inside a JSON-RPC batch fails the whole batch, not one member of it
    (observed on predict-fwa 2026-09-08, where it failed the entire health
    section). `expect_value=False` does not help: it covers an empty `0x` result,
    not an error object. So the batch is tried first for the common case and a
    failure falls back to one call per token id, where a revert costs only that
    token its resolved owner. An unresolved token keeps its provisional holder.
    """
    token_ids = [
        token_id for position in positions for token_id in position.nft_token_ids
    ]
    if not token_ids:
        return {}
    calls = [(manager, uniswap.owner_of_call_data(token_id)) for token_id in token_ids]
    owners: Dict[int, str] = {}
    try:
        results = await context.state_client.batch_call(
            calls, context.block, expect_value=False
        )
    except EvmClientError:
        logger.info(
            'owner batch reverted for %s; falling back to %s single reads',
            context.project.key,
            len(token_ids),
        )
        results = []
        for call in calls:
            try:
                results.append(
                    (await context.state_client.call(*call, context.block, expect_value=False))
                )
            except EvmClientError:
                results.append((None, None))
    for token_id, (data, raw) in zip(token_ids, results):
        if raw is not None:
            context.record_jsonrpc(raw)
        if not data or data == '0x':
            continue
        owners[token_id] = str(abi.decode_single('address', data)).lower()
    return owners


def _owner_classes(context: BuildContext, positions) -> Dict[str, str]:
    """`project`, `locker`, `contract` or `eoa` for each liquidity owner.

    Only `project` and `locker` are decided here, from addresses the dossier
    already knows. Everything else stays `eoa` by default rather than being
    guessed: distinguishing a contract from an account needs an `eth_getCode`
    per owner, which is a per-owner state read this section does not have a
    budget for, and calling an unknown address `contract` without checking would
    be a claim nothing supports.
    """
    project_addresses = set()
    deployer = context.identity.get('deployer')
    if isinstance(deployer, dict):
        for value in deployer.values():
            if isinstance(value, str) and value.startswith('0x') and len(value) == 42:
                project_addresses.add(value.lower())
    hooks = context.identity.get('hooks')
    if isinstance(hooks, str) and hooks.startswith('0x') and len(hooks) == 42:
        project_addresses.add(hooks.lower())

    classes = {address: 'project' for address in project_addresses}
    for locker in context.chain.lockers:
        classes[str(locker).lower()] = 'locker'
    return classes


def _pairs(
    *,
    provider: Dict[str, Any],
    active: int,
    counterparties: int,
    holders: Dict[str, Any],
    split: Dict[str, int],
    custody: Dict[str, Any],
    context: BuildContext,
) -> List[Dict[str, Any]]:
    """The five pairs, each carrying both members and the mode it guards against.

    Built as one list so a gameable metric structurally cannot be stored without
    its counterpart: there is no field for a lone `volume_h24`.
    """
    pool_share = _primary_pool_share(context)
    return [
        {
            'metric': 'dex_volume_h24_usd',
            'value': provider.get('volume_h24_usd'),
            'source': 'dex_provider',
            'counterpart': 'active_addresses_24h',
            'counterpart_value': active,
            'counterpart_source': 'transfer_history',
            'guards_against': 'wash_trading: volume without new counterparties',
        },
        {
            'metric': 'dex_trades_h24',
            'value': provider.get('txns_h24'),
            'source': 'dex_provider',
            'counterpart': 'pool_counterparties_24h',
            'counterpart_value': counterparties,
            'counterpart_source': 'transfer_history',
            'guards_against': 'bot_churn: trades without distinct counterparties',
        },
        {
            'metric': 'holder_count',
            'value': holders['holder_count'],
            'source': 'transfer_history',
            'counterpart': 'new_vs_returning_24h_and_top_ten_share',
            'counterpart_value': {
                'new': split['new'],
                'returning': split['returning'],
                'top_ten_share': holders['top_ten_share'],
            },
            'counterpart_source': 'transfer_history',
            'guards_against': 'wallet_splitting: one holder becoming twenty',
        },
        {
            'metric': 'liquidity_usd',
            'value': provider.get('liquidity_usd'),
            'source': 'dex_provider',
            'counterpart': 'custody',
            'counterpart_value': custody,
            'counterpart_source': 'position_events',
            'guards_against': 'liquidity_pull: depth nobody is committed to',
            # A4 asks the liquidity row to name the pool type its custody read is
            # defined for, because the read differs: a v3 pool holds its own
            # tokens and a v4 pool does not exist as an address at all.
            'pool_type': custody.get('pool_type', UNAVAILABLE),
        },
        {
            'metric': 'primary_pool_share_of_provider_liquidity',
            'value': pool_share,
            'source': 'dex_provider',
            'counterpart': 'primary_pool_onchain_liquidity',
            'counterpart_value': custody.get('pool_liquidity'),
            'counterpart_source': 'chain',
            'guards_against': (
                'fragmentation: many small third-party pools around a launch'
            ),
            'pool_type': custody.get('pool_type', UNAVAILABLE),
        },
    ]


def _primary_pool_share(context: BuildContext) -> Optional[float]:
    pools = context.identity.get('pools') or []
    reference = str(context.project.pool_ref).lower()
    total = sum(pool.get('liquidity_usd') or 0 for pool in pools)
    primary = next(
        (
            pool.get('liquidity_usd') or 0
            for pool in pools
            if str(pool.get('reference') or '').lower() == reference
        ),
        None,
    )
    if primary is None or not total:
        return None
    return round(primary / total, 6)
