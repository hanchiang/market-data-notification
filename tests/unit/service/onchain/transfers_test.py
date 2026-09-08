"""The holder derivation and the window counts, over real rows in real Postgres.

These aggregate in SQL, so they are tested through the store rather than against
a Python stand-in: the netting is a `SUM ... HAVING > 0` over a `UNION ALL` of
signed amounts, and a Python re-implementation in the test would be asserting
that the author wrote the same query twice.

The amounts are uint256-scale on purpose. `numeric(78,0)` is what makes the
netting exact, and a test at small integers would pass just as happily against a
column that had silently become `bigint`.
"""
import pytest

from src.service.onchain.collectors import transfers
from src.service.onchain.registry import parse_registry, upsert_registry

WAD = 10 ** 18
HUGE = 10 ** 30  # far beyond float64's exact range, and beyond bigint

ALICE = '0x' + '11' * 20
BOB = '0x' + '22' * 20
CAROL = '0x' + '33' * 20
POOL = '0x' + '44' * 20
ZERO = transfers.ZERO_ADDRESS
DEAD = transfers.DEAD_ADDRESS


@pytest.fixture
def token_id(onchain_repository, onchain_registry_payload):
    registry = parse_registry(onchain_registry_payload)
    project_ids = upsert_registry(onchain_repository, registry)
    return onchain_repository.upsert_entity(
        level='token', key='token:4663:0xtest', parent_id=project_ids['touch-grass'],
        attrs={'address': '0xtest'},
    )


def _transfer(block, index, sender, recipient, amount):
    return {
        'block': block, 'tx_hash': f'0x{index:064x}', 'log_index': index,
        'from_addr': sender, 'to_addr': recipient, 'amount': amount,
    }


class TestHolderDerivation:
    def test_balances_net_receipts_against_sends(self, onchain_repository, token_id):
        onchain_repository.insert_transfers(token_id, [
            _transfer(1, 0, ZERO, ALICE, 100 * HUGE),
            _transfer(2, 1, ALICE, BOB, 40 * HUGE),
            _transfer(3, 2, BOB, CAROL, 10 * HUGE),
        ])
        onchain_repository.commit()

        balances = dict(onchain_repository.holder_balances(token_id, exclude=[ZERO, DEAD]))
        assert balances == {ALICE: 60 * HUGE, BOB: 30 * HUGE, CAROL: 10 * HUGE}

    def test_an_address_that_sent_everything_back_is_not_a_holder(
        self, onchain_repository, token_id
    ):
        """The count out of this is a count of CURRENT holders, not of everyone
        who ever held -- which is the number a holder-count metric means."""
        onchain_repository.insert_transfers(token_id, [
            _transfer(1, 0, ZERO, ALICE, WAD),
            _transfer(2, 1, ALICE, BOB, WAD),
        ])
        onchain_repository.commit()
        balances = dict(onchain_repository.holder_balances(token_id, exclude=[ZERO, DEAD]))
        assert ALICE not in balances
        assert balances == {BOB: WAD}

    def test_burn_sinks_are_excluded_from_the_holder_set(
        self, onchain_repository, token_id
    ):
        """Counting a burn address as a holder would put the largest "holder" of
        a token with a burn at an address nobody controls."""
        onchain_repository.insert_transfers(token_id, [
            _transfer(1, 0, ZERO, ALICE, 10 * WAD),
            _transfer(2, 1, ALICE, DEAD, 4 * WAD),
        ])
        onchain_repository.commit()
        summary = transfers.holder_summary(onchain_repository, token_id)
        assert summary['holder_count'] == 1
        assert summary['top_holders'][0]['address'] == ALICE
        assert summary['circulating_from_transfers'] == str(6 * WAD)

    def test_the_top_ten_share_is_over_circulating_supply(
        self, onchain_repository, token_id
    ):
        rows = [_transfer(1, 0, ZERO, ALICE, 90 * WAD)]
        rows += [
            _transfer(2, index + 1, ZERO, '0x' + f'{index:040x}', WAD)
            for index in range(1, 11)
        ]
        onchain_repository.insert_transfers(token_id, rows)
        onchain_repository.commit()
        summary = transfers.holder_summary(onchain_repository, token_id)
        assert summary['holder_count'] == 11
        assert len(summary['top_holders']) == 10
        assert summary['top_holders'][0]['share'] == 0.9
        assert summary['top_ten_share'] == 0.99


class TestWindowCounts:
    @pytest.fixture(autouse=True)
    def rows(self, onchain_repository, token_id):
        onchain_repository.insert_transfers(token_id, [
            _transfer(10, 0, ZERO, ALICE, 10 * WAD),      # before the window
            _transfer(100, 1, ALICE, POOL, WAD),           # in the window
            _transfer(101, 2, POOL, BOB, WAD),             # in the window
            _transfer(102, 3, BOB, CAROL, WAD),            # in the window, not the pool
            _transfer(500, 4, ALICE, BOB, WAD),            # after the window
        ])
        onchain_repository.commit()

    def test_active_addresses_counts_both_sides_of_a_transfer(
        self, onchain_repository, token_id
    ):
        assert onchain_repository.active_addresses(token_id, 100, 102) == 4

    def test_pool_counterparties_excludes_the_pool_itself(
        self, onchain_repository, token_id
    ):
        """The counterpart to the provider's trade count is who traded, not how
        many transfers touched the pool -- so the pool's own address must not be
        one of its counterparties."""
        assert onchain_repository.pool_counterparties(token_id, [POOL], 100, 102) == 2

    def test_a_pool_type_with_no_address_has_no_counterparties(
        self, onchain_repository, token_id
    ):
        """A v4 pool with no hook and an unresolved manager address yields an
        empty address list, and an empty `= ANY('{}')` must return zero rather
        than every row."""
        assert onchain_repository.pool_counterparties(token_id, [], 100, 102) == 0

    def test_new_versus_returning_splits_by_first_sight(
        self, onchain_repository, token_id
    ):
        """The split is over RECEIVERS in the window, by whether the token had
        ever reached them before it. Alice first received at block 10, so a
        receipt inside the window makes her returning; the pool, Bob and Carol
        first received inside it. One holder splitting into twenty wallets shows
        up here as twenty new addresses on one day, which is the shape the
        holder count on its own cannot show."""
        onchain_repository.insert_transfers(token_id, [
            _transfer(102, 20, CAROL, ALICE, WAD)
        ])
        onchain_repository.commit()
        split = onchain_repository.new_versus_returning(token_id, 100, 102)
        assert split == {'new': 3, 'returning': 1}

    def test_burn_counts_both_sinks_and_subtracts_only_a_real_spend(
        self, onchain_repository, token_id
    ):
        """Regression, and the reason the two sinks are not symmetric: the
        fixture already mints 10 WAD *from* `0x0` at block 10. Treating that as
        an outflow from a sink -- which the first version of this query did --
        made the burn figure negative by the whole minted supply. A spend out of
        `0x…dEaD` is a real un-burn and is still subtracted."""
        onchain_repository.insert_transfers(token_id, [
            _transfer(200, 10, ALICE, DEAD, 3 * WAD),
            _transfer(201, 11, ALICE, ZERO, 2 * WAD),
            _transfer(202, 12, DEAD, ALICE, WAD),
        ])
        onchain_repository.commit()
        burned = onchain_repository.burned_amount(
            token_id, list(transfers.burn_addresses()), mint_source=ZERO
        )
        assert burned == 4 * WAD

    def test_a_mint_alone_is_not_a_negative_burn(self, onchain_repository, token_id):
        burned = onchain_repository.burned_amount(
            token_id, list(transfers.burn_addresses()), mint_source=ZERO
        )
        assert burned == 0


class TestFetchCursor:
    @pytest.mark.asyncio
    async def test_the_first_advance_starts_at_the_creation_block(
        self, onchain_repository, token_id
    ):
        seen = {}

        async def fake_fetch(client, query, from_block, to_block, **kwargs):
            seen['range'] = (from_block, to_block)
            return [], []

        import src.service.onchain.collectors.transfers as module

        original = module.fetch_window
        module.fetch_window = fake_fetch
        try:
            outcome = await transfers.advance_transfers(
                onchain_repository, None, token_entity_id=token_id,
                token_address='0x' + 'aa' * 20, creation_block=5000, to_block=6000,
            )
        finally:
            module.fetch_window = original

        assert seen['range'] == (5000, 6000)
        assert outcome.resumed is False
        assert onchain_repository.get_fetch_cursor(token_id, transfers.STREAM_TRANSFER) == 6000

    @pytest.mark.asyncio
    async def test_a_later_advance_resumes_from_the_cursor(
        self, onchain_repository, token_id
    ):
        """What makes the first build's ~30M-block walk survive an interruption:
        the second run asks for the blocks after the last committed window, not
        for the whole history again."""
        onchain_repository.set_fetch_cursor(token_id, transfers.STREAM_TRANSFER, 6000)
        onchain_repository.commit()
        seen = {}

        async def fake_fetch(client, query, from_block, to_block, **kwargs):
            seen['range'] = (from_block, to_block)
            return [], []

        import src.service.onchain.collectors.transfers as module

        original = module.fetch_window
        module.fetch_window = fake_fetch
        try:
            outcome = await transfers.advance_transfers(
                onchain_repository, None, token_entity_id=token_id,
                token_address='0x' + 'aa' * 20, creation_block=5000, to_block=9000,
            )
        finally:
            module.fetch_window = original

        assert seen['range'] == (6001, 9000)
        assert outcome.resumed is True

    @pytest.mark.asyncio
    async def test_nothing_new_to_fetch_is_not_a_request(
        self, onchain_repository, token_id
    ):
        onchain_repository.set_fetch_cursor(token_id, transfers.STREAM_TRANSFER, 9000)
        onchain_repository.commit()
        outcome = await transfers.advance_transfers(
            onchain_repository, None, token_entity_id=token_id,
            token_address='0x' + 'aa' * 20, creation_block=5000, to_block=9000,
        )
        assert outcome.windows == 0 and outcome.fetched == 0

    def test_the_cursor_never_moves_backwards(self, onchain_repository, token_id):
        onchain_repository.set_fetch_cursor(token_id, transfers.STREAM_TRANSFER, 9000)
        onchain_repository.set_fetch_cursor(token_id, transfers.STREAM_TRANSFER, 100)
        onchain_repository.commit()
        assert onchain_repository.get_fetch_cursor(token_id, transfers.STREAM_TRANSFER) == 9000
