"""Uniswap v3 and v4 specifics: pool identity, liquidity events, custody, tick math.

Kept in the backend for phase 1a rather than lifted into the library (design
DG-4): it has exactly one consumer, and a library release between every edit and
its first use would slow the only loop that is finding the bugs.

Two things here are worth reading before trusting a number out of this module:

* **A v4 pool has no address.** Its identity is `keccak256(abi.encode(currency0,
  currency1, fee, tickSpacing, hooks))`, and every read goes through the
  singleton pool manager or the state view keyed by that id. So "the pool's token
  balance" is meaningless for v4 -- the manager holds every v4 pool's tokens at
  once -- which is why `pool_held_share` is derived by tick math instead.
* **The tick-price relation is computed in decimal, not by Uniswap's TickMath.**
  Transcribing TickMath's twenty magic constants from memory is exactly the kind
  of thing that is wrong in one digit and passes every test that does not have a
  reference to check against, so this uses `Decimal` at 60 digits of precision
  for `sqrt(1.0001**tick) * 2**96`. The error against the Solidity routine is far
  below one part in 10^18, and it is checked rather than assumed: `pool_held_share`
  runs the same derivation on a v3 pool, where the pool DOES hold its own tokens,
  and compares with that pool's `balanceOf` (design, Testing: custody).
"""
import logging
from dataclasses import dataclass, field
from decimal import Context, Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from market_data_library.core.onchain.evm import abi
from market_data_library.core.onchain.evm.keccak import keccak256

logger = logging.getLogger('Onchain uniswap')

# 60 significant digits. A v3 sqrt price is a Q64.96 fixed-point number, so ~50
# digits are needed for the widest legal price and the rest is headroom.
#
# A LOCAL context, never `getcontext().prec = 60`. Decimal's context is
# process-wide and per-thread, so setting it at import time silently re-rounded
# every other Decimal in the backend: the project monitor's report started
# emitting `pair_price` to 60 places instead of 28, which its own row-equality
# test caught. A module that imports must not change how unrelated arithmetic
# rounds.
_TICK_CONTEXT = Context(prec=60)

ZERO_ADDRESS = '0x' + '0' * 40

MIN_TICK = -887272
MAX_TICK = 887272

Q96 = 1 << 96


# -- calldata ---------------------------------------------------------------

def call_data(name: str, arg_types: Sequence[Any] = (), args: Sequence[Any] = ()) -> str:
    return abi.encode_call(name, arg_types, args)


V3_POOL_GETTERS = {
    'factory': ('factory', 'address'),
    'token0': ('token0', 'address'),
    'token1': ('token1', 'address'),
    'fee': ('fee', 'uint24'),
    'tickSpacing': ('tickSpacing', 'int24'),
    'liquidity': ('liquidity', 'uint128'),
}


# -- events -----------------------------------------------------------------

# v3 pool. `sender` is NOT indexed while `owner` is, so the data words are
# (sender, amount, amount0, amount1) in declaration order.
V3_MINT = abi.EventSpec(
    'Mint',
    [
        ('sender', 'address', False),
        ('owner', 'address', True),
        ('tickLower', 'int24', True),
        ('tickUpper', 'int24', True),
        ('amount', 'uint128', False),
        ('amount0', 'uint256', False),
        ('amount1', 'uint256', False),
    ],
)
V3_BURN = abi.EventSpec(
    'Burn',
    [
        ('owner', 'address', True),
        ('tickLower', 'int24', True),
        ('tickUpper', 'int24', True),
        ('amount', 'uint128', False),
        ('amount0', 'uint256', False),
        ('amount1', 'uint256', False),
    ],
)
V3_POOL_CREATED = abi.EventSpec(
    'PoolCreated',
    [
        ('token0', 'address', True),
        ('token1', 'address', True),
        ('fee', 'uint24', True),
        ('tickSpacing', 'int24', False),
        ('pool', 'address', False),
    ],
)
V3_INCREASE_LIQUIDITY = abi.EventSpec(
    'IncreaseLiquidity',
    [
        ('tokenId', 'uint256', True),
        ('liquidity', 'uint128', False),
        ('amount0', 'uint256', False),
        ('amount1', 'uint256', False),
    ],
)
V3_DECREASE_LIQUIDITY = abi.EventSpec(
    'DecreaseLiquidity',
    [
        ('tokenId', 'uint256', True),
        ('liquidity', 'uint128', False),
        ('amount0', 'uint256', False),
        ('amount1', 'uint256', False),
    ],
)
# ERC-721. Identical name and arity to ERC-20 `Transfer` but a different topic0,
# because the third argument is indexed here and is not there -- which is what
# makes an NFT transfer distinguishable from a token transfer by topic alone.
ERC721_TRANSFER = abi.EventSpec(
    'Transfer',
    [
        ('from', 'address', True),
        ('to', 'address', True),
        ('tokenId', 'uint256', True),
    ],
)

# v4 pool manager.
V4_INITIALIZE = abi.EventSpec(
    'Initialize',
    [
        ('id', 'bytes32', True),
        ('currency0', 'address', True),
        ('currency1', 'address', True),
        ('fee', 'uint24', False),
        ('tickSpacing', 'int24', False),
        ('hooks', 'address', False),
        ('sqrtPriceX96', 'uint160', False),
        ('tick', 'int24', False),
    ],
)
V4_MODIFY_LIQUIDITY = abi.EventSpec(
    'ModifyLiquidity',
    [
        ('id', 'bytes32', True),
        ('sender', 'address', True),
        ('tickLower', 'int24', False),
        ('tickUpper', 'int24', False),
        ('liquidityDelta', 'int256', False),
        ('salt', 'bytes32', False),
    ],
)


# -- v4 pool id -------------------------------------------------------------

def compute_v4_pool_id(
    currency0: str, currency1: str, fee: int, tick_spacing: int, hooks: str
) -> str:
    """`keccak256(abi.encode(PoolKey))`, the whole of a v4 pool's identity.

    Recomputing it from the `Initialize` log's own fields and comparing with the
    operator's reference is what turns "the provider says this is the pool" into
    a chain-confirmed fact (`key_matches`, design D3).
    """
    encoded = abi.encode(
        ['address', 'address', 'uint24', 'int24', 'address'],
        [currency0, currency1, fee, tick_spacing, hooks],
    )
    return '0x' + keccak256(encoded).hex()


# -- hook permissions -------------------------------------------------------

# The 14 low bits of a v4 hook's ADDRESS are its permission set: the hook is
# deployed to a mined address whose low bits declare which callbacks the pool
# manager will invoke. Bit 13 is the first callback in the core library's order.
#
# UNVERIFIED against this deployment's `Hooks` library (design, Red team: "the
# hook permission bits are laid out differently from the v4 core release these
# contracts derive from"). If the layout differs, the field is wrong rather than
# absent -- which is why the decoded set is stored alongside the raw low bits, so
# a later correction can be applied to stored evidence without a re-read.
HOOK_FLAGS: List[Tuple[int, str]] = [
    (13, 'beforeInitialize'),
    (12, 'afterInitialize'),
    (11, 'beforeAddLiquidity'),
    (10, 'afterAddLiquidity'),
    (9, 'beforeRemoveLiquidity'),
    (8, 'afterRemoveLiquidity'),
    (7, 'beforeSwap'),
    (6, 'afterSwap'),
    (5, 'beforeDonate'),
    (4, 'afterDonate'),
    (3, 'beforeSwapReturnsDelta'),
    (2, 'afterSwapReturnsDelta'),
    (1, 'afterAddLiquidityReturnsDelta'),
    (0, 'afterRemoveLiquidityReturnsDelta'),
]

HOOK_PERMISSION_MASK = (1 << 14) - 1


def decode_hook_permissions(hook_address: str) -> Dict[str, Any]:
    """The callbacks a v4 hook address declares.

    `none` for the zero address, which is the ordinary case: a pool with no hook
    is a pool nothing can interpose on, and that is the fact worth recording.
    """
    address = (hook_address or ZERO_ADDRESS).lower()
    if address == ZERO_ADDRESS:
        return {'hook': ZERO_ADDRESS, 'permissions': [], 'bits': 0, 'kind': 'none'}
    bits = int(address, 16) & HOOK_PERMISSION_MASK
    return {
        'hook': address,
        'bits': bits,
        'kind': 'hooked',
        'permissions': sorted(
            name for shift, name in HOOK_FLAGS if bits & (1 << shift)
        ),
    }


# -- tick math --------------------------------------------------------------

def sqrt_price_x96_at_tick(tick: int) -> int:
    """`floor(sqrt(1.0001**tick) * 2**96)`.

    See the module docstring for why this is decimal arithmetic and not a
    transcription of Solidity's TickMath.
    """
    if not MIN_TICK <= tick <= MAX_TICK:
        raise ValueError(f'tick {tick} is outside the representable range')
    ratio = _TICK_CONTEXT.power(Decimal('1.0001'), Decimal(tick))
    return int(_TICK_CONTEXT.multiply(_TICK_CONTEXT.sqrt(ratio), Decimal(Q96)))


def amounts_for_liquidity(
    liquidity: int, tick_lower: int, tick_upper: int, current_tick: int
) -> Tuple[int, int]:
    """The (token0, token1) amounts a position of `liquidity` holds right now.

    The three cases are the whole of concentrated liquidity: below its range a
    position is entirely token0, above it entirely token1, and inside it holds
    both in the proportion the current price sets. A position whose range the
    price has left is therefore NOT half the pool's depth for the pair -- which
    is the reason a provider's single "liquidity USD" figure is the gameable one
    the pairing rule exists to pair.
    """
    if liquidity <= 0:
        return 0, 0
    if tick_lower > tick_upper:
        tick_lower, tick_upper = tick_upper, tick_lower
    sqrt_a = sqrt_price_x96_at_tick(tick_lower)
    sqrt_b = sqrt_price_x96_at_tick(tick_upper)
    if sqrt_a == sqrt_b:
        return 0, 0
    sqrt_p = sqrt_price_x96_at_tick(max(min(current_tick, MAX_TICK), MIN_TICK))

    if current_tick < tick_lower:
        return _amount0(liquidity, sqrt_a, sqrt_b), 0
    if current_tick >= tick_upper:
        return 0, _amount1(liquidity, sqrt_a, sqrt_b)
    return _amount0(liquidity, sqrt_p, sqrt_b), _amount1(liquidity, sqrt_a, sqrt_p)


def _amount0(liquidity: int, sqrt_low: int, sqrt_high: int) -> int:
    if sqrt_low > sqrt_high:
        sqrt_low, sqrt_high = sqrt_high, sqrt_low
    if sqrt_low == 0:
        return 0
    return (liquidity * Q96 * (sqrt_high - sqrt_low)) // (sqrt_high * sqrt_low)


def _amount1(liquidity: int, sqrt_low: int, sqrt_high: int) -> int:
    if sqrt_low > sqrt_high:
        sqrt_low, sqrt_high = sqrt_high, sqrt_low
    return (liquidity * (sqrt_high - sqrt_low)) // Q96


# -- position events --------------------------------------------------------

@dataclass
class PositionRow:
    """One liquidity event, normalised across v3 and v4 (store table
    `position_event`)."""

    block: int
    tx_hash: str
    log_index: int
    kind: str
    owner: str
    nft_token_id: Optional[int]
    tick_lower: int
    tick_upper: int
    liquidity_delta: int
    salt: Optional[str]

    def as_row(self) -> Dict[str, Any]:
        return {
            'block': self.block,
            'tx_hash': self.tx_hash,
            'log_index': self.log_index,
            'kind': self.kind,
            'owner': self.owner,
            'nft_token_id': self.nft_token_id,
            'tick_lower': self.tick_lower,
            'tick_upper': self.tick_upper,
            'liquidity_delta': self.liquidity_delta,
            'salt': self.salt,
        }


def _block_of(log: Dict[str, Any]) -> int:
    return int(log['blockNumber'], 16)


def _index_of(log: Dict[str, Any]) -> int:
    return int(log['logIndex'], 16)


def normalise_v3_events(
    mint_logs: Sequence[Dict[str, Any]], burn_logs: Sequence[Dict[str, Any]]
) -> List[PositionRow]:
    """v3 `Mint`/`Burn` as signed liquidity deltas keyed by (owner, range).

    The owner recorded is the event's own `owner`, which for an NFT position is
    the position manager rather than the person -- attribution to the real holder
    happens in `attribute_owners` from the manager's ERC-721 transfers.
    """
    rows: List[PositionRow] = []
    for log in mint_logs:
        fields = abi.decode_log(V3_MINT, log)
        rows.append(
            PositionRow(
                block=_block_of(log),
                tx_hash=log['transactionHash'],
                log_index=_index_of(log),
                kind='mint',
                owner=str(fields['owner']).lower(),
                nft_token_id=None,
                tick_lower=int(fields['tickLower']),
                tick_upper=int(fields['tickUpper']),
                liquidity_delta=int(fields['amount']),
                salt=None,
            )
        )
    for log in burn_logs:
        fields = abi.decode_log(V3_BURN, log)
        rows.append(
            PositionRow(
                block=_block_of(log),
                tx_hash=log['transactionHash'],
                log_index=_index_of(log),
                kind='burn',
                owner=str(fields['owner']).lower(),
                nft_token_id=None,
                tick_lower=int(fields['tickLower']),
                tick_upper=int(fields['tickUpper']),
                liquidity_delta=-int(fields['amount']),
                salt=None,
            )
        )
    return sorted(rows, key=lambda row: (row.block, row.log_index))


def normalise_v4_events(
    logs: Sequence[Dict[str, Any]], position_manager: Optional[str] = None
) -> List[PositionRow]:
    """v4 `ModifyLiquidity` already carries a signed delta, so one event shape
    covers both directions. `sender` is the position manager, the hook, or a
    launcher acting directly.

    When `sender` IS the position manager, the event's `salt` is `bytes32(tokenId)`
    -- the manager derives it that way so each NFT owns a distinct position under
    one `msg.sender`. That makes the token id readable off the event itself, which
    is the only reason a WITHDRAWAL can be attributed at all: a decrease emits no
    ERC-721 transfer, so the same-transaction join that identifies a deposit finds
    nothing. Without this, a burn and its own mint land under different owners and
    never net (verified 2026-09-08 against pool entity 16: eleven burns, every one
    of them under the manager, every salt equal to a mint's token id).

    The salt is only read as a token id when the sender is the manager. For a
    direct liquidity provider the salt is arbitrary data of their choosing, and
    reading it as a token id would invent an NFT that does not exist.
    """
    manager = str(position_manager).lower() if position_manager else None
    rows: List[PositionRow] = []
    for log in logs:
        fields = abi.decode_log(V4_MODIFY_LIQUIDITY, log)
        delta = int(fields['liquidityDelta'])
        sender = str(fields['sender']).lower()
        salt = str(fields['salt'])
        token_id = None
        if manager is not None and sender == manager:
            salt_value = int(salt, 16)
            if salt_value:
                token_id = salt_value
        rows.append(
            PositionRow(
                block=_block_of(log),
                tx_hash=log['transactionHash'],
                log_index=_index_of(log),
                kind='mint' if delta >= 0 else 'burn',
                owner=sender,
                nft_token_id=token_id,
                tick_lower=int(fields['tickLower']),
                tick_upper=int(fields['tickUpper']),
                liquidity_delta=delta,
                salt=salt,
            )
        )
    return sorted(rows, key=lambda row: (row.block, row.log_index))


def _token_id_after(
    entries: Optional[Sequence[Tuple[int, int]]], log_index: int
) -> Optional[int]:
    """The token id whose manager event FOLLOWS this pool event in the same
    transaction.

    The manager emits `Transfer` and then `IncreaseLiquidity` after the pool's
    own `Mint`, so the first manager event past the pool event's log index is the
    one that belongs to it. Falling back to the last entry keeps a transaction
    whose ordering is unexpected attributed rather than dropped -- being wrong
    about which of two positions in one multicall is worse than being wrong about
    none, but calling it unattributed loses the liquidity entirely.
    """
    if not entries:
        return None
    for index, token_id in entries:
        if index > log_index:
            return token_id
    return entries[-1][1]


def attribute_owners(
    rows: Sequence[PositionRow],
    nft_transfers: Sequence[Dict[str, Any]],
    token_id_by_tx: Optional[Dict[str, List[Tuple[int, int]]]] = None,
) -> List[PositionRow]:
    """Give every row the NFT token id its liquidity belongs to, and a holder.

    A row's token id is found three ways, in order of how directly the chain
    states it:

    1. The row already carries one -- v4, where the manager writes the token id
       into the event's own salt.
    2. `token_id_by_tx`, built by the caller from the manager's own
       `IncreaseLiquidity`/`DecreaseLiquidity` in the same transaction -- v3,
       where the pool event names only the manager. Passed as
       `{tx: [(log_index, token_id), ...]}` and matched by ORDER within the
       transaction, because one multicall can mint two positions and the manager
       emits its own event after each: keeping a single id per transaction
       netted both positions under whichever came last.
    3. The ERC-721 mint (`from` = `0x0`) in the same transaction -- a first
       deposit, and the only one of the three that a WITHDRAWAL never has.

    Route 3 alone is what this did until 2026-09-08, and it is why custody was
    wrong: a decrease has no NFT mint, so it kept the position manager as owner
    while its own deposit had been rewritten to the holder. The two rows landed
    under different owners and could not net, leaving every custody share
    computed over liquidity that had already left the pool.

    The holder assigned here is PROVISIONAL -- the last holder visible in the
    fetched transfers. It is the current holder only if no transfer happened in a
    block this fetch did not cover, which is why `resolve_position_owners` reads
    `ownerOf` at the pinned block and overrides it. Rows with no token id by any
    route keep their event owner: that is liquidity held directly, and calling it
    unattributed would lose the fact that someone holds it.
    """
    minted_in: Dict[str, List[Tuple[int, int]]] = {}
    holder_of: Dict[int, str] = {}
    for log in sorted(nft_transfers, key=lambda entry: (_block_of(entry), _index_of(entry))):
        fields = abi.decode_log(ERC721_TRANSFER, log)
        token_id = int(fields['tokenId'])
        sender = str(fields['from']).lower()
        recipient = str(fields['to']).lower()
        if sender == ZERO_ADDRESS:
            minted_in.setdefault(str(log['transactionHash']).lower(), []).append(
                (_index_of(log), token_id)
            )
        holder_of[token_id] = recipient

    by_tx = {
        key.lower(): sorted(value)
        for key, value in (token_id_by_tx or {}).items()
    }
    attributed: List[PositionRow] = []
    for row in rows:
        tx_hash = str(row.tx_hash).lower()
        token_id = row.nft_token_id
        if token_id is None:
            token_id = _token_id_after(by_tx.get(tx_hash), row.log_index)
        if token_id is None:
            token_id = _token_id_after(minted_in.get(tx_hash), row.log_index)
        if token_id is None:
            attributed.append(row)
            continue
        attributed.append(
            PositionRow(
                block=row.block,
                tx_hash=row.tx_hash,
                log_index=row.log_index,
                kind=row.kind,
                owner=holder_of.get(token_id, row.owner),
                nft_token_id=token_id,
                tick_lower=row.tick_lower,
                tick_upper=row.tick_upper,
                liquidity_delta=row.liquidity_delta,
                salt=row.salt,
            )
        )
    return attributed


@dataclass
class Position:
    """One netted position: (owner, tick range, salt)."""

    owner: str
    tick_lower: int
    tick_upper: int
    salt: Optional[str]
    liquidity: int = 0
    nft_token_ids: List[int] = field(default_factory=list)


def rows_from_store(stored: Sequence[Dict[str, Any]]) -> List[PositionRow]:
    """`position_event` rows back into `PositionRow`.

    One reconstruction shared by both readers -- custody and the token's
    pool-held share -- so they cannot drift into netting the same events two
    different ways.
    """
    return [
        PositionRow(
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


def net_positions(rows: Sequence[PositionRow]) -> List[Position]:
    """Positions netted per NFT token id where there is one, else per
    (owner, tick range, salt). Closed ones are dropped.

    The token id is the key that matters, and keying on the owner instead is the
    bug this replaces: a deposit is attributed to the NFT's holder while its own
    withdrawal names the position manager, so an owner-keyed pair never nets and
    the pool looks to hold liquidity that was taken out. Two rows sharing a token
    id ARE the same position by definition, whoever the manager reported.

    The owner recorded on a netted position is the LAST contributing row's, which
    is provisional for anything token-id-keyed -- `resolve_position_owners`
    settles it against the chain at the pinned block.

    A closed position contributes zero rather than disappearing from the history:
    it is netted to zero here and excluded from the returned list, so the custody
    share is over open liquidity only while the events behind it stay in the
    store.
    """
    netted: Dict[Tuple[Any, ...], Position] = {}
    for row in rows:
        if row.nft_token_id is not None:
            key: Tuple[Any, ...] = ('nft', row.nft_token_id)
        else:
            key = ('raw', row.owner, row.tick_lower, row.tick_upper, row.salt)
        position = netted.get(key)
        if position is None:
            position = Position(
                owner=row.owner,
                tick_lower=row.tick_lower,
                tick_upper=row.tick_upper,
                salt=row.salt,
            )
            netted[key] = position
        else:
            position.owner = row.owner
        position.liquidity += row.liquidity_delta
        if row.nft_token_id is not None and row.nft_token_id not in position.nft_token_ids:
            position.nft_token_ids.append(row.nft_token_id)
    return [position for position in netted.values() if position.liquidity > 0]


def owner_of_call_data(token_id: int) -> str:
    """`ownerOf(uint256)` calldata for a position-manager NFT."""
    return abi.encode_call('ownerOf', ['uint256'], [token_id])


def apply_resolved_owners(
    positions: Sequence[Position], owners: Dict[int, str]
) -> List[Position]:
    """Replace each position's provisional owner with its `ownerOf` result.

    Only the pinned-block read settles who holds a position: the log-derived
    holder is the last transfer inside the fetched windows, and an NFT that
    changed hands in a block carrying no liquidity event was never fetched. A
    token id missing from `owners` keeps its provisional owner -- a failed or
    reverted read must not silently reassign someone's liquidity.
    """
    for position in positions:
        for token_id in position.nft_token_ids:
            resolved = owners.get(token_id)
            if resolved:
                position.owner = resolved
                break
    return list(positions)


def custody_shares(
    positions: Sequence[Position], owner_class: Dict[str, str]
) -> Dict[str, Any]:
    """Liquidity by owner and by owner class.

    Shares are computed over the summed position liquidity, NOT over the pool's
    reported `liquidity()`: the pool's figure is liquidity in range at the
    current tick, while these positions include out-of-range ones. Both are
    stored, and the collector reports them side by side rather than reconciling
    them into one number that would be neither.
    """
    total = sum(position.liquidity for position in positions)
    by_owner: Dict[str, int] = {}
    for position in positions:
        by_owner[position.owner] = by_owner.get(position.owner, 0) + position.liquidity

    by_class: Dict[str, int] = {}
    for owner, amount in by_owner.items():
        klass = owner_class.get(owner, 'eoa')
        by_class[klass] = by_class.get(klass, 0) + amount

    largest = max(by_owner.items(), key=lambda item: item[1], default=(None, 0))
    return {
        'position_liquidity_total': str(total),
        'owner_count': len(by_owner),
        'largest_owner': largest[0],
        'largest_owner_share': _share(largest[1], total),
        'share_by_class': {
            name: _share(amount, total) for name, amount in sorted(by_class.items())
        },
        'liquidity_by_class': {
            name: str(amount) for name, amount in sorted(by_class.items())
        },
    }


def _share(part: int, total: int) -> Optional[float]:
    if not total:
        return None
    return round(part / total, 6)
