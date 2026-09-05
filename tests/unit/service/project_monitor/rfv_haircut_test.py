"""The residual against `rfv()`'s 98% credit of the Morpho vault position.

Every figure asserted here comes from the operator store's own 133 epochs, via
`fixtures/haircut_epochs.json`, and is asserted BY VALUE to the cent. Asserting
that a deposit epoch's residual merely shrank would pass against a fix that
shrinks it for the wrong reason -- the ticket's own criterion, and the reason
the expected residual is written out as its two surviving terms rather than as
one number nobody can check.

What is deliberately NOT tested, because implementing it is the defect: the
residual is not compared against a prediction built from the same epoch's
realised component deltas. Interest and `delta polRfv` are unobservable as
flows, so predicting them zeroes the residual in every epoch forever and hides
the polRfv drain or vault re-mark the column exists to surface. The expected
values below come from the fixture's component deltas; the production residual
never reads them, which is why the comparison has something to say.
"""
from decimal import Decimal

import pytest

from src.job.project_monitor import report as report_job
from src.service.project_monitor import recorder
from src.service.project_monitor.config import NETNET
from src.service.project_monitor.report import (
    INTEREST_OUTSIDE_ENVELOPE,
    identity_breaks,
    load_epoch_rows,
    render_table,
)
from src.service.project_monitor.rfv_identity import (
    IDENTITY_BROKEN,
    IDENTITY_INCOMPLETE,
    IDENTITY_OK,
    MORPHO_CREDIT,
)

PROJECT = NETNET.name
CENT = Decimal('0.01')
# The band every quiet epoch in the store falls inside. Not a tolerance the code
# enforces -- an assertion about what the corrected number looks like when it is
# right, so a fix that lands the arithmetic in the wrong order of magnitude is
# caught even where the exact expected value is not asserted.
QUIET_BAND = (Decimal('7.2'), Decimal('185.2'))


class _StubRepository:
    """Stands in for the store in the entrypoint test: a context manager and
    nothing else, because the rows it would return are stubbed too."""

    def __init__(self, url):
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _readings(epoch_fixture, overrides=None):
    """The four Treasury readings, in the reading-row shape `commit_sample` takes.

    `overrides` replaces a reading's wei value, which is how the perturbation
    tests move the chain out from under a coefficient the code hardcodes.
    """
    overrides = overrides or {}
    return [
        {
            'name': name,
            'contract': 'treasury',
            'tier': 'core',
            'raw_hex': '0x1',
            'value_int': int(overrides.get(name, reading['value_int'])),
            'value_json': None,
            'decimals': reading['decimals'],
            'state': 'ok',
            'error_class': None,
        }
        for name, reading in sorted(epoch_fixture['readings'].items())
        if name not in overrides or overrides[name] is not None
    ]


def _seed(repository, fixture, group_index, overrides=None):
    """Commit one consecutive group of real epochs, with their flow windows."""
    run_id = repository.start_run('test')
    group = fixture['groups'][group_index]
    for epoch in group:
        epoch_fixture = fixture['epochs'][str(epoch)]
        sample = recorder.SampleResult(
            block=epoch_fixture['block'],
            block_timestamp=epoch_fixture['block_timestamp'],
            epoch_number=epoch,
            endpoint_kind='public',
            readings=_readings(epoch_fixture, (overrides or {}).get(epoch)),
        )
        recorder.commit_sample(
            repository, run_id=run_id, project_name=PROJECT, sample=sample,
            kind=recorder.KIND_BACKFILL,
        )
        # The fixture sums each window's flows per (direction, counterparty,
        # label) -- the exact grouping the report itself applies -- so the
        # transaction hash and log index carry no information here and are
        # synthesised to satisfy the table's uniqueness constraint.
        repository.insert_flows([
            {
                'project': PROJECT,
                'block': epoch_fixture['block'],
                'tx_hash': f'0x{epoch:04x}{index:04x}',
                'log_index': index,
                'direction': flow['direction'],
                'counterparty': flow['counterparty'],
                'amount': int(flow['amount']),
                'decimals': flow['decimals'],
                'label': flow['label'],
                'rule': 'labelled-counterparty',
            }
            for index, flow in enumerate(epoch_fixture['flows'])
        ])
    repository.commit()
    return {row['epoch']: row for row in load_epoch_rows(repository, NETNET)}


def test_a_deposit_epoch_s_residual_is_the_two_terms_the_haircut_leaves(
    repository, haircut_fixture
):
    """Epoch 133: a 965,383.40 USDG deposit, residual 137.29.

    The deposit costs `rfv()` 19,307.67 -- 2% of itself -- because it moves from
    a component credited at 100% to one credited at 98%. Nothing left the
    treasury, so the whole of that belongs in the model, and what survives in
    the residual is the two terms nothing predicts: 95.62 of vault interest at
    98% and 41.67 of polRfv drift.

    Before this fix the same epoch reported -19,170.38, and before internal
    flows were excluded from the net it reported 946,213 -- both of them a
    treasury-internal move rendered as the largest signal in the history.
    """
    rows = _seed(repository, haircut_fixture, 1)
    row = rows[133]

    assert row['vault_net_deposit'].quantize(CENT) == Decimal('965383.40')
    assert row['deposit_haircut'].quantize(CENT) == Decimal('19307.67')
    # The two surviving terms, named, then their sum. Naming them is what makes
    # a wrong-for-the-right-total fix visible.
    interest_term = MORPHO_CREDIT * row['vault_interest']['amount']
    assert interest_term.quantize(CENT) == Decimal('95.62')
    assert row['rfv_components']['pol_rfv_delta'].quantize(CENT) == Decimal('41.67')
    assert row['residual'].quantize(CENT) == Decimal('137.29')


def test_a_corrected_deposit_epoch_is_not_confined_to_the_quiet_band(
    repository, haircut_fixture
):
    """Epoch 120: residual 225.25, above the 7.2-185.2 quiet band, correctly.

    Its polRfv drift is the second largest in the history -- 169.33 of the
    225.25 -- which is a property of that epoch and not a fault in the fix. The
    epoch is here so that no later change can "improve" the deposit correction
    by squeezing every deposit epoch into the quiet band, which would mean
    modelling the polRfv term away.
    """
    rows = _seed(repository, haircut_fixture, 0)
    row = rows[120]

    assert row['residual'].quantize(CENT) == Decimal('225.25')
    assert row['residual'] > QUIET_BAND[1]
    interest_term = MORPHO_CREDIT * row['vault_interest']['amount']
    assert interest_term.quantize(CENT) == Decimal('55.92')
    assert row['rfv_components']['pol_rfv_delta'].quantize(CENT) == Decimal('169.33')


def test_a_quiet_epoch_s_residual_is_interest_plus_pol_drift_not_zero(
    repository, haircut_fixture
):
    """Epoch 132: no vault flow at all, residual 118.36 = 90.29 + 28.08.

    Asserted by value rather than as non-zero, because "non-zero" is exactly
    what the banned construction passes: predicting all three terms is zero only
    in integer arithmetic, and in floating point on ~5,000,000-token values it
    leaves a residual under a cent that a non-zero check waves through.

    The band assertion is the second half. A quiet epoch is not supposed to sit
    at zero -- interest arrives with no transfer to book it and polRfv drifts on
    its own -- so the number this column should show is a two- or three-figure
    one, and 69 quiet epochs put that between 7.2 and 185.2.
    """
    rows = _seed(repository, haircut_fixture, 1)
    row = rows[132]

    assert row['vault_net_deposit'] == 0, 'epoch 132 books no vault flow'
    assert row['deposit_haircut'] == 0, 'and so must attract no correction'
    interest_term = MORPHO_CREDIT * row['vault_interest']['amount']
    assert interest_term.quantize(CENT) == Decimal('90.29')
    assert row['rfv_components']['pol_rfv_delta'].quantize(CENT) == Decimal('28.08')
    assert row['residual'].quantize(CENT) == Decimal('118.36')
    assert QUIET_BAND[0] <= row['residual'] <= QUIET_BAND[1]


def test_the_identity_holds_on_every_real_epoch_and_says_so(
    repository, haircut_fixture
):
    """The control for the perturbation test below: unperturbed, nothing shouts.

    Without this, a check that reported `broken` unconditionally would pass the
    next test and take the alert with it.
    """
    rows = _seed(repository, haircut_fixture, 1)

    assert [row['rfv_identity']['state'] for row in rows.values()] == [IDENTITY_OK] * 3
    assert all(row['rfv_identity']['diff_wei'] == 0 for row in rows.values())
    assert identity_breaks(list(rows.values())) == []
    assert 'IDENTITY BROKEN' not in render_table(list(rows.values()))


def test_the_identity_check_fails_when_the_vault_credit_moves(
    repository, haircut_fixture
):
    """Re-rate the vault at 97% in one epoch's readings; the check must see it.

    This is the protocol change the coefficient is exposed to: 98% is observed
    over 133 epochs and never read from the contract, so a re-rating would
    otherwise leave the residual mis-modelling every deposit after it in
    silence. The perturbation keeps `rfv()` internally consistent at the NEW
    coefficient -- it is what the chain would return after a re-rate, not a
    corrupted reading -- so only a check that pins 98% specifically goes red.
    """
    fixture_133 = haircut_fixture['epochs']['133']['readings']
    morpho = int(fixture_133['Treasury.morphoAssets']['value_int'])
    re_rated = (
        int(fixture_133['Treasury.liquidUsdg']['value_int'])
        + morpho * 97 // 100
        + int(fixture_133['Treasury.polRfv']['value_int'])
    )
    rows = _seed(
        repository, haircut_fixture, 1, overrides={133: {'Treasury.rfv': re_rated}}
    )

    assert rows[133]['rfv_identity']['state'] == IDENTITY_BROKEN
    # One percent of the position, the size of the re-rating, so the diff is
    # reported rather than only the verdict.
    assert rows[133]['rfv_identity']['diff_wei'] == Decimal(-(morpho // 100))
    assert rows[132]['rfv_identity']['state'] == IDENTITY_OK, 'only the moved epoch'
    assert identity_breaks(list(rows.values())) == [133]
    assert 'IDENTITY BROKEN' in render_table(list(rows.values()))


def test_a_missing_component_is_not_reported_as_a_holding_identity(
    repository, haircut_fixture
):
    """A failed read must not resolve to `ok`.

    The cheap way to make the check never fail is to require all four values and
    treat their absence as agreement, which would make the warning disappear on
    exactly the sample where a read is also failing.
    """
    rows = _seed(
        repository, haircut_fixture, 1, overrides={133: {'Treasury.morphoAssets': None}}
    )

    assert rows[133]['rfv_identity']['state'] == IDENTITY_INCOMPLETE
    assert identity_breaks(list(rows.values())) == [], 'incomplete is not a break'


def test_vault_interest_outside_the_envelope_is_flagged_without_touching_the_identity(
    repository, haircut_fixture
):
    """Ten times the position's usual accrual, credited consistently.

    `morphoAssets` and `rfv()` both move, by the amount and by 98% of it, so the
    identity still holds exactly -- which is the point: a vault whose value is
    re-marked upward is not an arithmetic error, and only the interest model
    with its tolerance has anything to say about it. Epoch 133 runs at 34.5 ppm
    against a 10-95 ppm tolerance; this puts it near 3,700.
    """
    fixture_133 = haircut_fixture['epochs']['133']['readings']
    morpho = int(fixture_133['Treasury.morphoAssets']['value_int'])
    extra = 10 * 10 ** 21  # 10,000 tokens, against a ~97-token epoch accrual
    rows = _seed(
        repository, haircut_fixture, 1,
        overrides={
            133: {
                'Treasury.morphoAssets': morpho + extra,
                'Treasury.rfv': int(fixture_133['Treasury.rfv']['value_int'])
                + extra * 98 // 100,
            }
        },
    )

    assert rows[133]['rfv_identity']['state'] == IDENTITY_OK, 'the identity is intact'
    assert rows[133]['vault_interest']['state'] == INTEREST_OUTSIDE_ENVELOPE
    assert rows[132]['vault_interest']['state'] == 'ok', 'an ordinary epoch is not'
    assert 'outside the 10-95 ppm tolerance' in render_table(list(rows.values()))


def test_an_ordinary_epoch_s_interest_rate_is_reported_and_within_tolerance(
    repository, haircut_fixture
):
    """The control, by value: 34.5 ppm at epoch 133, 32.6 at 132.

    A rate the code computed but never rendered would leave the tolerance check
    watching a column nobody sees, so the rendered table is asserted too.
    """
    rows = _seed(repository, haircut_fixture, 1)

    assert rows[133]['vault_interest']['rate_ppm'].quantize(
        Decimal('0.1')
    ) == Decimal('34.5')
    assert rows[132]['vault_interest']['rate_ppm'].quantize(
        Decimal('0.1')
    ) == Decimal('32.6')
    table = render_table(list(rows.values()))
    assert 'vault ppm' in table
    assert '34.5' in table


@pytest.mark.parametrize(
    'perturbation, expected_state',
    (
        ({}, IDENTITY_OK),
        ({'Treasury.morphoAssets': 1}, IDENTITY_BROKEN),
    ),
)
def test_the_recorder_shouts_on_every_sample_not_only_the_epoch_s_last(
    repository, haircut_fixture, caplog, perturbation, expected_state
):
    """The report sees one closing sample per epoch; the recorder sees them all.

    A break on a mid-epoch sample that is later superseded would never reach the
    report, and it is the same warning about the same coefficient. Logged rather
    than raised, so the sample that evidences the change is still stored.
    """
    epoch_fixture = haircut_fixture['epochs']['133']
    overrides = {
        name: int(epoch_fixture['readings'][name]['value_int']) + delta
        for name, delta in perturbation.items()
    }
    run_id = repository.start_run('test')
    sample = recorder.SampleResult(
        block=epoch_fixture['block'], block_timestamp=epoch_fixture['block_timestamp'],
        epoch_number=133, endpoint_kind='public',
        readings=_readings(epoch_fixture, overrides),
    )
    with caplog.at_level('ERROR'):
        recorder.commit_sample(
            repository, run_id=run_id, project_name=PROJECT, sample=sample,
            kind=recorder.KIND_LIVE,
        )

    shouted = [r for r in caplog.records if 'rfv() identity broken' in r.getMessage()]
    assert bool(shouted) is (expected_state == IDENTITY_BROKEN)
    # The sample is stored either way: refusing to write it would delete the
    # evidence of the very change the log line is warning about.
    assert load_epoch_rows(repository, NETNET)[0]['epoch'] == 133


@pytest.mark.parametrize(
    'identity_state, expected_exit',
    ((IDENTITY_OK, 0), (IDENTITY_BROKEN, report_job.EXIT_IDENTITY_BROKEN)),
)
def test_the_report_job_exits_non_zero_when_the_identity_breaks(
    monkeypatch, capsys, identity_state, expected_exit
):
    """A scheduled run must not report a re-rated vault only in text.

    The rows are stubbed rather than seeded: what is under test is the
    entrypoint's exit code, and going through the store would make this a second
    test of the row builder that happens to end in an integer.
    """
    rows = [{'epoch': 133, 'present': True, 'rfv_identity': {'state': identity_state}}]
    monkeypatch.setattr(report_job, 'load_epoch_rows', lambda *_: rows)
    monkeypatch.setattr(report_job, 'render_table', lambda _: 'table')
    monkeypatch.setattr(
        report_job, 'ProjectMonitorRepository', _StubRepository
    )
    monkeypatch.setattr(
        report_job, 'get_project_monitor_database_url', lambda *_: 'stub'
    )

    assert report_job.main() == expected_exit
    # The table still prints in full on a break: which epochs carry it is what
    # says how far back the re-rating goes.
    assert 'table' in capsys.readouterr().out
