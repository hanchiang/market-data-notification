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
from src.service.onchain import diff
from src.service.onchain.collectors import token_economics


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
