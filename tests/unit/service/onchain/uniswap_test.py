"""Pool identity, hook bits, tick math, netting and custody shares.

Everything here is deterministic given its inputs, so nothing is mocked. Two
groups matter more than the rest:

* the **tick math**, because it is the one derivation in the dossier with no
  chain read to check it for a v4 pool -- so it is checked here against the
  algebraic identities it must satisfy, and in a real build against a v3 pool's
  own balance;
* the **netting**, because a closed position contributing anything other than
  zero would put liquidity in the custody table that nobody holds.
"""
import pytest

from src.service.onchain.collectors import uniswap

POOL_MANAGER_LOG_ID = '0x' + 'ab' * 32
ALICE = '0x' + '11' * 20
BOB = '0x' + '22' * 20
MANAGER = '0x' + '33' * 20


def _log(topics, data='0x', block=100, index=0, tx='0x' + 'ee' * 32):
    return {
        'topics': topics,
        'data': data,
        'blockNumber': hex(block),
        'logIndex': hex(index),
        'transactionHash': tx,
    }


class TestPoolId:
    def test_the_id_is_keccak_of_the_encoded_key(self):
        """The v4 identity. Two keys differing only in `fee` must not collide,
        which is the property `key_matches` relies on."""
        base = uniswap.compute_v4_pool_id(uniswap.ZERO_ADDRESS, ALICE, 3000, 60, uniswap.ZERO_ADDRESS)
        other_fee = uniswap.compute_v4_pool_id(uniswap.ZERO_ADDRESS, ALICE, 500, 60, uniswap.ZERO_ADDRESS)
        assert base.startswith('0x') and len(base) == 66
        assert base != other_fee
        # Deterministic: the same key always names the same pool.
        assert base == uniswap.compute_v4_pool_id(
            uniswap.ZERO_ADDRESS, ALICE, 3000, 60, uniswap.ZERO_ADDRESS
        )

    def test_the_hook_is_part_of_the_identity(self):
        without = uniswap.compute_v4_pool_id(uniswap.ZERO_ADDRESS, ALICE, 3000, 60, uniswap.ZERO_ADDRESS)
        with_hook = uniswap.compute_v4_pool_id(uniswap.ZERO_ADDRESS, ALICE, 3000, 60, BOB)
        assert without != with_hook


class TestHookPermissions:
    def test_the_zero_address_is_no_hook_at_all(self):
        decoded = uniswap.decode_hook_permissions(uniswap.ZERO_ADDRESS)
        assert decoded['kind'] == 'none'
        assert decoded['permissions'] == []

    def test_the_low_bits_decode_to_callback_names(self):
        """A hand-built address: bit 7 (beforeSwap) and bit 6 (afterSwap) set.

        The value of the field is that it says a hook can interpose on a swap
        BEFORE any swap fails, so the two swap flags are the ones asserted.
        """
        address = '0x' + '00' * 18 + f'{(1 << 7) | (1 << 6):04x}'
        decoded = uniswap.decode_hook_permissions(address)
        assert decoded['kind'] == 'hooked'
        assert decoded['permissions'] == ['afterSwap', 'beforeSwap']
        assert decoded['bits'] == 0b11000000

    def test_bits_above_the_permission_mask_are_ignored(self):
        """Only the low 14 bits are permissions; the rest of the address is the
        address. A decoder reading further would report flags for every hook."""
        address = '0x' + 'ff' * 20
        decoded = uniswap.decode_hook_permissions(address)
        assert decoded['bits'] == uniswap.HOOK_PERMISSION_MASK
        assert len(decoded['permissions']) == 14


class TestTickMath:
    def test_tick_zero_is_price_one(self):
        assert uniswap.sqrt_price_x96_at_tick(0) == uniswap.Q96

    def test_the_ratio_grows_by_one_basis_point_per_tick(self):
        """`1.0001**tick` is the definition; one tick up is one basis point on
        the price, so half a basis point on the square root."""
        base = uniswap.sqrt_price_x96_at_tick(0)
        one_up = uniswap.sqrt_price_x96_at_tick(1)
        assert one_up > base
        assert 1.00004999 < one_up / base < 1.00005001

    def test_positive_and_negative_ticks_are_reciprocal(self):
        for tick in (1, 100, 10_000, 200_000):
            up = uniswap.sqrt_price_x96_at_tick(tick)
            down = uniswap.sqrt_price_x96_at_tick(-tick)
            assert abs((up * down) / (uniswap.Q96 ** 2) - 1) < 1e-12

    def test_a_tick_outside_the_range_is_refused(self):
        with pytest.raises(ValueError):
            uniswap.sqrt_price_x96_at_tick(uniswap.MAX_TICK + 1)

    def test_below_its_range_a_position_is_all_token0(self):
        amount0, amount1 = uniswap.amounts_for_liquidity(10 ** 18, 100, 200, current_tick=50)
        assert amount0 > 0 and amount1 == 0

    def test_above_its_range_a_position_is_all_token1(self):
        amount0, amount1 = uniswap.amounts_for_liquidity(10 ** 18, 100, 200, current_tick=300)
        assert amount0 == 0 and amount1 > 0

    def test_inside_its_range_a_position_holds_both(self):
        amount0, amount1 = uniswap.amounts_for_liquidity(10 ** 18, 100, 200, current_tick=150)
        assert amount0 > 0 and amount1 > 0

    def test_a_symmetric_range_at_its_midpoint_is_balanced(self):
        """At tick 0 with a range symmetric about it, the two amounts are equal
        up to rounding -- the identity that catches a swapped `sqrtA`/`sqrtB`."""
        amount0, amount1 = uniswap.amounts_for_liquidity(10 ** 24, -1000, 1000, current_tick=0)
        assert abs(amount0 - amount1) / amount0 < 1e-9

    def test_zero_liquidity_holds_nothing(self):
        assert uniswap.amounts_for_liquidity(0, -100, 100, 0) == (0, 0)


class TestNetting:
    def _row(self, kind, owner, delta, lower=-100, upper=100, tx='0xaa', index=0, token=None):
        return uniswap.PositionRow(
            block=1, tx_hash=tx, log_index=index, kind=kind, owner=owner,
            nft_token_id=token, tick_lower=lower, tick_upper=upper,
            liquidity_delta=delta, salt=None,
        )

    def test_a_closed_position_contributes_zero(self):
        positions = uniswap.net_positions(
            [self._row('mint', ALICE, 500), self._row('burn', ALICE, -500, index=1)]
        )
        assert positions == []

    def test_positions_net_per_owner_range_and_salt(self):
        rows = [
            self._row('mint', ALICE, 500),
            self._row('mint', ALICE, 300, index=1),
            self._row('mint', BOB, 200, index=2),
            # Same owner, different range: a separate position, not a top-up.
            self._row('mint', ALICE, 100, lower=-200, upper=200, index=3),
        ]
        positions = {(p.owner, p.tick_lower): p.liquidity for p in uniswap.net_positions(rows)}
        assert positions == {(ALICE, -100): 800, (BOB, -100): 200, (ALICE, -200): 100}


class TestOwnerAttribution:
    def test_a_position_manager_owner_becomes_the_nft_holder(self):
        """The join the design specifies: the pool event and the manager's mint
        from `0x0` share a transaction, so the token id and its holder are
        recoverable without reading the manager's state."""
        tx = '0x' + 'cd' * 32
        rows = [
            uniswap.PositionRow(
                block=10, tx_hash=tx, log_index=1, kind='mint', owner=MANAGER,
                nft_token_id=None, tick_lower=-60, tick_upper=60,
                liquidity_delta=1000, salt=None,
            )
        ]
        nft = [
            _log(
                [uniswap.ERC721_TRANSFER.topic0,
                 '0x' + '0' * 64,
                 '0x' + '0' * 24 + ALICE[2:],
                 '0x' + f'{7:064x}'],
                block=10, index=2, tx=tx,
            )
        ]
        attributed = uniswap.attribute_owners(rows, nft)
        assert attributed[0].owner == ALICE
        assert attributed[0].nft_token_id == 7

    def test_a_later_nft_transfer_moves_the_position(self):
        tx = '0x' + 'cd' * 32
        rows = [
            uniswap.PositionRow(
                block=10, tx_hash=tx, log_index=1, kind='mint', owner=MANAGER,
                nft_token_id=None, tick_lower=-60, tick_upper=60,
                liquidity_delta=1000, salt=None,
            )
        ]
        nft = [
            _log([uniswap.ERC721_TRANSFER.topic0, '0x' + '0' * 64,
                  '0x' + '0' * 24 + ALICE[2:], '0x' + f'{7:064x}'], block=10, index=2, tx=tx),
            _log([uniswap.ERC721_TRANSFER.topic0, '0x' + '0' * 24 + ALICE[2:],
                  '0x' + '0' * 24 + BOB[2:], '0x' + f'{7:064x}'], block=20, index=0,
                 tx='0x' + 'ff' * 32),
        ]
        assert uniswap.attribute_owners(rows, nft)[0].owner == BOB

    def test_a_position_held_directly_keeps_its_event_owner(self):
        """No NFT in the transaction means the liquidity was added directly.
        Calling that unattributed would lose the fact that someone holds it."""
        rows = [
            uniswap.PositionRow(
                block=10, tx_hash='0xaa', log_index=1, kind='mint', owner=ALICE,
                nft_token_id=None, tick_lower=-60, tick_upper=60,
                liquidity_delta=1000, salt=None,
            )
        ]
        assert uniswap.attribute_owners(rows, [])[0].owner == ALICE


class TestCustodyShares:
    def test_shares_are_reported_by_owner_class(self):
        positions = [
            uniswap.Position(owner=ALICE, tick_lower=-60, tick_upper=60, salt=None, liquidity=750),
            uniswap.Position(owner=BOB, tick_lower=-60, tick_upper=60, salt=None, liquidity=250),
        ]
        shares = uniswap.custody_shares(positions, {ALICE: 'project'})
        assert shares['owner_count'] == 2
        assert shares['largest_owner'] == ALICE
        assert shares['largest_owner_share'] == 0.75
        assert shares['share_by_class'] == {'project': 0.75, 'eoa': 0.25}

    def test_no_positions_is_not_a_division_by_zero(self):
        shares = uniswap.custody_shares([], {})
        assert shares['owner_count'] == 0
        assert shares['largest_owner_share'] is None
