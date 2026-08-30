"""Store behaviour against a real Postgres: R8's atomicity, the single-writer
lock, exact amounts, and insert-or-ignore.
"""
import time

import pytest

from src.service.project_monitor import recorder
from src.service.project_monitor.repository import LockNotAcquiredError

PROJECT = 'NETNET'


def _sample_result(block: int, epoch: int = 100, readings=None):
    return recorder.SampleResult(
        block=block,
        block_timestamp=1788079000 + block,
        epoch_number=epoch,
        endpoint_kind='public',
        readings=readings
        or [
            {
                'name': 'Treasury.rfv', 'contract': 'treasury', 'tier': 'core',
                'raw_hex': '0x01', 'value_int': 4945643465655181262198189,
                'value_json': None, 'decimals': 18, 'state': 'ok',
                'error_class': None,
            }
        ],
    )


def test_ddl_is_idempotent(repository):
    """`CREATE TABLE IF NOT EXISTS` runs on every connect, so it has to survive
    running against a database that already has the schema."""
    repository.create_schema()
    repository.create_schema()
    assert repository.fetch_all('SELECT 1 AS ok')[0]['ok'] == 1


def test_amounts_survive_a_round_trip_at_uint256_scale(repository):
    """`numeric(78,0)`, not float. A treasury figure wrong in its last places is
    worse than one that is missing, because it looks fine."""
    run_id = repository.start_run('test')
    huge = (1 << 255) + 12345  # beyond float64's exact range by many orders
    sample_id = repository.insert_sample(
        run_id=run_id, project=PROJECT, block=1, block_timestamp=1,
        epoch_number=1, kind='live', endpoint_kind='public',
    )
    repository.insert_readings(
        sample_id,
        [{
            'name': 'huge', 'contract': 'x', 'tier': 'core', 'raw_hex': None,
            'value_int': huge, 'value_json': None, 'decimals': 0, 'state': 'ok',
            'error_class': None,
        }],
    )
    repository.commit()
    stored = repository.fetch_all('SELECT value_int FROM reading')[0]['value_int']
    assert int(stored) == huge


def test_a_committed_sample_advances_the_cursor(repository):
    run_id = repository.start_run('test')
    assert repository.get_live_cursor(PROJECT) is None
    recorder.commit_sample(
        repository, run_id=run_id, project_name=PROJECT,
        sample=_sample_result(500), kind=recorder.KIND_LIVE,
    )
    assert repository.get_live_cursor(PROJECT) == 500


def test_a_backfill_sample_written_later_does_not_rewind_the_cursor(repository):
    """The cursor is `max(block)` over live samples, never the last row.

    Backfill writes older rows after newer live ones. Taking "the most recent
    row" would rewind the log window over ground already covered -- and because
    the log tables are insert-or-ignore, that damage would be silent.
    """
    run_id = repository.start_run('test')
    recorder.commit_sample(
        repository, run_id=run_id, project_name=PROJECT,
        sample=_sample_result(900, epoch=110), kind=recorder.KIND_LIVE,
    )
    recorder.commit_sample(
        repository, run_id=run_id, project_name=PROJECT,
        sample=_sample_result(100, epoch=90), kind=recorder.KIND_BACKFILL,
    )
    assert repository.get_live_cursor(PROJECT) == 900


def test_a_core_failure_leaves_no_sample_row_and_an_unchanged_cursor(
    repository, second_repository
):
    """AC7, first half. Verified by reading the table back on a FRESH
    connection: asserting on the same connection that did the rollback would
    pass even if the rows were merely invisible to it."""
    run_id = repository.start_run('test')
    recorder.commit_sample(
        repository, run_id=run_id, project_name=PROJECT,
        sample=_sample_result(400, epoch=100), kind=recorder.KIND_LIVE,
    )
    cursor_before = repository.get_live_cursor(PROJECT)

    sample_id = repository.insert_sample(
        run_id=run_id, project=PROJECT, block=500, block_timestamp=1,
        epoch_number=101, kind='live', endpoint_kind='public',
    )
    repository.insert_readings(
        sample_id,
        [{
            'name': 'partial', 'contract': 'x', 'tier': 'core', 'raw_hex': '0x1',
            'value_int': 1, 'value_json': None, 'decimals': 0, 'state': 'ok',
            'error_class': None,
        }],
    )
    # The core read fails here, after some readings are already written.
    repository.rollback()

    blocks = [
        row['block']
        for row in second_repository.fetch_all(
            'SELECT block FROM sample WHERE project = %s', (PROJECT,)
        )
    ]
    assert blocks == [400]
    assert second_repository.fetch_all(
        'SELECT count(*) AS n FROM reading WHERE name = %s', ('partial',)
    )[0]['n'] == 0
    assert second_repository.get_live_cursor(PROJECT) == cursor_before == 400


def test_a_peripheral_failure_still_commits_the_sample(repository):
    """AC7, second half: the sample row and every other reading exist, the
    failed one is recorded as `failed`, and the run row names it."""
    run_id = repository.start_run('test')
    readings = [
        {
            'name': 'Treasury.rfv', 'contract': 'treasury', 'tier': 'core',
            'raw_hex': '0x1', 'value_int': 100, 'value_json': None, 'decimals': 18,
            'state': 'ok', 'error_class': None,
        },
        {
            'name': 'Mark.NVDA.latestRoundData', 'contract': 'NVDA_feed',
            'tier': 'peripheral', 'raw_hex': None, 'value_int': None,
            'value_json': None, 'decimals': None, 'state': 'failed',
            'error_class': 'EvmRpcError',
        },
    ]
    sample = _sample_result(700, epoch=120, readings=readings)
    sample.failed_peripheral = ['Mark.NVDA.latestRoundData']
    recorder.commit_sample(
        repository, run_id=run_id, project_name=PROJECT, sample=sample,
        kind=recorder.KIND_LIVE,
    )
    repository.finish_run(
        run_id, outcome='ok',
        notes='failed peripheral reads: ' + ', '.join(sample.failed_peripheral),
    )

    rows = repository.fetch_all('SELECT name, state, error_class FROM reading')
    assert {r['name']: r['state'] for r in rows} == {
        'Treasury.rfv': 'ok', 'Mark.NVDA.latestRoundData': 'failed',
    }
    # A failure is recorded as a state, never as a null that reads like a zero.
    failed = next(r for r in rows if r['state'] == 'failed')
    assert failed['error_class'] == 'EvmRpcError'
    run = repository.fetch_all('SELECT notes FROM run WHERE id = %s', (run_id,))[0]
    assert 'Mark.NVDA.latestRoundData' in run['notes']
    assert repository.get_live_cursor(PROJECT) == 700


def test_overlapping_log_windows_do_not_double_count(repository):
    """Backfill and live windows can overlap; `(tx_hash, log_index)` is what
    makes that harmless rather than inflating emission."""
    rows = [{
        'project': PROJECT, 'block': 10, 'tx_hash': '0xabc', 'log_index': 3,
        'recipient': '0x1', 'amount': 5, 'decimals': 9, 'class': 'bond',
    }]
    assert repository.insert_mints(rows) == 1
    assert repository.insert_mints(rows) == 0  # the same log, seen twice
    repository.commit()
    assert repository.fetch_all('SELECT count(*) AS n FROM mint')[0]['n'] == 1


def test_a_second_run_cannot_take_the_advisory_lock(repository, second_repository):
    """Cron does not serialise a run that outlasts its hour, which is the case
    this guards. A second connection, not a second cursor: the lock is
    session-scoped, so a second cursor would take it happily."""
    with repository.advisory_lock():
        with pytest.raises(LockNotAcquiredError):
            with second_repository.advisory_lock():
                pass


def test_the_lock_is_released_when_the_run_ends(repository, second_repository):
    """The guard must not fire on correct input: a sequential run takes it."""
    with repository.advisory_lock():
        pass
    with second_repository.advisory_lock():
        pass


def test_a_killed_connection_leaves_no_lock_behind(repository, second_repository):
    """Why an advisory lock beats the lock file it replaces: the server releases
    it when the connection dies, so a killed run needs no manual cleanup."""
    second_repository.connection.execute(
        'SELECT pg_try_advisory_lock(%s)',
        (__import__(
            'src.service.project_monitor.repository', fromlist=['ADVISORY_LOCK_KEY']
        ).ADVISORY_LOCK_KEY,),
    )
    second_repository.connection.commit()
    with pytest.raises(LockNotAcquiredError):
        with repository.advisory_lock():
            pass
    second_repository.close()  # the "kill"

    # Polled, not asserted once. `close()` returns as soon as the client socket
    # is shut; the server releases session locks only when that backend process
    # actually exits, so for a short window the lock is held by a connection
    # that is already gone. Measured under a full-suite run: the immediate
    # retake failed in roughly 1 run in 3, and one retry ~2.4ms later always
    # succeeded. Asserting it once made this test flaky rather than wrong -- the
    # property really is "the server releases it", and "instantly" was never
    # part of it. Production is unaffected: this job runs hourly, so no real run
    # retakes the lock milliseconds after a peer was killed.
    deadline = time.monotonic() + 5
    while True:
        try:
            with repository.advisory_lock():
                pass
            break
        except LockNotAcquiredError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.01)


def _window(boundaries):
    return {
        'raw_responses': [], 'mints': [], 'flows': [], 'events': [],
        'boundaries': boundaries, 'queries': [],
    }


def test_a_window_with_two_rebases_names_only_the_epoch_it_observed(repository):
    """P2-1. A sample observes exactly ONE epoch, so it can name exactly one
    boundary: the latest in the window. A window spanning two rebases -- an
    outage longer than one 8 h epoch -- also discovers the earlier boundary, but
    the sample says nothing about which epoch that one opened.

    Stamping the sample's number onto both mislabels the earlier boundary, and
    `upsert_epoch_boundary` COALESCEs on update, so the wrong value is then
    frozen against every later correction.
    """
    run_id = repository.start_run('test')
    sample = _sample_result(1000, epoch=50)
    recorder.commit_sample(
        repository, run_id=run_id, project_name=PROJECT, sample=sample,
        kind=recorder.KIND_LIVE,
        log_window=_window([
            {'first_block': 700, 'rebase_tx': '0xa'},
            {'first_block': 900, 'rebase_tx': '0xb'},
        ]),
    )

    rows = {
        row['first_block']: row['epoch_number']
        for row in repository.fetch_all(
            'SELECT first_block, epoch_number FROM epoch_boundary WHERE project = %s',
            (PROJECT,),
        )
    }
    assert rows == {700: None, 900: 50}, rows


def test_a_later_sample_can_fill_a_boundary_left_unnamed(repository):
    """The other half of leaving it NULL: the gap must be fillable, or the
    conservative choice above just loses the number permanently."""
    run_id = repository.start_run('test')
    recorder.commit_sample(
        repository, run_id=run_id, project_name=PROJECT,
        sample=_sample_result(1000, epoch=50), kind=recorder.KIND_LIVE,
        log_window=_window([
            {'first_block': 700, 'rebase_tx': '0xa'},
            {'first_block': 900, 'rebase_tx': '0xb'},
        ]),
    )
    # A backfill sample at 699 observes epoch 48, so the boundary at 700 opens 49.
    repository.upsert_epoch_boundary(PROJECT, 700, None, epoch_number=49)
    repository.commit()

    rows = {
        row['first_block']: row['epoch_number']
        for row in repository.fetch_all(
            'SELECT first_block, epoch_number FROM epoch_boundary WHERE project = %s',
            (PROJECT,),
        )
    }
    assert rows == {700: 49, 900: 50}
