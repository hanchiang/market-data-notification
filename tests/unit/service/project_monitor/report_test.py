"""AC5: the per-epoch table, and `report --json` agreeing with it.

Rows are built from committed store rows only -- the report never touches the
chain, which is what makes it runnable on a `pg_dump` restored locally.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal

from src.service.project_monitor import recorder
from src.service.project_monitor.config import NETNET
from src.service.project_monitor.report import (
    COLUMNS,
    HEADER_NOTES,
    _cell,
    _lookup,
    load_epoch_rows,
    render_json,
    render_table,
)

PROJECT = NETNET.name


def _readings(rfv: int, backing: int, supply: int):
    return [
        {'name': 'Treasury.rfv', 'contract': 'treasury', 'tier': 'core',
         'raw_hex': '0x1', 'value_int': rfv, 'value_json': None, 'decimals': 18,
         'state': 'ok', 'error_class': None},
        {'name': 'Treasury.backingPerToken', 'contract': 'treasury', 'tier': 'core',
         'raw_hex': '0x1', 'value_int': backing, 'value_json': None, 'decimals': 18,
         'state': 'ok', 'error_class': None},
        {'name': 'NET.totalSupply', 'contract': 'NET', 'tier': 'core',
         'raw_hex': '0x1', 'value_int': supply, 'value_json': None, 'decimals': 9,
         'state': 'ok', 'error_class': None},
        {'name': 'pair.getReserves', 'contract': 'canonicalV2Pair', 'tier': 'core',
         'raw_hex': '0x1', 'value_int': None,
         'value_json': ['445771613725', '283218791841', '1788079528'],
         'decimals': None, 'state': 'ok', 'error_class': None},
    ]


def _commit_epoch(repository, run_id, *, block, epoch, rfv, backing, supply):
    sample = recorder.SampleResult(
        block=block, block_timestamp=1788000000 + block, epoch_number=epoch,
        endpoint_kind='public', readings=_readings(rfv, backing, supply),
    )
    return recorder.commit_sample(
        repository, run_id=run_id, project_name=PROJECT, sample=sample,
        kind=recorder.KIND_LIVE,
    )


WAD = 10 ** 18


def _seed_three_epochs(repository):
    run_id = repository.start_run('test')
    _commit_epoch(repository, run_id, block=100, epoch=10,
                  rfv=1000 * WAD, backing=80 * WAD, supply=12_500_000_000)
    _commit_epoch(repository, run_id, block=200, epoch=11,
                  rfv=1100 * WAD, backing=82 * WAD, supply=13_000_000_000)
    _commit_epoch(repository, run_id, block=300, epoch=12,
                  rfv=1210 * WAD, backing=84 * WAD, supply=13_500_000_000)
    repository.insert_mints([
        {'project': PROJECT, 'block': 150, 'tx_hash': '0xa', 'log_index': 1,
         'recipient': '0x1', 'amount': 250_000_000, 'decimals': 9, 'class': 'rebase'},
        {'project': PROJECT, 'block': 160, 'tx_hash': '0xb', 'log_index': 1,
         'recipient': '0x2', 'amount': 200_000_000, 'decimals': 9, 'class': 'bond'},
        {'project': PROJECT, 'block': 170, 'tx_hash': '0xc', 'log_index': 1,
         'recipient': '0x3', 'amount': 50_000_000, 'decimals': 9, 'class': 'issuance'},
    ])
    repository.insert_flows([
        {'project': PROJECT, 'block': 160, 'tx_hash': '0xb', 'log_index': 2,
         'direction': 'in', 'counterparty': '0x9', 'amount': 90_000_000,
         'decimals': 6, 'label': 'bond', 'rule': 'bond:BondCreated'},
        {'project': PROJECT, 'block': 165, 'tx_hash': '0xd', 'log_index': 1,
         'direction': 'out', 'counterparty': '0x8', 'amount': 10_000_000,
         'decimals': 6, 'label': 'unlabelled', 'rule': 'unlabelled'},
    ])
    repository.commit()
    return run_id


def test_three_epochs_produce_three_rows_in_order(repository):
    _seed_three_epochs(repository)
    rows = load_epoch_rows(repository, NETNET)
    assert [r['epoch'] for r in rows] == [10, 11, 12]
    assert all(r['present'] for r in rows)
    assert [r['block'] for r in rows] == [100, 200, 300]


def test_each_epoch_carries_the_chain_s_own_close_time_not_the_read_time(repository):
    """The timestamp is the block's, rendered ISO 8601 UTC.

    Asserted as exact strings against `block_timestamp = 1788000000 + block`,
    because the failure this guards is a plausible-looking wrong time: reading
    `read_at` instead would render a valid ISO string on every row, and for a
    backfilled sample it is the moment we swept, weeks after the epoch closed.
    Only comparing against the chain's value separates them, which is why the
    second assertion pins the two apart rather than checking the format.
    """
    _seed_three_epochs(repository)
    rows = load_epoch_rows(repository, NETNET)
    assert [r['block_time_utc'] for r in rows] == [
        '2026-08-29T10:41:40Z', '2026-08-29T10:43:20Z', '2026-08-29T10:45:00Z',
    ]
    # Same instant the row already carries, so the column can never drift from
    # `block_timestamp` while still looking like a time.
    assert all(
        r['block_time_utc']
        == datetime.fromtimestamp(r['block_timestamp'], tz=timezone.utc).strftime(
            '%Y-%m-%dT%H:%M:%SZ'
        )
        for r in rows
    )
    # The rendered table carries it too -- a row key nothing renders is not the
    # ask, and `COLUMNS` is a separate list that can be forgotten.
    table = render_table(rows)
    assert 'close time (UTC)' in table
    assert '2026-08-29T10:41:40Z' in table


def test_growth_and_dilution_are_measured_against_the_previous_close(repository):
    _seed_three_epochs(repository)
    rows = load_epoch_rows(repository, NETNET)
    second = rows[1]
    # rfv 1000 -> 1100 is +10%
    assert second['treasury_growth_pct'] == Decimal(10)
    # emission in (100, 200] is 0.25 + 0.20 + 0.05 = 0.5 NET against a previous
    # close of 12.5 NET
    assert second['dilution_pct'] == Decimal(4)
    assert second['emission_rebase'] == Decimal('0.25')
    assert second['emission_bond'] == Decimal('0.2')
    assert second['emission_issuance'] == Decimal('0.05')


def test_emission_is_reported_per_class_never_as_one_number(repository):
    """The 2026-08-28 "1.16%/day" figure was wrong because it compared bond and
    team mints against the rebase ceiling. Separate columns are the fix."""
    _seed_three_epochs(repository)
    row = load_epoch_rows(repository, NETNET)[1]
    for column in ('emission_rebase', 'emission_bond', 'emission_issuance'):
        assert column in row


def test_a_missing_epoch_is_a_row_of_dashes_not_an_interpolation(repository):
    """AC5: a missing epoch appears as a missing row, never interpolated.

    Drawing a line through a gap would invent a treasury figure nobody observed,
    and it would look exactly like one that was.
    """
    run_id = repository.start_run('test')
    _commit_epoch(repository, run_id, block=100, epoch=10,
                  rfv=1000 * WAD, backing=80 * WAD, supply=12_500_000_000)
    _commit_epoch(repository, run_id, block=300, epoch=12,
                  rfv=1210 * WAD, backing=84 * WAD, supply=13_500_000_000)
    repository.commit()

    rows = load_epoch_rows(repository, NETNET)
    assert [r['epoch'] for r in rows] == [10, 11, 12]
    gap = rows[1]
    assert gap['present'] is False
    assert 'rfv' not in gap  # no interpolated figure exists to be read
    table = render_table(rows)
    # Located by content, not by line offset: the epoch-11 row must be all
    # dashes across every column. An offset would silently start asserting on
    # some other line the moment the report grew a section.
    gap_line = next(
        line for line in table.splitlines() if line.split()[:1] == ['11']
    )
    assert set(gap_line.split()) == {'11', '-'}
    # And the flow detail names the gap rather than omitting it, so a reader
    # cannot mistake "no sample" for "a sample with no flows".
    assert 'epoch 11: no sample' in table


def test_the_closing_sample_is_the_last_one_observing_that_epoch(repository):
    run_id = repository.start_run('test')
    _commit_epoch(repository, run_id, block=100, epoch=10,
                  rfv=1000 * WAD, backing=80 * WAD, supply=12_500_000_000)
    _commit_epoch(repository, run_id, block=180, epoch=10,
                  rfv=1050 * WAD, backing=81 * WAD, supply=12_600_000_000)
    repository.commit()
    rows = load_epoch_rows(repository, NETNET)
    assert len(rows) == 1
    assert rows[0]['block'] == 180
    assert rows[0]['rfv'] == Decimal(1050)


def test_a_backfill_sample_counts_as_a_closing_sample(repository):
    """A late-filled row is still an observation of that epoch's state; the
    `kind` flag keeps it distinguishable without excluding it."""
    run_id = repository.start_run('test')
    sample = recorder.SampleResult(
        block=90, block_timestamp=1, epoch_number=9, endpoint_kind='alchemy',
        readings=_readings(900 * WAD, 79 * WAD, 12_000_000_000),
    )
    recorder.commit_sample(
        repository, run_id=run_id, project_name=PROJECT, sample=sample,
        kind=recorder.KIND_BACKFILL,
    )
    repository.commit()
    rows = load_epoch_rows(repository, NETNET)
    assert rows[0]['epoch'] == 9
    assert rows[0]['kind'] == 'backfill'


def test_the_residual_is_the_rfv_move_not_explained_by_visible_flows(repository):
    _seed_three_epochs(repository)
    row = load_epoch_rows(repository, NETNET)[1]
    # rfv moved +100; visible net USDG flow was +90 - 10 = +80.
    assert row['residual'] == Decimal(20)


def test_the_header_carries_the_notes_that_stop_a_misreading(repository):
    _seed_three_epochs(repository)
    table = render_table(load_epoch_rows(repository, NETNET))
    assert 'fees unattributed' in table
    assert 'Sleeve total is NOT part of rfv()' in table
    assert 'do not sum to rfv()' in table
    assert len(HEADER_NOTES) == 5


def test_json_and_table_agree_column_for_column(repository):
    """The ticket's own acceptance criterion. The JSON is what the later local
    dashboard reads, so a divergence here would give the operator two numbers
    for the same quantity."""
    _seed_three_epochs(repository)
    rows = load_epoch_rows(repository, NETNET)
    parsed = json.loads(render_json(rows))
    assert len(parsed) == len(rows)
    for row, record in zip(rows, parsed):
        assert record['epoch'] == row['epoch']
        for key, _ in COLUMNS:
            if key == 'epoch':
                continue
            # `_lookup`, not `row.get`: a dotted column key (`buyback.capacity`)
            # reaches into a nested dict, and `row.get` would return None for
            # every one of them -- making the comparison below pass vacuously
            # for exactly the columns most recently added.
            value = _lookup(row, key)
            rendered = _lookup(record, key)
            if value is None:
                assert rendered is None, key
            elif isinstance(value, Decimal):
                assert rendered == str(value), key
            else:
                assert rendered == value, key


def test_json_distinguishes_a_missing_epoch_from_a_failed_reading(repository):
    """Both print a dash in the table; a consumer must be able to tell them
    apart, which is why the reading state travels with the row."""
    run_id = repository.start_run('test')
    _commit_epoch(repository, run_id, block=100, epoch=10,
                  rfv=1000 * WAD, backing=80 * WAD, supply=12_500_000_000)
    failed = _readings(1100 * WAD, 82 * WAD, 13_000_000_000)
    failed.append({
        'name': 'Mark.NVDA.latestRoundData', 'contract': 'NVDA_feed',
        'tier': 'peripheral', 'raw_hex': None, 'value_int': None,
        'value_json': None, 'decimals': None, 'state': 'failed',
        'error_class': 'EvmRpcError',
    })
    sample = recorder.SampleResult(
        block=300, block_timestamp=2, epoch_number=12, endpoint_kind='public',
        readings=failed,
    )
    recorder.commit_sample(
        repository, run_id=run_id, project_name=PROJECT, sample=sample,
        kind=recorder.KIND_LIVE,
    )
    repository.commit()

    parsed = json.loads(render_json(load_epoch_rows(repository, NETNET)))
    missing = next(r for r in parsed if r['epoch'] == 11)
    present = next(r for r in parsed if r['epoch'] == 12)
    assert missing['present'] is False
    assert present['present'] is True
    assert present['reading_states']['Mark.NVDA.latestRoundData'] == 'failed'


def test_an_empty_store_reports_nothing_rather_than_raising(repository):
    assert load_epoch_rows(repository, NETNET) == []
    assert 'epoch' in render_table([])


# AC5's enumerated figures, transcribed from the requirement. The point of the
# list is that it is written from the CRITERION, not from `COLUMNS` -- a test
# that iterates the implementation's own column tuple can only ever confirm the
# table renders what it renders, which is how eight AC5 figures went missing
# from the operator's surface while a green agreement test watched.
AC5_TABLE_FIGURES = (
    'close block',
    'backing/token',
    'd backing %',
    'rfv()',
    'growth %',
    'rebase',
    'bond',
    'issuance',
    'other',
    'dilution %',
    'residual',
    'premium x',
    'LP supply',
    'LP treasury %',
    'bb capacity',
    'bb filled',
    'bb repurchased',
    'bb burned',
    'lb supplied',
    'lb borrowed',
    'lb util %',
    'lb borrow APR %',
    'Sleeve $ (memo)',
)


def test_the_table_carries_every_ac5_figure(repository):
    """AC5 enumerates what each epoch row shows. Every fixed-arity figure is a
    column; the two variable-arity ones are the detail block below the table."""
    _seed_three_epochs(repository)
    table = render_table(load_epoch_rows(repository, NETNET))
    header = table.splitlines()[len(HEADER_NOTES) + 2]
    missing = [figure for figure in AC5_TABLE_FIGURES if figure not in header]
    assert not missing, f'AC5 figures absent from the table header: {missing}'
    # Inflows and outflows cannot be columns -- a bucket appears the first time
    # a counterparty transacts -- so AC5 is met for them by the detail block.
    assert 'flows by epoch' in table
    assert 'AC5: inflows by bucket, outflows by recipient' in table


def test_the_ac5_figure_check_can_fail(repository):
    """The guard above must be able to go red, or it is decoration."""
    _seed_three_epochs(repository)
    table = render_table(load_epoch_rows(repository, NETNET))
    header = table.splitlines()[len(HEADER_NOTES) + 2]
    assert 'a column AC5 never asked for' not in header


def test_an_unlabelled_outflow_keeps_its_recipient_address(repository):
    """R6 rule 4 and AC5's "outflows by recipient". Two unknown recipients must
    stay two lines: merging them under one `unlabelled` bucket makes an
    unrecognised drain read as a single ordinary counterparty."""
    run_id = repository.start_run('test')
    _commit_epoch(repository, run_id, block=100, epoch=10,
                  rfv=1000 * WAD, backing=80 * WAD, supply=12_500_000_000)
    _commit_epoch(repository, run_id, block=200, epoch=11,
                  rfv=1100 * WAD, backing=82 * WAD, supply=12_600_000_000)
    first = '0x' + 'a' * 40
    second = '0x' + 'b' * 40
    repository.insert_flows([
        {
            'project': PROJECT, 'block': 150, 'tx_hash': '0xf1', 'log_index': i,
            'direction': 'out', 'counterparty': address, 'amount': 5_000_000,
            'decimals': 6, 'label': 'unlabelled', 'rule': 'unlabelled',
        }
        for i, address in enumerate((first, second))
    ])
    repository.commit()

    row = load_epoch_rows(repository, NETNET)[1]
    assert set(row['outflows']) == {f'unlabelled:{first}', f'unlabelled:{second}'}
    table = render_table(load_epoch_rows(repository, NETNET))
    assert first in table and second in table


def test_a_not_deployed_contract_renders_differently_from_a_failed_read():
    """Design, Report: "A `not_deployed` reading renders as 'n/a (not deployed)'".

    Three different blanks that must not print the same character: a contract
    that does not exist yet at this block, a figure with no on-chain source at
    all, and a read that was issued and failed. The state consulted is the
    column's OWN -- an earlier version scanned every reading in the sample, so
    one not-deployed contract anywhere stamped the label on unrelated blanks.
    """
    row = {
        'present': True,
        'epoch': 5,
        'buyback': {
            'capacity': None,
            'capacity_state': 'not_deployed',
            'filled': None,
            'filled_state': 'no_onchain_source',
        },
        'loopback': {'state': 'failed'},
    }
    assert _cell(row, 'buyback.capacity') == 'n/a (not deployed)'
    assert _cell(row, 'buyback.filled') == 'n/a (no source)'
    assert _cell(row, 'loopback.utilization_pct') == 'failed'
    # A column with no state mapping still falls back to a plain dash.
    assert _cell(row, 'residual') == '-'


def test_the_not_deployed_label_does_not_leak_across_columns():
    """The counterpart: a not-deployed buyback must not label the Loopback."""
    row = {
        'present': True,
        'epoch': 5,
        'buyback': {'capacity': None, 'capacity_state': 'not_deployed'},
        'loopback': {'state': 'ok', 'utilization_pct': None},
    }
    assert _cell(row, 'buyback.capacity') == 'n/a (not deployed)'
    assert _cell(row, 'loopback.utilization_pct') == '-'


# --------------------------------- AC5's VALUE path for the P1-2 figures
#
# Round-1 test review, finding 1: every test above seeds only the four readings
# in `_readings()`, so `Morpho.market`, `IRM.borrowRateView`,
# `inverseBond.capacityRemaining` and all twelve Sleeve/Mark readings are
# ABSENT from every row those tests build. The JSON/table agreement test then
# compares `None == None` for exactly the eight figures P1-2 was about, and
# `test_the_table_carries_every_ac5_figure` checks static header text only.
# Demonstrated: swapping `supplied`/`borrowed` in `_loopback` AND pointing
# `buyback.capacity` at a nonexistent reading name left the suite green.
#
# So P1-2 was fixed at the header level and never pinned at the value level --
# the same defect one layer down. The tests below seed the REAL readings from
# the committed fixture and assert the derived figures, with every expected
# value stated as a literal computed from the struct's documented field order
# rather than by re-running the production formula.

SLEEVE_SYMBOLS = ('NVDA', 'SPCX', 'AAPL', 'MSFT', 'GOOGL', 'COIN')

# The newest Chainlink `updatedAt` across the six marks in the fixture. A
# sample timestamp at or just after it is "fresh"; +25 h is stale.
FIXTURE_NEWEST_MARK_AT = 1787956586


def _fixture_readings(expected_fixture, names=None):
    """Readings straight from the committed fixture, in the recorder's own
    stored shape, so the report is driven by values a real chain read produced
    rather than by numbers invented to satisfy the assertions."""
    rows = []
    for reading in expected_fixture['readings']:
        if names is not None and reading['name'] not in names:
            continue
        rows.append({
            'name': reading['name'],
            'contract': reading['contract'],
            'tier': reading['tier'],
            'raw_hex': reading['raw_hex'],
            'value_int': reading['value_int'],
            'value_json': reading['value_json'],
            'decimals': reading['decimals'],
            'state': reading['state'],
            'error_class': reading['error_class'],
        })
    return rows


def _commit_fixture_epoch(repository, run_id, *, block, epoch, block_timestamp,
                          expected_fixture):
    sample = recorder.SampleResult(
        block=block, block_timestamp=block_timestamp, epoch_number=epoch,
        endpoint_kind='public', readings=_fixture_readings(expected_fixture),
    )
    return recorder.commit_sample(
        repository, run_id=run_id, project_name=PROJECT, sample=sample,
        kind=recorder.KIND_LIVE,
    )


def test_the_loopback_figures_carry_the_markets_own_field_order(
    repository, expected_fixture
):
    """Morpho Blue's `Market` is
    (totalSupplyAssets, totalSupplyShares, totalBorrowAssets, totalBorrowShares,
    lastUpdate, fee) -- supplied at index 0 and borrowed at index 2, NOT
    adjacent. Swapping them inverts utilization, which is one of G5's six
    pre-registered thresholds and the figure that says whether a supplier can
    still exit. The expected numbers below are read off the fixture's own
    `Morpho.market` tuple by that documented order, not by re-running
    `_loopback`."""
    run_id = repository.start_run('test')
    _commit_fixture_epoch(
        repository, run_id, block=49_877_926, epoch=132,
        block_timestamp=FIXTURE_NEWEST_MARK_AT + 60,
        expected_fixture=expected_fixture,
    )
    repository.commit()

    row = load_epoch_rows(repository, NETNET)[0]
    loopback = row['loopback']

    assert loopback['state'] == 'ok'
    # 51,225,024,314 / 1e6 and 51,211,175,064 / 1e6 -- indices 0 and 2.
    assert loopback['total_supplied'] == Decimal('51225.024314')
    assert loopback['total_borrowed'] == Decimal('51211.175064')
    # 99.97%: the market was effectively fully drawn when captured, which is
    # the state the requirement calls out (a supplier cannot exit).
    assert loopback['utilization_pct'] > Decimal('99.9')
    assert loopback['utilization_pct'] < Decimal('100')
    # borrowRateView is a per-second wad rate; x seconds-per-year x 100.
    assert loopback['borrow_apr_pct'].quantize(Decimal('0.01')) == Decimal('25.93')
    # Supply APR is derived, not read: borrow x utilization x (1 - fee), and
    # fee is 0 here, so it sits just under the borrow rate.
    assert loopback['supply_apr_pct'] < loopback['borrow_apr_pct']
    assert loopback['supply_apr_pct'] > loopback['borrow_apr_pct'] * Decimal('0.99')


def test_the_buyback_capacity_reaches_the_row_and_the_rendered_table(
    repository, expected_fixture
):
    """`inverseBond.capacityRemaining` is one of AC5's four buyback figures and
    feeds a G5 threshold. A reading name that no longer resolves would leave the
    cell blank, which reads exactly like a contract that is not deployed."""
    run_id = repository.start_run('test')
    _commit_fixture_epoch(
        repository, run_id, block=49_877_926, epoch=132,
        block_timestamp=FIXTURE_NEWEST_MARK_AT + 60,
        expected_fixture=expected_fixture,
    )
    repository.commit()

    rows = load_epoch_rows(repository, NETNET)
    buyback = rows[0]['buyback']
    assert buyback['capacity_state'] == 'ok'
    assert buyback['capacity'] == Decimal('21551.46043603')
    # And it survives rendering rather than only existing on the row dict.
    assert '21,551.4604' in render_table(rows)
    # `filled` stays unavailable by design -- no on-chain source exists, and
    # inventing one is worse than the visible gap.
    assert buyback['filled'] is None
    assert buyback['filled_state'] == 'no_onchain_source'
    assert 'n/a (no source)' in render_table(rows)


def test_the_sleeve_total_marks_every_leg_at_its_own_feed(
    repository, expected_fixture
):
    """The Sleeve is six balances at six independent Chainlink marks, each with
    its own feed decimals. The expected total is stated as a literal, computed
    once from the fixture's balances and answers -- deliberately NOT by looping
    over the same symbols the way `_sleeve` does, because a test that rebuilds
    the value with the production algorithm passes whenever both are wrong the
    same way (which is exactly how the char-array bug survived AC3)."""
    run_id = repository.start_run('test')
    _commit_fixture_epoch(
        repository, run_id, block=49_877_926, epoch=132,
        block_timestamp=FIXTURE_NEWEST_MARK_AT + 60,
        expected_fixture=expected_fixture,
    )
    repository.commit()

    row = load_epoch_rows(repository, NETNET)[0]

    assert row['sleeve_state'] == 'ok', 'every leg present; no silent omission'
    assert row['sleeve_total_usd'].quantize(Decimal('0.01')) == Decimal('2392608.40')
    # A fresh sample must not be flagged stale, or the flag says nothing.
    assert row['sleeve_mark_stale'] is False
    # That the Sleeve is not inside rfv is pinned against the fixture's own
    # rfv components in `fixture_test.py`; not restated here.


def test_a_sample_long_after_its_marks_flags_the_sleeve_as_stale(
    repository, expected_fixture
):
    """The counterpart: a dead feed must not value the Sleeve forever at
    whatever the last price happened to be. Same readings, a sample 25 h later."""
    run_id = repository.start_run('test')
    _commit_fixture_epoch(
        repository, run_id, block=49_877_926, epoch=132,
        block_timestamp=FIXTURE_NEWEST_MARK_AT + 25 * 3600,
        expected_fixture=expected_fixture,
    )
    repository.commit()

    row = load_epoch_rows(repository, NETNET)[0]
    assert row['sleeve_mark_stale'] is True
    # The total is still computed -- stale is a warning on a figure, not a
    # reason to blank it.
    assert row['sleeve_total_usd'] > 0


def test_the_json_agreement_is_not_vacuous_for_the_p1_2_figures(
    repository, expected_fixture
):
    """`test_json_and_table_agree_column_for_column` above compares every
    column, but with only four readings seeded it compares `None == None` for
    the eight figures P1-2 was about. Driven from the fixture, those columns
    carry real values -- so the agreement check has something to disagree
    about."""
    run_id = repository.start_run('test')
    _commit_fixture_epoch(
        repository, run_id, block=49_877_926, epoch=132,
        block_timestamp=FIXTURE_NEWEST_MARK_AT + 60,
        expected_fixture=expected_fixture,
    )
    repository.commit()

    rows = load_epoch_rows(repository, NETNET)
    parsed = json.loads(render_json(rows))

    # `buyback.repurchased` and `buyback.burned` are deliberately NOT in this
    # list. They are summed from `event` rows, and `_sum_event_field` returns
    # Decimal(0) on an empty table -- so an `is not None` assertion here would
    # pass off that default and claim a coverage this test does not have
    # (round-2 test review, finding 7). They are pinned instead by
    # `test_a_recorded_buyback_reaches_the_repurchased_and_burned_columns`
    # below, which inserts a real event row.
    p1_2_columns = (
        'buyback.capacity',
        'loopback.total_supplied', 'loopback.total_borrowed',
        'loopback.utilization_pct', 'loopback.borrow_apr_pct',
        'sleeve_total_usd',
    )
    for key in p1_2_columns:
        value = _lookup(rows[0], key)
        assert value is not None, f'{key} is still None -- the check is vacuous'
        assert _lookup(parsed[0], key) == str(value), key


# The one InverseBonded execution in chain history, decoded in
# `fixture_test.py` against its raw log. Restated here as the amount a report
# row must show when such an event falls inside the epoch.
BUYBACK_NET_BURNED_RAW = 1_368_953_250  # 9 dp
BUYBACK_NET_BURNED = Decimal('1.36895325')


def test_a_recorded_buyback_reaches_the_repurchased_and_burned_columns(
    repository, expected_fixture
):
    """AC5's `repurchased` and `burned`, which the 2026-08-30 amendment pinned
    to `InverseBonded.netBurned` -- the dapp sets both to the same sum, so a
    buyback burns exactly what it repurchases.

    Round-2 test review, finding 7: nothing inserted an `InverseBonded` row
    anywhere, and `_sum_event_field` returns Decimal(0) for an empty table, so
    mistyping the event name in `report.py` left both columns rendering 0.00
    forever -- which reads exactly like an epoch with no buybacks. The zero
    default is why this needs a non-zero event rather than an `is not None`
    check.
    """
    run_id = repository.start_run('test')
    _commit_fixture_epoch(
        repository, run_id, block=100, epoch=10,
        block_timestamp=FIXTURE_NEWEST_MARK_AT + 60,
        expected_fixture=expected_fixture,
    )
    _commit_fixture_epoch(
        repository, run_id, block=200, epoch=11,
        block_timestamp=FIXTURE_NEWEST_MARK_AT + 120,
        expected_fixture=expected_fixture,
    )
    repository.insert_events([{
        'project': PROJECT, 'block': 150, 'tx_hash': '0xbb', 'log_index': 4,
        'contract': 'inverseBond', 'name': 'InverseBonded',
        'fields_json': {
            'seller': '0x' + '8' * 40,
            'netBurned': str(BUYBACK_NET_BURNED_RAW),
            'usdgPaidWad': '7654221293517432240',
        },
    }])
    repository.commit()

    rows = load_epoch_rows(repository, NETNET)
    buyback = rows[1]['buyback']

    assert buyback['repurchased'] == BUYBACK_NET_BURNED
    assert buyback['burned'] == BUYBACK_NET_BURNED
    # Both figures come from the same field by design; asserting they agree is
    # the property the amendment states, not a redundant restatement.
    assert buyback['repurchased'] == buyback['burned']

    # The epoch BEFORE the buyback must stay at zero -- the block-window filter
    # is what keeps a buyback from being counted in every later epoch too.
    assert rows[0]['buyback']['repurchased'] == Decimal(0)

    # And it survives rendering.
    assert '1.3690' in render_table(rows)
