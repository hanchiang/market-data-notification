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
        'data': data if data.startswith('0x') else '0x' + data,
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


class TestWithdrawalNetting:
    """The 2026-09-08 custody defect: a withdrawal never netted against its own
    deposit, so every custody share was over liquidity already out of the pool.

    Verified against pool entity 16 in `onchain_demo`: eleven burns, all under the
    v4 position manager, netting against mints under twelve different holders. The
    netted total (29277002188455995842192) is exactly the pool's own
    `getLiquidity`; the un-netted mint sum was 31686285479365033662895.
    """

    def _v4_log(self, *, delta, token_id, sender, block, index, tick_lower=-60, tick_upper=60):
        return _log(
            [uniswap.V4_MODIFY_LIQUIDITY.topic0, '0x' + 'ab' * 32,
             '0x' + '0' * 24 + sender[2:]],
            block=block, index=index, tx='0x' + f'{block:064x}',
            data=(f'{tick_lower & ((1 << 256) - 1):064x}'
                  f'{tick_upper & ((1 << 256) - 1):064x}'
                  f'{delta & ((1 << 256) - 1):064x}'
                  f'{token_id:064x}'),
        )

    def test_a_v4_withdrawal_nets_against_its_deposit_under_a_different_owner(self):
        logs = [
            self._v4_log(delta=1000, token_id=7, sender=MANAGER, block=10, index=0),
            self._v4_log(delta=-1000, token_id=7, sender=MANAGER, block=20, index=0),
        ]
        rows = uniswap.normalise_v4_events(logs, MANAGER)
        assert [row.nft_token_id for row in rows] == [7, 7]
        # The deposit is rewritten to the holder; the withdrawal has no ERC-721
        # transfer of its own and keeps the manager. Owner-keyed netting fails here.
        nft = [
            _log([uniswap.ERC721_TRANSFER.topic0, '0x' + '0' * 64,
                  '0x' + '0' * 24 + ALICE[2:], '0x' + f'{7:064x}'],
                 block=10, index=1, tx='0x' + f'{10:064x}')
        ]
        attributed = uniswap.attribute_owners(rows, nft)
        assert uniswap.net_positions(attributed) == []

    def test_the_salt_is_only_read_as_a_token_id_for_the_manager(self):
        """A direct provider's salt is arbitrary data of their choosing."""
        logs = [self._v4_log(delta=1000, token_id=7, sender=ALICE, block=10, index=0)]
        assert uniswap.normalise_v4_events(logs, MANAGER)[0].nft_token_id is None
        assert uniswap.normalise_v4_events(logs, ALICE)[0].nft_token_id == 7

    def test_a_v3_withdrawal_nets_through_the_managers_own_token_id_event(self):
        deposit_tx, withdraw_tx = '0x' + 'a1' * 32, '0x' + 'b2' * 32
        rows = [
            uniswap.PositionRow(
                block=10, tx_hash=deposit_tx, log_index=1, kind='mint', owner=MANAGER,
                nft_token_id=None, tick_lower=-60, tick_upper=60,
                liquidity_delta=1000, salt=None,
            ),
            uniswap.PositionRow(
                block=20, tx_hash=withdraw_tx, log_index=1, kind='burn', owner=MANAGER,
                nft_token_id=None, tick_lower=-60, tick_upper=60,
                liquidity_delta=-1000, salt=None,
            ),
        ]
        nft = [
            _log([uniswap.ERC721_TRANSFER.topic0, '0x' + '0' * 64,
                  '0x' + '0' * 24 + ALICE[2:], '0x' + f'{9:064x}'],
                 block=10, index=2, tx=deposit_tx)
        ]
        # Without the DecreaseLiquidity join the burn keeps the manager as owner.
        assert uniswap.net_positions(uniswap.attribute_owners(rows, nft)) != []
        attributed = uniswap.attribute_owners(
            rows, nft, {deposit_tx: [(3, 9)], withdraw_tx: [(3, 9)]}
        )
        assert [row.nft_token_id for row in attributed] == [9, 9]
        assert uniswap.net_positions(attributed) == []

    def test_two_positions_minted_in_one_transaction_keep_their_own_token_ids(self):
        """A multicall mints two positions in one transaction and the manager
        emits `IncreaseLiquidity` after each pool `Mint`, so the ids have to be
        matched by ORDER. One id per transaction put both pool events under the
        later position, which then reported twice its liquidity while the other
        reported none."""
        tx = '0x' + 'c3' * 32
        rows = [
            uniswap.PositionRow(
                block=10, tx_hash=tx, log_index=1, kind='mint', owner=MANAGER,
                nft_token_id=None, tick_lower=-60, tick_upper=60,
                liquidity_delta=1000, salt=None,
            ),
            uniswap.PositionRow(
                block=10, tx_hash=tx, log_index=5, kind='mint', owner=MANAGER,
                nft_token_id=None, tick_lower=-120, tick_upper=120,
                liquidity_delta=2000, salt=None,
            ),
        ]
        attributed = uniswap.attribute_owners(
            rows, [], {tx: [(3, 11), (7, 12)]}
        )
        assert [row.nft_token_id for row in attributed] == [11, 12]

    def test_rows_sharing_a_token_id_net_even_when_their_owners_disagree(self):
        """Why the netting key is the TOKEN ID and not the owner.

        The store outlives one build. A row written by an earlier build under the
        old attribution names the position manager, while a row written after the
        fix names the holder -- exactly the shape sitting in `onchain_demo` before
        the repair refetch. Keying on the owner would leave those two under
        separate positions and report liquidity that is not there.
        """
        rows = [
            uniswap.PositionRow(
                block=10, tx_hash='0xa1', log_index=1, kind='mint', owner=ALICE,
                nft_token_id=9, tick_lower=-60, tick_upper=60,
                liquidity_delta=1000, salt=None,
            ),
            uniswap.PositionRow(
                block=20, tx_hash='0xb2', log_index=1, kind='burn', owner=MANAGER,
                nft_token_id=9, tick_lower=-60, tick_upper=60,
                liquidity_delta=-1000, salt=None,
            ),
        ]
        assert uniswap.net_positions(rows) == []

    def test_the_pinned_block_owner_overrides_the_log_derived_holder(self):
        positions = [
            uniswap.Position(owner=ALICE, tick_lower=-60, tick_upper=60, salt=None,
                             liquidity=1000, nft_token_ids=[9]),
        ]
        assert uniswap.apply_resolved_owners(positions, {9: BOB})[0].owner == BOB

    def test_a_token_id_with_no_owner_read_keeps_its_provisional_holder(self):
        """A reverted or failed `ownerOf` must not reassign someone's liquidity."""
        positions = [
            uniswap.Position(owner=ALICE, tick_lower=-60, tick_upper=60, salt=None,
                             liquidity=1000, nft_token_ids=[9]),
        ]
        assert uniswap.apply_resolved_owners(positions, {})[0].owner == ALICE


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

    def test_a_later_nft_transfer_inside_the_fetch_moves_the_provisional_holder(self):
        """Provisional only. The ERC-721 stream is fetched over the blocks the
        pool's own liquidity events occupy, so a sale in any other block is not
        in `nft` at all. `apply_resolved_owners` is what settles the holder --
        see `test_the_pinned_block_owner_overrides_the_log_derived_holder`."""
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
