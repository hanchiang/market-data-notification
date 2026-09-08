"""Token economics: supply, where new supply can come from, and where it sits.

The section that says whether the number on a chart can be diluted. `mint_path`
is its structural field -- the threshold table flags any change to it, because a
token going from `fixed` to mintable is a different asset regardless of how much
was minted.

`pool_held_share` is the one derived figure here, and the derivation differs by
pool type for a reason that is easy to get wrong: a v3 pool holds its own tokens,
so the share is a `balanceOf`; a v4 pool has no address, and the singleton pool
manager's balance is every v4 pool on the chain at once. So for v4 the amount is
computed from the open positions by tick math, and that derivation is validated
by running it on a v3 pool where `balanceOf` can check it (design, Testing).
"""
import logging
from typing import Any, Dict

from market_data_library.core.onchain.evm import abi

from src.service.onchain.collectors import transfers, uniswap
from src.service.onchain.collectors.base import (
    SECTION_TOKEN_ECONOMICS,
    STATUS_OK,
    BuildContext,
    SectionResult,
)
from src.service.onchain.config import ARCHETYPES_WITHOUT_TREASURY

logger = logging.getLogger('Onchain token economics')

UNAVAILABLE = 'unavailable'
NOT_APPLICABLE = 'not_applicable'


async def collect(context: BuildContext) -> SectionResult:
    token_address = context.identity.get('token_address')
    if not token_address:
        from src.service.onchain.collectors.contract_safety import IdentityUnresolvedError

        raise IdentityUnresolvedError('token economics needs a resolved token address')

    token_entity_id = context.token_entity_id()
    supply_data, raw = await context.state_client.call(
        token_address, uniswap.call_data('totalSupply'), context.block
    )
    context.record_jsonrpc(raw)
    total_supply = int(abi.decode_single('uint256', supply_data))

    burned = context.repository.burned_amount(
        token_entity_id,
        list(transfers.burn_addresses()),
        mint_source=transfers.ZERO_ADDRESS,
    )
    holders = transfers.holder_summary(context.repository, token_entity_id)

    fields: Dict[str, Any] = {
        'total_supply': str(total_supply),
        'decimals': context.identity.get('decimals', UNAVAILABLE),
        'burned': str(burned),
        'burned_share': None if not total_supply else round(burned / total_supply, 6),
        'mint_path': await _mint_path(context, token_entity_id),
        'top_holders': holders['top_holders'],
        'top_ten_share': holders['top_ten_share'],
        'lockers': _lockers(context),
        'treasury': (
            NOT_APPLICABLE
            if context.project.archetype in ARCHETYPES_WITHOUT_TREASURY
            else UNAVAILABLE
        ),
    }
    fields['pool_held_share'] = await _pool_held_share(context, total_supply)
    return SectionResult(
        name=SECTION_TOKEN_ECONOMICS,
        status=STATUS_OK,
        fields=fields,
        evidence_ids=list(context.evidence_ids),
    )


async def _mint_path(context: BuildContext, token_entity_id: int) -> str:
    """`fixed`, `selector`, `observed` or both.

    Two independent signals, because either alone is wrong in one direction: a
    `mint` selector that is never called still means supply CAN grow, and a token
    with no `mint` selector can still have minted through an internal path that
    only shows up as a `Transfer` from `0x0` after creation. The field records
    which signals fired, not a yes/no, so the diff says which one changed.
    """
    safety = context.identity.get('privileged_selectors') or []
    has_selector = 'mint' in safety
    creation_block = context.identity.get('creation_block')
    minted_after_creation = False
    if isinstance(creation_block, int):
        row = context.repository.fetch_one(
            'SELECT 1 AS hit FROM onchain.transfer WHERE token_id = %s '
            'AND from_addr = %s AND block > %s LIMIT 1',
            (token_entity_id, transfers.ZERO_ADDRESS, creation_block),
        )
        minted_after_creation = row is not None

    if has_selector and minted_after_creation:
        return 'selector_and_observed'
    if has_selector:
        return 'selector'
    if minted_after_creation:
        return 'observed'
    return 'fixed'


def _lockers(context: BuildContext) -> Any:
    """`unavailable` while the chain's locker list is empty (design DG-6).

    Not `none`: nobody has verified a locker deployment on this chain, so "no
    tokens are locked" and "we do not know where lockers are" must not read the
    same in a diff.
    """
    if not context.chain.lockers:
        return UNAVAILABLE
    return sorted(str(address).lower() for address in context.chain.lockers)


async def _pool_held_share(context: BuildContext, total_supply: int) -> Any:
    if not total_supply:
        return None
    version = context.identity.get('version')
    token_address = context.identity.get('token_address')
    if version == 'v4':
        return await _v4_pool_held_share(context, total_supply)

    pool = context.identity.get('pool_address')
    if not pool or not token_address:
        return UNAVAILABLE
    data, raw = await context.state_client.call(
        token_address, uniswap.call_data('balanceOf', ['address'], [pool]), context.block
    )
    context.record_jsonrpc(raw)
    held = int(abi.decode_single('uint256', data))
    return {
        'method': 'pool_balance_of',
        'amount': str(held),
        'share': round(held / total_supply, 6),
    }


async def _v4_pool_held_share(context: BuildContext, total_supply: int) -> Any:
    """The token amount of the pool's open positions, by tick math.

    `currency0`/`currency1` decide which of the two amounts is this token. When
    the key is `unavailable` -- the `Initialize` log was not served -- so is this
    field, because there is nothing to say which side of the pair the token is.
    """
    pool_entity_id = context.pool_entity_id()
    tick = context.identity.get('tick')
    currency0 = context.identity.get('currency0')
    token_address = str(context.identity.get('token_address') or '').lower()
    if not isinstance(tick, int) or not isinstance(currency0, str) or not token_address:
        return UNAVAILABLE
    if not currency0.startswith('0x'):
        return UNAVAILABLE

    stored = context.repository.get_position_events(pool_entity_id)
    rows = [
        uniswap.PositionRow(
            block=int(row['block']),
            tx_hash=row['tx_hash'],
            log_index=int(row['log_index']),
            kind=row['kind'],
            owner=row['owner'],
            nft_token_id=None if row['nft_token_id'] is None else int(row['nft_token_id']),
            tick_lower=int(row['tick_lower']),
            tick_upper=int(row['tick_upper']),
            liquidity_delta=int(row['liquidity_delta']),
            salt=row['salt'],
        )
        for row in stored
    ]
    positions = uniswap.net_positions(rows)
    if not positions:
        return {'method': 'v4_tick_math', 'amount': '0', 'share': 0.0, 'positions': 0}

    is_currency0 = currency0 == token_address
    total = 0
    for position in positions:
        amount0, amount1 = uniswap.amounts_for_liquidity(
            position.liquidity, position.tick_lower, position.tick_upper, tick
        )
        total += amount0 if is_currency0 else amount1
    return {
        'method': 'v4_tick_math',
        'amount': str(total),
        'share': round(total / total_supply, 6),
        'positions': len(positions),
        'token_is_currency0': is_currency0,
    }


async def validate_tick_math_against_v3(
    context: BuildContext, *, pool_address: str, token_address: str
) -> Dict[str, Any]:
    """Run the v4 derivation on a v3 pool and compare with that pool's balance.

    The design's validation for the tick math (Testing: custody). A v3 pool DOES
    hold its own tokens, so `balanceOf` is ground truth for exactly the quantity
    the v4 path has no ground truth for. Returns both figures and their relative
    error rather than a boolean: the derivation is fixed-point and the answer is
    "how close", which a boolean would throw away.
    """
    pool_entity_id = context.pool_entity_id()
    tick = context.identity.get('tick')
    if not isinstance(tick, int):
        return {'state': UNAVAILABLE, 'reason': 'no current tick'}

    stored = context.repository.get_position_events(pool_entity_id)
    positions = uniswap.net_positions(
        [
            uniswap.PositionRow(
                block=int(row['block']),
                tx_hash=row['tx_hash'],
                log_index=int(row['log_index']),
                kind=row['kind'],
                owner=row['owner'],
                nft_token_id=None,
                tick_lower=int(row['tick_lower']),
                tick_upper=int(row['tick_upper']),
                liquidity_delta=int(row['liquidity_delta']),
                salt=row['salt'],
            )
            for row in stored
        ]
    )
    is_currency0 = str(context.identity.get('currency0') or '').lower() == token_address.lower()
    derived = 0
    for position in positions:
        amount0, amount1 = uniswap.amounts_for_liquidity(
            position.liquidity, position.tick_lower, position.tick_upper, tick
        )
        derived += amount0 if is_currency0 else amount1

    data, raw = await context.state_client.call(
        token_address,
        uniswap.call_data('balanceOf', ['address'], [pool_address]),
        context.block,
    )
    context.record_jsonrpc(raw)
    actual = int(abi.decode_single('uint256', data))
    return {
        'derived': str(derived),
        'balance_of': str(actual),
        'relative_error': None if not actual else abs(derived - actual) / actual,
        'positions': len(positions),
    }
