"""Transfer-derived fields must not report a number they could not compute.

`burned`, `top_holders`, `top_ten_share`, `pool_held_share` and `mint_path` are
sums over stored `Transfer` rows. When the log fetch fails they sum over what is
there, which is nothing or a fragment, and come out as `0`, `[]` and `fixed` --
answers rather than gaps.

Observed on run 2, 2026-09-10: `total_supply` fell 0.52% on a fixed-supply token
while `burned` read `0`, in a section reporting `ok`. An operator would chase a
loss that never happened. `mint_path` is worse: the threshold table watches it,
so a mintable token reads `fixed` on any night the fetch dies.
"""
import asyncio
from types import SimpleNamespace

import pytest
from market_data_library.core.onchain.evm import RawResponse

from src.service.onchain import diff
from src.service.onchain.collectors import token_economics, transfers, uniswap
from src.service.onchain.collectors.base import BuildContext
from src.service.onchain.observability import run_context
from src.service.onchain.registry import parse_registry, upsert_registry


class FakeRepository:
    """Only `get_fetch_cursor`, which is the whole decision input."""

    def __init__(self, cursor):
        self._cursor = cursor

    def get_fetch_cursor(self, entity_id, stream):
        return self._cursor


class FakeContext:
    def __init__(self, cursor, block=59_383_500):
        self.repository = FakeRepository(cursor)
        self.block = block


class TestTransferStoreLag:
    def test_a_cursor_at_the_pinned_block_is_current(self) -> None:
        context = FakeContext(cursor=59_383_500)
        assert token_economics._transfer_store_lag(context, 1) is None

    def test_a_cursor_past_the_pinned_block_is_current(self) -> None:
        # A later build's cursor is ahead of this run's pinned block. The rows
        # this run needs are all present, so nothing is missing.
        context = FakeContext(cursor=59_400_000)
        assert token_economics._transfer_store_lag(context, 1) is None

    def test_no_cursor_at_all_is_an_empty_store(self) -> None:
        context = FakeContext(cursor=None)
        assert token_economics._transfer_store_lag(context, 1) == 'TransferStoreEmpty'

    def test_a_cursor_one_block_short_is_behind(self) -> None:
        # One block short still means a partial sum, which is a confident wrong
        # number rather than an obviously missing one.
        context = FakeContext(cursor=59_383_499)
        assert token_economics._transfer_store_lag(context, 1) == 'TransferStoreBehind'

    def test_a_stale_marker_is_recognised_as_a_failure_by_the_diff(self) -> None:
        # The bug this whole file exists for is a value that LOOKS like data.
        # `is_failed` requires a Mapping, so a bare string marker would diff as
        # a literal change to "failed" instead of being skipped.
        marker = diff.failed_field('TransferStoreBehind')
        assert diff.is_failed(marker)


# --- Per-field markers, through `collect()` against real rows ------------------
#
# The three markers cover different fields (module docstring). The unit tests
# above check the decision inputs; these run the collector so a marker landing
# on the wrong field, or a computable figure being hidden, fails here.

PIN = 60_000_000
TOKEN = '0x' + 'aa' * 20
POOL = '0x' + 'bb' * 20
HOLDER = '0x' + 'cc' * 20
SUPPLY = 1_000_000 * 10 ** 18
POOL_HELD = 250_000 * 10 ** 18

class _StateClient:
    """`totalSupply` and the pool's `balanceOf`, the two state reads v3 makes,
    answered only for this token at this pin and only for the pool's address:
    a read of the wrong contract, block or holder raises instead of answering."""

    endpoint = SimpleNamespace(kind='alchemy')

    def __init__(self, supply=SUPPLY):
        self.supply = supply

    async def call(self, to, data, block, *, expect_value=True):
        assert to == TOKEN, to
        assert block == PIN, block
        if data == uniswap.call_data('totalSupply'):
            value = self.supply
        elif data == uniswap.call_data('balanceOf', ['address'], [POOL]):
            value = POOL_HELD
        else:
            raise AssertionError(f'unexpected call {data}')
        raw = RawResponse(
            method='eth_call', params=[{'to': to, 'data': data}, hex(block)],
            body={'jsonrpc': '2.0', 'id': 1, 'result': '0x' + f'{value:064x}'},
            endpoint_kind='alchemy',
        )
        return '0x' + f'{value:064x}', raw


@pytest.fixture
def build(onchain_repository, onchain_registry_payload):
    registry = parse_registry(onchain_registry_payload)
    project_ids = upsert_registry(onchain_repository, registry)

    def make(version='v3', supply=SUPPLY, **identity_extra):
        identity = {
            'token_address': TOKEN, 'decimals': 18, 'version': version,
            'privileged_selectors': [], 'creation_block': 1_000,
        }
        if version == 'v3':
            identity['pool_address'] = POOL
        else:
            # A served `Initialize` log: the tick-math inputs the v4 path needs.
            identity.update(pool_id='0x' + 'dd' * 32, tick=0, currency0=TOKEN)
        identity.update(identity_extra)
        context = BuildContext(
            repository=onchain_repository, registry=registry,
            chain=SimpleNamespace(chain_id=4663, lockers=[]),
            project=SimpleNamespace(
                key='touch-grass', archetype='launchpad-fixed-supply',
                pool_ref=POOL, display_name='Touch Grass',
            ),
            chain_entity_id=1, project_entity_id=project_ids['touch-grass'],
            pinned=SimpleNamespace(block=PIN, timestamp=0, window_start_block=PIN - 100,
                                   window_start_timestamp=0),
            state_client=_StateClient(supply), log_client=object(),
            dexscreener=None, explorer=None, identity=identity,
        )
        token_id = context.token_entity_id()
        onchain_repository.insert_transfers(token_id, [
            _transfer_row(2_000, 0, transfers.ZERO_ADDRESS, HOLDER, SUPPLY),
            _transfer_row(3_000, 1, HOLDER, transfers.DEAD_ADDRESS, 10 ** 18),
        ])
        onchain_repository.commit()
        return context, token_id

    return make


def _transfer_row(block, index, sender, recipient, amount):
    return {
        'block': block, 'tx_hash': f'0x{index:064x}', 'log_index': index,
        'from_addr': sender, 'to_addr': recipient, 'amount': amount,
    }


def _cursor(repository, entity_id, stream, block):
    repository.set_fetch_cursor(entity_id, stream, block)
    repository.commit()


def _collect(context):
    """Evidence rows refuse to be written outside a run (A12)."""
    run_id = context.repository.start_run('onchain.build')
    with run_context(run_id, 'onchain.build'):
        return asyncio.run(token_economics.collect(context))


class TestCollect:
    def test_a_current_verified_store_reports_every_field(self, build):
        context, token_id = build()
        _cursor(context.repository, token_id, transfers.STREAM_TRANSFER, PIN)
        _cursor(context.repository, token_id, transfers.STREAM_DERIVATION_VERIFIED, PIN)

        section = _collect(context)

        assert section.status == 'ok' and section.error_class is None
        assert section.fields['burned'] == str(10 ** 18)
        assert section.fields['mint_path'] == 'observed'
        assert section.fields['top_holders'][0]['address'] == HOLDER
        assert section.fields['pool_held_share'] == {
            'method': 'pool_balance_of', 'amount': str(POOL_HELD), 'share': 0.25,
        }

    def test_an_unverified_derivation_marks_the_transfer_sums_only(self, build):
        """The transfer walk reached the pin but health never proved the rows
        against `balanceOf` at this height (it raised, or its section rolled
        back). The five sums are marked; the v3 pool balance is a state read
        and stays a number."""
        context, token_id = build()
        _cursor(context.repository, token_id, transfers.STREAM_TRANSFER, PIN)

        section = _collect(context)

        assert section.status == 'partial'
        assert section.error_class == 'DerivationUnverified'
        for name in ('burned', 'burned_share', 'mint_path', 'top_holders', 'top_ten_share'):
            assert diff.is_failed(section.fields[name]), name
            assert section.fields[name]['error_class'] == 'DerivationUnverified'
        assert section.fields['pool_held_share']['method'] == 'pool_balance_of'

    def test_a_lagging_transfer_store_leaves_the_v3_pool_balance_readable(self, build):
        """Before 2026-09-11 `pool_held_share` shared the transfer markers and
        was hidden on every night the walk lagged, though it never read a
        transfer row."""
        context, token_id = build()
        _cursor(context.repository, token_id, transfers.STREAM_TRANSFER, PIN - 1)
        _cursor(context.repository, token_id, transfers.STREAM_DERIVATION_VERIFIED, PIN)

        section = _collect(context)

        assert section.error_class == 'TransferStoreBehind'
        assert diff.is_failed(section.fields['burned'])
        assert section.fields['pool_held_share']['amount'] == str(POOL_HELD)

    def test_a_v4_pool_with_no_position_rows_marks_only_the_pool_share(self, build):
        """The v4 path sums the position stream, so it is gated on that cursor
        and nothing else is: the transfer sums stay numbers."""
        context, token_id = build(version='v4')
        _cursor(context.repository, token_id, transfers.STREAM_TRANSFER, PIN)
        _cursor(context.repository, token_id, transfers.STREAM_DERIVATION_VERIFIED, PIN)

        section = _collect(context)

        assert section.status == 'partial'
        assert section.error_class == 'PositionStoreEmpty'
        assert section.fields['pool_held_share']['error_class'] == 'PositionStoreEmpty'
        assert section.fields['burned'] == str(10 ** 18)
        assert section.fields['top_holders'][0]['address'] == HOLDER

    def test_two_lags_name_the_transfer_one_as_the_section_error(self, build):
        context, token_id = build(version='v4')
        _cursor(context.repository, token_id, transfers.STREAM_TRANSFER, PIN - 1)

        section = _collect(context)

        assert section.error_class == 'TransferStoreBehind'
        assert section.fields['pool_held_share']['error_class'] == 'PositionStoreEmpty'

    def test_a_v4_pool_whose_key_never_resolved_is_unavailable_not_a_lag(self, build):
        """With the `Initialize` log unserved there is no tick and no currency
        order, so the v4 path cannot run and health's custody walk may never
        have written a position cursor. That absence is not a lag: the field
        says `unavailable` without forcing the section to `partial`."""
        context, token_id = build(version='v4', tick='unavailable', currency0='unavailable')
        _cursor(context.repository, token_id, transfers.STREAM_TRANSFER, PIN)
        _cursor(context.repository, token_id, transfers.STREAM_DERIVATION_VERIFIED, PIN)

        section = _collect(context)

        assert section.status == 'ok'
        assert section.fields['pool_held_share'] == 'unavailable'

    def test_a_zero_supply_reads_no_position_row_and_is_not_gated(self, build):
        context, token_id = build(version='v4', supply=0)
        _cursor(context.repository, token_id, transfers.STREAM_TRANSFER, PIN)
        _cursor(context.repository, token_id, transfers.STREAM_DERIVATION_VERIFIED, PIN)

        section = _collect(context)

        assert section.status == 'ok'
        assert section.fields['pool_held_share'] is None
