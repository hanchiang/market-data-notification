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

    @pytest.mark.asyncio
    async def test_no_open_position_issues_no_read(self):
        assert await health._resolve_owners(object(), '0xmanager', []) == {}
