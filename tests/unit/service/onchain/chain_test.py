"""Endpoint roles, the 24-hour boundary search, and the string decoder.

The binary search runs against a fake chain whose timestamps are exact, so the
assertion is about the SEARCH -- that it lands on the highest block at or before
the target and does not wander -- rather than about a real chain's jitter.
"""
import pytest

from src.runtime.runtime_mode import RuntimeMode
from src.service.onchain import chain
from src.service.onchain.config import ChainConstants
from src.service.onchain.spend import MeteredBudget, SpendLedger

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


LIVE = RuntimeMode.from_test_mode(False)
TEST = RuntimeMode.from_test_mode(True)


def _archive(monkeypatch):
    from market_data_library.core.onchain.evm import Endpoint

    monkeypatch.setattr(
        chain, 'get_archive_endpoint',
        lambda: Endpoint(kind='alchemy', url='https://example.invalid/key'),
    )


class TestEndpointRoles:
    def test_logs_go_to_the_public_endpoint_by_default(self, monkeypatch):
        """The free archive tier refuses `eth_getLogs` beyond ten blocks, two
        orders below the narrowest window the fetcher asks for."""
        _archive(monkeypatch)
        monkeypatch.delenv('ONCHAIN_LOG_ENDPOINT', raising=False)
        assert chain.log_role(LIVE, SpendLedger()).endpoint.kind == 'public'

    def test_the_setting_moves_logs_to_the_archive_endpoint(self, monkeypatch):
        """The backfill switch (`kb/decisions.md` 2026-09-10): on for the one
        pay-as-you-go window, off again afterwards."""
        _archive(monkeypatch)
        monkeypatch.setenv('ONCHAIN_LOG_ENDPOINT', 'archive')
        assert chain.log_role(LIVE, SpendLedger()).endpoint.kind == 'alchemy'

    def test_the_setting_cannot_move_a_test_run_onto_the_metered_account(self, monkeypatch):
        _archive(monkeypatch)
        monkeypatch.setenv('ONCHAIN_LOG_ENDPOINT', 'archive')
        assert chain.log_role(TEST, SpendLedger()).endpoint.kind == 'public'

    def test_the_setting_without_a_key_falls_back_to_public(self, monkeypatch):
        monkeypatch.setattr(chain, 'get_archive_endpoint', lambda: None)
        monkeypatch.setenv('ONCHAIN_LOG_ENDPOINT', 'archive')
        assert chain.log_role(LIVE, SpendLedger()).endpoint.kind == 'public'

    def test_a_misspelt_setting_is_refused_not_defaulted(self, monkeypatch):
        _archive(monkeypatch)
        monkeypatch.setenv('ONCHAIN_LOG_ENDPOINT', 'archvie')
        with pytest.raises(ValueError):
            chain.log_role(LIVE, SpendLedger())

    def test_test_mode_keeps_state_reads_off_the_metered_account(self, monkeypatch):
        _archive(monkeypatch)
        assert chain.state_role(TEST, SpendLedger()).endpoint.kind == 'public'

    def test_a_live_run_uses_the_archive_endpoint_when_one_is_configured(self, monkeypatch):
        _archive(monkeypatch)
        assert chain.state_role(LIVE, SpendLedger()).endpoint.kind == 'alchemy'

    def test_no_archive_key_falls_back_rather_than_raising(self, monkeypatch):
        monkeypatch.setattr(chain, 'get_archive_endpoint', lambda: None)
        assert chain.state_role(LIVE, SpendLedger()).endpoint.kind == 'public'

    def test_every_role_budget_is_metered(self, monkeypatch):
        """The count lives at `reserve`; a role handed an unwrapped budget
        would be the uncounted call site the ledger was rebuilt to remove."""
        _archive(monkeypatch)
        ledger = SpendLedger()
        for role in (chain.state_role(LIVE, ledger), chain.log_role(LIVE, ledger),
                     chain.state_role(TEST, ledger)):
            assert isinstance(role.budget, MeteredBudget)

    def test_the_archive_meter_carries_the_ceiling_and_month_to_date(self, monkeypatch):
        _archive(monkeypatch)
        monkeypatch.setenv('ONCHAIN_ALCHEMY_MONTHLY_CU_CEILING', '4200')
        role = chain.state_role(LIVE, SpendLedger(), alchemy_spent_this_month=17)
        assert role.budget.meter.ceiling_units == 4200
        assert role.budget.meter.spent_before_run == 17
        assert role.budget.meter.bills_units is True

    def test_the_public_meter_has_no_ceiling(self, monkeypatch):
        # `load_dotenv()` at import means a repo `.env` set for the backfill
        # (`ONCHAIN_LOG_ENDPOINT=archive` plus a key) reaches this test.
        monkeypatch.delenv('ONCHAIN_LOG_ENDPOINT', raising=False)
        monkeypatch.setattr(chain, 'get_archive_endpoint', lambda: None)
        role = chain.log_role(LIVE, SpendLedger())
        assert role.budget.meter.ceiling_units is None
        assert role.budget.meter.bills_units is False


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


class TestTheJobActuallyPassesBothClients:
    """`pin_block_and_window`'s `log_client` is OPTIONAL and defaults to None,
    and with it absent the function is exactly the pre-fix behaviour: pin on the
    state endpoint and read logs somewhere else. So dropping the third argument
    at the one call site restores the defect in full, and every test above --
    which calls the function directly with both clients -- stays green.

    The call site is what the tests above cannot reach; an AST read of it is
    cheaper than standing up two role clients and a registry to drive `run_build`.
    """

    def test_the_build_job_pins_with_the_log_client_as_well_as_the_state_client(self):
        import ast
        import pathlib

        from src.job.onchain import build as build_job

        tree = ast.parse(pathlib.Path(build_job.__file__).read_text())
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'pin_block_and_window'
        ]
        assert len(calls) == 1, 'expected exactly one pin site in the build job'
        passed = [
            arg.id for arg in calls[0].args if isinstance(arg, ast.Name)
        ] + [kw.arg for kw in calls[0].keywords]
        assert 'state_client' in passed
        assert 'log_client' in passed or 'log_client' in [
            kw.arg for kw in calls[0].keywords
        ]
