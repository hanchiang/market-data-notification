"""The owner-attribution join must not scan the chain-wide position manager.

The position manager carries every project's ERC-721 Transfers, so a full-history
scan of it is both enormous and pointless: the join matches an NFT mint to a
liquidity event in the SAME TRANSACTION, so only blocks holding this pool's events
can contribute. These tests pin that restriction, because losing it does not fail --
it silently turns one build into an hours-long walk (observed 2026-09-08 on
Robinhood Chain, where the window collapsed to 5,859 blocks with 3.9M left to go).
"""
import pytest

from src.service.onchain.collectors import health


def _log(block: int) -> dict:
    return {'blockNumber': hex(block)}


class TestEventRanges:
    def test_no_events_produce_no_ranges(self):
        assert health._event_ranges([]) == []

    def test_one_event_produces_a_single_block_range(self):
        assert health._event_ranges([_log(100)]) == [(100, 100)]

    def test_nearby_blocks_coalesce_into_one_range(self):
        gap = health.NFT_RANGE_GAP_BLOCKS
        ranges = health._event_ranges([_log(100), _log(100 + gap)])
        assert ranges == [(100, 100 + gap)]

    def test_distant_blocks_stay_separate(self):
        gap = health.NFT_RANGE_GAP_BLOCKS
        ranges = health._event_ranges([_log(100), _log(100 + gap + 1)])
        assert ranges == [(100, 100), (100 + gap + 1, 100 + gap + 1)]

    def test_ranges_are_ordered_and_deduplicated(self):
        ranges = health._event_ranges([_log(900_000), _log(100), _log(100)])
        assert ranges == [(100, 100), (900_000, 900_000)]


class FakeContext:
    """Only what `_fetch_owner_transfers` touches."""

    block = 57_000_000
    log_client = object()

    def charge_logs(self, requests):
        """Spend accounting is P2-2's concern, not this test's."""


class TestFetchOwnerTransfers:
    @pytest.fixture
    def windows(self, monkeypatch):
        seen = []

        async def fake_fetch_window(client, query, from_block, to_block):
            seen.append((query.name, from_block, to_block))
            return [{'blockNumber': hex(from_block)}], []

        monkeypatch.setattr(
            'src.service.project_monitor.logs.fetch_window', fake_fetch_window
        )
        return seen

    @pytest.mark.asyncio
    async def test_a_pool_with_no_liquidity_events_issues_no_query(self, windows):
        logs = await health._fetch_owner_transfers(
            FakeContext(), 'v4_nft:x', '0xmanager', []
        )
        assert logs == []
        assert windows == []

    @pytest.mark.asyncio
    async def test_each_coalesced_range_is_fetched_and_head_is_never_the_bound(
        self, windows
    ):
        events = [_log(1_000), _log(40_000_000)]
        await health._fetch_owner_transfers(
            FakeContext(), 'v4_nft:x', '0xmanager', events
        )
        assert windows == [
            ('v4_nft:x', 1_000, 1_000),
            ('v4_nft:x', 40_000_000, 40_000_000),
        ]
        # The regression: the old code passed `context.block` as the upper bound,
        # so a single query covered 57M blocks of an unrelated stream.
        assert all(to_block != FakeContext.block for _, _, to_block in windows)


class TestOwnerResolution:
    """A burned NFT reverts, and a revert inside a JSON-RPC batch fails the WHOLE
    batch rather than one member of it. Observed 2026-09-08 on predict-fwa, where
    "ERC721: owner query for nonexistent token" failed the entire health section
    for a position that had simply been closed.
    """

    class _Positions(list):
        pass

    def _position(self, token_id, owner='0xalice'):
        from src.service.onchain.collectors import uniswap

        return uniswap.Position(
            owner=owner, tick_lower=-60, tick_upper=60, salt=None,
            liquidity=10, nft_token_ids=[token_id],
        )

    @pytest.mark.asyncio
    async def test_a_reverting_token_costs_only_its_own_owner(self, monkeypatch):
        from market_data_library.core.onchain.evm.errors import EvmRpcError

        calls_made = []

        class Client:
            endpoint = type('E', (), {'kind': 'alchemy'})()

            async def batch_call(self, calls, block, *, expect_value=True):
                raise EvmRpcError('execution reverted', endpoint_kind='alchemy')

            async def call(self, to, data, block, *, expect_value=True):
                calls_made.append(data)
                if len(calls_made) == 1:
                    raise EvmRpcError('execution reverted', endpoint_kind='alchemy')
                return ('0x' + '0' * 24 + 'bb' * 20, object())

        class Context:
            block = 100
            state_client = Client()
            project = type('P', (), {'key': 'x'})()

            def record_jsonrpc(self, raw):
                pass

        owners = await health._resolve_owners(
            Context(), '0xmanager', [self._position(1), self._position(2)]
        )
        assert list(owners) == [2]
        assert owners[2] == '0x' + 'bb' * 20
        # One read per token, not one read that stopped at the bad one.
        assert len(calls_made) == 2

    @pytest.mark.asyncio
    async def test_both_the_batch_and_the_fallback_ask_for_a_missing_value(
        self, monkeypatch
    ):
        """`expect_value=False` is what lets an empty `0x` come back as a value
        rather than as an error. The fakes above take it as a keyword default, so
        a regression to `expect_value=True` -- which the collector's own docstring
        says is wrong -- changes nothing they assert. Read it off the call."""
        from market_data_library.core.onchain.evm.errors import EvmRpcError

        flags = []

        class Client:
            endpoint = type('E', (), {'kind': 'alchemy'})()

            async def batch_call(self, calls, block, *, expect_value=True):
                flags.append(('batch', expect_value))
                raise EvmRpcError('execution reverted', endpoint_kind='alchemy')

            async def call(self, to, data, block, *, expect_value=True):
                flags.append(('single', expect_value))
                return ('0x' + '0' * 24 + 'bb' * 20, object())

        class Context:
            block = 100
            state_client = Client()
            project = type('P', (), {'key': 'x'})()

            def record_jsonrpc(self, raw):
                pass

        await health._resolve_owners(Context(), '0xmanager', [self._position(1)])
        assert flags == [('batch', False), ('single', False)]

    @pytest.mark.asyncio
    async def test_an_unresolved_token_keeps_its_provisional_holder(self):
        """The half `_resolve_owners` cannot show on its own: a token whose read
        failed must be absent from the mapping, so `apply_resolved_owners` leaves
        the log-derived holder in place rather than blanking it."""
        from market_data_library.core.onchain.evm.errors import EvmRpcError
        from src.service.onchain.collectors import uniswap

        class Client:
            endpoint = type('E', (), {'kind': 'alchemy'})()

            async def batch_call(self, calls, block, *, expect_value=True):
                raise EvmRpcError('reverted', endpoint_kind='alchemy')

            async def call(self, to, data, block, *, expect_value=True):
                raise EvmRpcError('reverted', endpoint_kind='alchemy')

        class Context:
            block = 100
            state_client = Client()
            project = type('P', (), {'key': 'x'})()

            def record_jsonrpc(self, raw):
                pass

        positions = [self._position(1, owner='0xprovisional')]
        owners = await health._resolve_owners(Context(), '0xmanager', positions)
        assert owners == {}
        assert uniswap.apply_resolved_owners(positions, owners)[0].owner == '0xprovisional'

    @pytest.mark.asyncio
    async def test_no_open_position_issues_no_read(self):
        assert await health._resolve_owners(object(), '0xmanager', []) == {}


# --------------------------------------------------------------------------
# The scan bound and the conservation oracle.
#
# Two things escaped a green suite here on 2026-09-08 and neither had a test:
#
#   * `_custody_v3`/`_custody_v4` walked the chain from block 0 and did not
#     terminate. The trigger was identity carrying `unavailable` forward, which
#     `_creation_block` maps to 0 -- but the class of defect is wider than that
#     one trigger, and nothing anywhere asserts what block a custody scan starts
#     at. `TestCustodyScanBounds` pins it, so a start block that regresses to 0,
#     or a resume that ignores the stored cursor, goes red.
#   * The custody netting counted deposits that had already been withdrawn. The
#     strongest available oracle for the whole subsystem -- that a pool with one
#     open position nets to exactly the pool's own `getLiquidity` -- was written
#     down as prose in `uniswap_test.TestWithdrawalNetting`'s docstring and
#     executed by nothing. `TestCustodyConservation` executes it, through the
#     real store, which is also the only test that drives the collector's whole
#     fetch -> normalise -> attribute -> persist -> re-read -> net path.
# --------------------------------------------------------------------------

CREATION_BLOCK = 53_000_000
MANAGER = '0x' + '33' * 20
POOL_MANAGER = '0x' + '44' * 20
STATE_VIEW = '0x' + '55' * 20
V3_MANAGER = '0x' + '66' * 20
POOL_ID = '0x' + 'ab' * 32
POOL_ADDRESS = '0x' + '77' * 20
ALICE = '0x' + '11' * 20


def _v4_modify_log(*, delta, token_id, sender, block, index=0):
    return {
        'topics': [
            health.uniswap.V4_MODIFY_LIQUIDITY.topic0,
            POOL_ID,
            '0x' + '0' * 24 + sender[2:],
        ],
        'data': '0x' + (
            f'{-60 & ((1 << 256) - 1):064x}'
            f'{60:064x}'
            f'{delta & ((1 << 256) - 1):064x}'
            f'{token_id:064x}'
        ),
        'blockNumber': hex(block),
        'logIndex': hex(index),
        'transactionHash': '0x' + f'{block * 100 + index:064x}',
    }


def _erc721_mint_log(*, token_id, to, block, index):
    return {
        'topics': [
            health.uniswap.ERC721_TRANSFER.topic0,
            '0x' + '0' * 64,
            '0x' + '0' * 24 + to[2:],
            '0x' + f'{token_id:064x}',
        ],
        'data': '0x',
        'blockNumber': hex(block),
        'logIndex': hex(index),
        'transactionHash': '0x' + f'{block * 100 + 0:064x}',
    }


class _Repo:
    """Only the four repository calls custody makes, with the cursor in memory."""

    def __init__(self, cursor=None):
        self.cursor = cursor
        self.set_cursors = []
        self.stored = []

    def get_fetch_cursor(self, entity_id, stream):
        return self.cursor

    def set_fetch_cursor(self, entity_id, stream, block):
        self.set_cursors.append((stream, block))

    def insert_position_events(self, pool_id, rows):
        self.stored.extend(rows)
        return len(rows)

    def get_position_events(self, pool_id):
        return list(self.stored)


class _CustodyContext:
    """A build context with the chain reads faked and the log fetch spied on."""

    block = 57_000_000

    def __init__(self, *, version, cursor=None, pool_liquidity=1000, owner=ALICE):
        self.identity = {'pool_id': POOL_ID, 'pool_address': POOL_ADDRESS}
        self.repository = _Repo(cursor)
        self.log_client = object()
        self.state_client = self._StateClient(pool_liquidity, owner)
        self.project = type('P', (), {'key': 'touch-grass', 'pool_ref': POOL_ID})()
        self.chain = type('C', (), {
            'uniswap': {
                'v4_pool_manager': POOL_MANAGER,
                'v4_position_manager': MANAGER,
                'v4_state_view': STATE_VIEW,
                'v3_position_manager': V3_MANAGER,
            },
            'lockers': [],
        })()
        self.charged_logs = 0

    class _StateClient:
        endpoint = type('E', (), {'kind': 'alchemy'})()

        def __init__(self, pool_liquidity, owner):
            self.pool_liquidity = pool_liquidity
            self.owner = owner

        async def call(self, to, data, block, *, expect_value=True):
            return ('0x' + f'{self.pool_liquidity:064x}', object())

        async def batch_call(self, calls, block, *, expect_value=True):
            return [
                ('0x' + '0' * 24 + self.owner[2:], object()) for _ in calls
            ]

    def charge_logs(self, requests):
        self.charged_logs += requests

    def charge(self, kind, count, methods=None):
        pass

    def record_jsonrpc(self, raw):
        pass


@pytest.fixture
def scan(monkeypatch):
    """Records every log window the collector asks for, keyed by query name."""
    asked = []
    served = {}

    async def fake_fetch_window(client, query, from_block, to_block, **kwargs):
        asked.append((query.name, from_block, to_block))
        return list(served.get(query.name, [])), []

    monkeypatch.setattr(
        'src.service.project_monitor.logs.fetch_window', fake_fetch_window
    )
    return type('Scan', (), {'asked': asked, 'served': served})()


class TestCreationBlockFallback:
    """F4 (test round 1): `TestCustodyScanBounds` below always passes an
    explicit `creation_block=`, so `_creation_block` itself -- the one place
    that maps identity's `creation_block` field to a scan's start -- had only
    the identity carry-forward test (M9/M10) standing between a non-int value
    and a genesis walk, one layer away from the code that actually walks.

    The fallback to 0 is deliberate ("slow and correct" per the function's own
    docstring), not a bug to fail the section over, so this pins the choice
    directly at the layer that makes it rather than reversing it.
    """

    def test_a_resolved_int_creation_block_is_used_as_is(self):
        context = type('C', (), {'identity': {'creation_block': 53_000_000}})()
        assert health._creation_block(context) == 53_000_000

    @pytest.mark.parametrize(
        'value', ['unavailable', None, 53_000_000.5, [53_000_000]],
        ids=['string', 'none', 'float', 'list'],
    )
    def test_anything_other_than_an_int_falls_back_to_genesis(self, value):
        context = type('C', (), {'identity': {'creation_block': value}})()
        assert health._creation_block(context) == 0

    def test_a_missing_creation_block_key_falls_back_to_genesis(self):
        context = type('C', (), {'identity': {}})()
        assert health._creation_block(context) == 0


class TestCustodyScanBounds:
    @pytest.mark.asyncio
    async def test_a_first_v4_scan_starts_at_the_creation_block_not_at_genesis(
        self, scan
    ):
        context = _CustodyContext(version='v4')
        await health._custody_v4(context, pool_entity_id=1, creation_block=CREATION_BLOCK)

        pool_scan = next(w for w in scan.asked if w[0].startswith('v4_modify'))
        assert pool_scan[1] == CREATION_BLOCK
        assert pool_scan[2] == context.block

    @pytest.mark.asyncio
    async def test_a_first_v3_scan_starts_at_the_creation_block_not_at_genesis(
        self, scan
    ):
        context = _CustodyContext(version='v3')
        await health._custody_v3(context, pool_entity_id=1, creation_block=CREATION_BLOCK)

        for name in ('v3_mint', 'v3_burn'):
            window = next(w for w in scan.asked if w[0].startswith(name))
            assert window[1] == CREATION_BLOCK, name
            assert window[2] == context.block, name

    @pytest.mark.asyncio
    @pytest.mark.parametrize('version', ['v3', 'v4'])
    async def test_a_resumed_scan_starts_one_past_the_stored_cursor(
        self, scan, version
    ):
        """The cursor is what keeps a nightly build from re-walking the whole
        history. Ignoring it is not a wrong answer -- it is the unbounded walk
        again, arriving by a different route."""
        context = _CustodyContext(version=version, cursor=56_000_000)
        collect = health._custody_v4 if version == 'v4' else health._custody_v3
        await collect(context, pool_entity_id=1, creation_block=CREATION_BLOCK)

        pool_scans = [
            w for w in scan.asked if w[0].startswith(f'{version}_modify')
            or w[0].startswith(f'{version}_mint')
        ]
        assert pool_scans, scan.asked
        for window in pool_scans:
            assert window[1] == 56_000_001

    @pytest.mark.asyncio
    async def test_the_chain_wide_manager_is_never_scanned_over_the_whole_range(
        self, scan
    ):
        """The position manager carries every project's events. Its windows must
        be the pool's own event blocks, never the pool scan's full span -- that
        is the walk that did not finish on 2026-09-08."""
        context = _CustodyContext(version='v4')
        scan.served['v4_modify:touch-grass'] = [
            _v4_modify_log(delta=1000, token_id=7, sender=MANAGER, block=53_100_000)
        ]
        await health._custody_v4(context, pool_entity_id=1, creation_block=CREATION_BLOCK)

        nft_windows = [w for w in scan.asked if w[0].startswith('v4_nft')]
        assert nft_windows
        for _, low, high in nft_windows:
            assert low >= 53_100_000
            assert high - low <= health.NFT_RANGE_GAP_BLOCKS


class TestCustodyConservation:
    """The oracle: for a pool whose one open position spans the current tick, the
    netted position total must equal the pool's own reported liquidity. The
    un-netted sum does not, which is what makes this the check the shipped defect
    would have failed -- see `uniswap_test.TestWithdrawalNetting` for the real
    figures it is drawn from.
    """

    LIQUIDITY = 29277002188455995842192
    WITHDRAWN = 2409283290909037820703

    @pytest.mark.asyncio
    async def test_a_deposit_and_its_withdrawal_net_to_the_pools_own_liquidity(
        self, scan
    ):
        context = _CustodyContext(
            version='v4', pool_liquidity=self.LIQUIDITY, owner=ALICE
        )
        # One position: deposited in two parts, partly withdrawn. The withdrawal
        # is emitted by the manager with no ERC-721 transfer of its own, which is
        # the shape that used to land under a different owner and never net.
        scan.served['v4_modify:touch-grass'] = [
            _v4_modify_log(
                delta=self.LIQUIDITY + self.WITHDRAWN, token_id=7,
                sender=MANAGER, block=53_100_000,
            ),
            _v4_modify_log(
                delta=-self.WITHDRAWN, token_id=7,
                sender=MANAGER, block=53_200_000,
            ),
        ]
        scan.served['v4_nft:touch-grass'] = [
            _erc721_mint_log(token_id=7, to=ALICE, block=53_100_000, index=1)
        ]

        custody = await health._custody_v4(
            context, pool_entity_id=1, creation_block=CREATION_BLOCK
        )

        assert custody['open_positions'] == 1
        assert custody['position_liquidity_total'] == str(self.LIQUIDITY)
        assert custody['position_liquidity_total'] == custody['pool_liquidity']
        assert custody['largest_owner'] == ALICE.lower()
        assert custody['largest_owner_share'] == 1.0
