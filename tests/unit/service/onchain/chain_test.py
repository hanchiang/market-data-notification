"""Endpoint roles, the 24-hour boundary search, and the string decoder.

The binary search runs against a fake chain whose timestamps are exact, so the
assertion is about the SEARCH -- that it lands on the highest block at or before
the target and does not wander -- rather than about a real chain's jitter.
"""
import pytest

from src.runtime.runtime_mode import RuntimeMode
from src.service.onchain import chain
from src.service.onchain.config import ChainConstants

CONSTANTS = ChainConstants(chain_id=4663, blocks_per_second=10.0, blocks_per_hour=36000)


class FakeChain:
    """A chain producing one block every `interval` seconds, exactly."""

    def __init__(self, head: int, head_timestamp: int, interval: float = 0.1):
        self.head = head
        self.head_timestamp = head_timestamp
        self.interval = interval
        self.reads = 0

    async def get_block_by_number(self, block, full_transactions=False):
        self.reads += 1
        timestamp = int(self.head_timestamp - (self.head - block) * self.interval)
        return {'number': hex(block), 'timestamp': hex(timestamp)}, None

    async def block_number(self):
        return self.head, None


class TestEndpointRoles:
    def test_logs_always_go_to_the_public_endpoint(self):
        """The free archive tier refuses `eth_getLogs` beyond ten blocks, two
        orders below the narrowest window the fetcher asks for."""
        assert chain.log_role().endpoint.kind == 'public'

    def test_test_mode_keeps_state_reads_off_the_metered_account(self):
        role = chain.state_role(RuntimeMode.from_test_mode(True))
        assert role.endpoint.kind == 'public'

    def test_a_live_run_uses_the_archive_endpoint_when_one_is_configured(self, monkeypatch):
        from market_data_library.core.onchain.evm import Endpoint

        monkeypatch.setattr(
            chain, 'get_archive_endpoint',
            lambda: Endpoint(kind='alchemy', url='https://example.invalid/key'),
        )
        assert chain.state_role(RuntimeMode.from_test_mode(False)).endpoint.kind == 'alchemy'

    def test_no_archive_key_falls_back_rather_than_raising(self, monkeypatch):
        monkeypatch.setattr(chain, 'get_archive_endpoint', lambda: None)
        assert chain.state_role(RuntimeMode.from_test_mode(False)).endpoint.kind == 'public'


class TestBoundarySearch:
    @pytest.mark.asyncio
    async def test_it_finds_the_highest_block_at_or_before_the_target(self):
        head, head_timestamp = 1_000_000, 2_000_000
        fake = FakeChain(head, head_timestamp, interval=0.1)
        target = head_timestamp - chain.SECONDS_PER_DAY
        block, timestamp, reads = await chain.find_block_at_or_before(
            fake, target, high_block=head, high_timestamp=head_timestamp,
            constants=CONSTANTS,
        )
        assert timestamp <= target
        following, _ = await fake.get_block_by_number(block + 1)
        assert int(following['timestamp'], 16) > target
        # The design budgets ~25 header reads for this boundary. The seed keeps
        # the bracket inside a day's blocks rather than the chain's whole
        # history, which is what makes the figure independent of chain age.
        assert reads <= 25

    @pytest.mark.asyncio
    async def test_a_chain_younger_than_the_window_returns_its_earliest_block(self):
        """"Everything so far" is the honest window for a token minted
        yesterday; inventing a negative block would not be."""
        fake = FakeChain(500, 2_000_000, interval=0.1)
        block, _, _ = await chain.find_block_at_or_before(
            fake, 2_000_000 - chain.SECONDS_PER_DAY, high_block=500,
            high_timestamp=2_000_000, constants=CONSTANTS,
        )
        assert block == 0

    @pytest.mark.asyncio
    async def test_a_drifted_block_rate_still_lands_on_the_right_block(self):
        """The seed is an estimate, not a promise: a chain running at half the
        recorded rate must not return a block on the wrong side of the target."""
        fake = FakeChain(1_000_000, 2_000_000, interval=0.2)
        target = 2_000_000 - chain.SECONDS_PER_DAY
        block, timestamp, _ = await chain.find_block_at_or_before(
            fake, target, high_block=1_000_000, high_timestamp=2_000_000,
            constants=CONSTANTS,
        )
        assert timestamp <= target
        following, _ = await fake.get_block_by_number(block + 1)
        assert int(following['timestamp'], 16) > target

    @pytest.mark.asyncio
    async def test_the_pinned_window_is_a_block_interval(self):
        fake = FakeChain(1_000_000, 2_000_000, interval=0.1)
        pinned = await chain.pin_block_and_window(fake, CONSTANTS)
        assert pinned.block == 1_000_000
        assert pinned.window_start_block < pinned.block
        assert pinned.timestamp - pinned.window_start_timestamp >= chain.SECONDS_PER_DAY


    @pytest.mark.asyncio
    async def test_the_run_pins_at_the_lower_of_the_two_endpoint_heads(self):
        """Logs come from a different node than state reads. A block the state
        endpoint has and the log node has not returns an EMPTY log range, not an
        error -- and the per-chunk cursor commit banks that gap as done."""
        state = FakeChain(1_000_000, 2_000_000, interval=0.1)
        logs = FakeChain(999_990, 2_000_000, interval=0.1)
        pinned = await chain.pin_block_and_window(state, CONSTANTS, logs)
        assert pinned.block == 999_990

    @pytest.mark.asyncio
    async def test_a_log_endpoint_ahead_of_the_state_endpoint_does_not_move_the_pin(self):
        """The state endpoint's head is the ceiling for state reads, so a log node
        further ahead cannot raise it."""
        state = FakeChain(1_000_000, 2_000_000, interval=0.1)
        logs = FakeChain(1_000_050, 2_000_000, interval=0.1)
        pinned = await chain.pin_block_and_window(state, CONSTANTS, logs)
        assert pinned.block == 1_000_000


class TestDecodeString:
    def test_a_dynamic_string_return_value_decodes(self):
        payload = (
            '0x' + f'{32:064x}' + f'{5:064x}' + b'GRASS'.hex().ljust(64, '0')
        )
        assert chain.decode_string(payload) == 'GRASS'

    def test_a_bytes32_symbol_decodes(self):
        """Some older tokens return a right-padded `bytes32` from `symbol()`.
        The library's decoder handles static types only, so both shapes have to
        be recognised here or the symbol reads as mojibake."""
        assert chain.decode_string('0x' + b'ZZZ'.hex().ljust(64, '0')) == 'ZZZ'

    def test_an_empty_return_value_is_none(self):
        assert chain.decode_string('0x') is None
        assert chain.decode_string('') is None


class TestSpendCounters:
    def test_counters_accumulate_per_endpoint_kind(self):
        spend = chain.spend_counters()
        chain.add_spend(spend, 'alchemy', requests=3, units=48)
        chain.add_spend(spend, 'alchemy', requests=2, units=32)
        chain.add_spend(spend, 'public', requests=5)
        assert spend['requests'] == {'alchemy': 5, 'public': 5}
        assert spend['compute_units'] == {'alchemy': 80}


class TestCreationSearchBounds:
    """The window the identity collector looks for a pool's creation log in.

    Scanning from genesis to head is what left predict-fwa's `PoolCreated`
    unresolved through two rounds: the public node rate-limits long before a
    57M-block walk reaches the pool. The provider dates the pair, so the log is
    within hours of that timestamp and both ends can be bounded.
    """

    @pytest.mark.asyncio
    async def test_the_window_brackets_the_block_the_pool_was_created_in(self):
        from src.service.onchain.collectors.identity import creation_search_bounds

        head, head_timestamp = 40_000_000, 1_800_000_000
        fake = FakeChain(head, head_timestamp)
        # A pool created ~11.5 days of chain ago, dated by the provider in ms.
        created_timestamp = head_timestamp - 1_000_000
        created_block = head - 10_000_000
        from_block, to_block, reads = await creation_search_bounds(
            fake, created_timestamp * 1000,
            head=head, head_timestamp=head_timestamp, constants=CONSTANTS,
        )
        assert from_block < created_block < to_block
        assert to_block - from_block < head // 10
        assert reads < 40

    @pytest.mark.asyncio
    async def test_a_pair_the_provider_does_not_date_falls_back_to_the_whole_chain(self):
        """A bound invented from no timestamp could exclude the very log being
        looked for, so the honest answer is the old unbounded scan."""
        from src.service.onchain.collectors.identity import creation_search_bounds

        fake = FakeChain(40_000_000, 1_800_000_000)
        assert await creation_search_bounds(
            fake, None, head=40_000_000, head_timestamp=1_800_000_000,
            constants=CONSTANTS,
        ) == (0, 40_000_000, 0)
