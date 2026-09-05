"""The dashboard slice's acceptance criteria, AC-D1 to AC-D13.

Route tests go through a **shimmed** `TestClient`. Starlette 0.22's client
presents `client.host == 'testclient'` and its constructor takes no `client=`
argument, so a bare `TestClient(app)` would be refused 403 by the loopback
middleware on every request. The shim below sets the ASGI scope's client to a
loopback address before delegating; the middleware stays in place and is
exercised by its own tests, one of which shims a public address instead.
Nothing test-only is added to the app itself.

Everything here runs against `project_monitor_test`, never the operator store:
the `repository` fixture truncates it before each test.
"""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from starlette.testclient import TestClient

from src.job.project_monitor import dashboard as dashboard_job
from src.job.project_monitor.dashboard import build_app
from src.router.project_monitor import dashboard
from src.service.project_monitor import recorder
from src.service.project_monitor.config import NETNET
from src.service.project_monitor.report import (
    EVENT_NAMES_READ,
    HEADER_NOTES,
    STALE_AFTER_HOURS,
    chart_series,
    freshness,
    load_epoch_rows,
    render_json,
)
from src.service.project_monitor.repository import ProjectMonitorRepository

PROJECT = NETNET.name
WAD = 10 ** 18
MORPHO_VAULT = '0xBeEff033F34C046626B8D0A041844C5d1A5409dd'
PAGE = Path(dashboard.STATIC_DIR) / 'index.html'
FIXTURE_DIR = Path(__file__).parent / 'fixtures'


# -- harness ---------------------------------------------------------------


def _loopback_client(app, host='127.0.0.1', host_header=b'127.0.0.1:8765'):
    """A `TestClient` whose requests arrive from `host` carrying `host_header`.

    The shim is the smallest thing that satisfies the middleware without
    weakening it: the app, its middleware and every route are the real ones.
    Both halves have to be set -- `TestClient` presents `client.host ==
    'testclient'` and sends `Host: testserver`, and the middleware refuses each
    of those on its own.
    """
    async def shim(scope, receive, send):
        if scope['type'] == 'http':
            headers = [(k, v) for k, v in scope['headers'] if k != b'host']
            headers.append((b'host', host_header))
            scope = dict(scope, client=(host, 50000), headers=headers)
        await app(scope, receive, send)

    return TestClient(shim)


@pytest.fixture
def client(monkeypatch, database_url):
    """The real app, pointed at the `_test` database the fixtures seeded."""
    monkeypatch.setattr(
        dashboard, 'get_project_monitor_database_url', lambda runtime_mode: database_url
    )
    return _loopback_client(build_app())


def _readings(rfv, backing, supply, *, morpho=0, liquid=None, pol=0, reserves=True,
              break_identity=False):
    liquid_value = rfv - morpho * 98 // 100 - pol if liquid is None else liquid
    if break_identity:
        liquid_value += 10 ** 15
    rows = [
        {'name': 'Treasury.rfv', 'contract': 'treasury', 'tier': 'core',
         'raw_hex': '0x1', 'value_int': rfv, 'value_json': None, 'decimals': 18,
         'state': 'ok', 'error_class': None},
        {'name': 'Treasury.backingPerToken', 'contract': 'treasury', 'tier': 'core',
         'raw_hex': '0x1', 'value_int': backing, 'value_json': None, 'decimals': 18,
         'state': 'ok', 'error_class': None},
        {'name': 'Treasury.liquidUsdg', 'contract': 'treasury', 'tier': 'core',
         'raw_hex': '0x1', 'value_int': liquid_value, 'value_json': None, 'decimals': 18,
         'state': 'ok', 'error_class': None},
        {'name': 'Treasury.morphoAssets', 'contract': 'treasury', 'tier': 'core',
         'raw_hex': '0x1', 'value_int': morpho, 'value_json': None, 'decimals': 18,
         'state': 'ok', 'error_class': None},
        {'name': 'Treasury.polRfv', 'contract': 'treasury', 'tier': 'core',
         'raw_hex': '0x1', 'value_int': pol, 'value_json': None, 'decimals': 18,
         'state': 'ok', 'error_class': None},
        {'name': 'NET.totalSupply', 'contract': 'NET', 'tier': 'core',
         'raw_hex': '0x1', 'value_int': supply, 'value_json': None, 'decimals': 9,
         'state': 'ok', 'error_class': None},
    ]
    if reserves:
        rows += [
            {'name': 'pair.getReserves', 'contract': 'canonicalV2Pair', 'tier': 'core',
             'raw_hex': '0x1', 'value_int': None,
             'value_json': ['445771613725', '283218791841', '1788079528'],
             'decimals': None, 'state': 'ok', 'error_class': None},
            {'name': 'pair.token0', 'contract': 'canonicalV2Pair', 'tier': 'core',
             'raw_hex': '0x1', 'value_int': None,
             'value_json': NETNET.address('USDG'), 'decimals': None,
             'state': 'ok', 'error_class': None},
            {'name': 'USDG.decimals', 'contract': 'USDG', 'tier': 'core',
             'raw_hex': '0x6', 'value_int': 6, 'value_json': None, 'decimals': None,
             'state': 'ok', 'error_class': None},
            {'name': 'NET.decimals', 'contract': 'NET', 'tier': 'core',
             'raw_hex': '0x9', 'value_int': 9, 'value_json': None, 'decimals': None,
             'state': 'ok', 'error_class': None},
        ]
    return rows


def _commit(repository, run_id, *, block, epoch, timestamp=None, **reading_kwargs):
    sample = recorder.SampleResult(
        block=block,
        block_timestamp=timestamp if timestamp is not None else 1788000000 + block,
        epoch_number=epoch,
        endpoint_kind='public',
        readings=_readings(**reading_kwargs),
    )
    return recorder.commit_sample(
        repository, run_id=run_id, project_name=PROJECT, sample=sample,
        kind=recorder.KIND_LIVE,
    )


def _seed_three_epochs(repository, **overrides):
    run_id = repository.start_run('test')
    _commit(repository, run_id, block=100, epoch=10,
            rfv=1000 * WAD, backing=80 * WAD, supply=12_500_000_000)
    _commit(repository, run_id, block=200, epoch=11,
            rfv=1100 * WAD, backing=82 * WAD, supply=13_000_000_000)
    _commit(repository, run_id, block=300, epoch=12,
            rfv=1210 * WAD, backing=84 * WAD, supply=13_500_000_000, **overrides)
    repository.insert_mints([
        {'project': PROJECT, 'block': 150, 'tx_hash': '0xa', 'log_index': 1,
         'recipient': '0x1', 'amount': 250_000_000, 'decimals': 9, 'class': 'rebase'},
        {'project': PROJECT, 'block': 160, 'tx_hash': '0xb', 'log_index': 1,
         'recipient': '0x2', 'amount': 200_000_000, 'decimals': 9, 'class': 'bond'},
    ])
    repository.insert_flows([
        {'project': PROJECT, 'block': 160, 'tx_hash': '0xb', 'log_index': 2,
         'direction': 'in', 'counterparty': '0x9', 'amount': 90_000_000,
         'decimals': 6, 'label': 'bond', 'rule': 'bond:BondCreated'},
    ])
    repository.commit()
    return run_id


def seed_golden(repository):
    """The fixture the pre-DR14 byte-identity baseline was captured from.

    Deliberately broad, because the baseline is only as strong as the row
    surface it covers: a gap row, a haircut epoch with mints and every inflow
    bucket shape, an epoch with no pair readings (null price), and an epoch
    whose rfv() identity is broken. Public rather than `_`-prefixed because
    `scripts/capture_dashboard_row_baseline.py` imports it -- the baseline and
    the test have to seed the same store or the comparison means nothing.

    Every value here is fixed, and `_commit` derives `block_timestamp` from the
    block, so re-running it produces the same rows. Changing this function
    invalidates the baseline; the test will say so by failing.
    """
    run_id = repository.start_run('test')
    _commit(repository, run_id, block=100, epoch=10,
            rfv=1000 * WAD, backing=80 * WAD, supply=12_500_000_000)
    # epoch 11 has no sample: the gap row.
    _commit(repository, run_id, block=300, epoch=12, rfv=1210 * WAD,
            backing=84 * WAD, supply=13_500_000_000, morpho=100 * WAD, pol=50 * WAD)
    _commit(repository, run_id, block=400, epoch=13, rfv=1300 * WAD,
            backing=86 * WAD, supply=14_000_000_000, morpho=102 * WAD, pol=50 * WAD,
            reserves=False)
    _commit(repository, run_id, block=500, epoch=14, rfv=1400 * WAD,
            backing=88 * WAD, supply=14_500_000_000, morpho=104 * WAD, pol=50 * WAD,
            break_identity=True)
    repository.insert_mints([
        {'project': PROJECT, 'block': 250, 'tx_hash': '0xa', 'log_index': 1,
         'recipient': '0x1', 'amount': 250_000_000, 'decimals': 9, 'class': 'rebase'},
        {'project': PROJECT, 'block': 260, 'tx_hash': '0xb', 'log_index': 1,
         'recipient': '0x2', 'amount': 200_000_000, 'decimals': 9, 'class': 'bond'},
    ])
    repository.insert_flows([
        {'project': PROJECT, 'block': 250, 'tx_hash': '0xb', 'log_index': 2,
         'direction': 'in', 'counterparty': '0x9', 'amount': 90_000_000,
         'decimals': 6, 'label': 'bond', 'rule': 'bond:BondCreated'},
        {'project': PROJECT, 'block': 251, 'tx_hash': '0xu1', 'log_index': 1,
         'direction': 'in', 'counterparty': '0xaaa', 'amount': 1_000_000,
         'decimals': 6, 'label': 'unlabelled', 'rule': 'unlabelled'},
        {'project': PROJECT, 'block': 252, 'tx_hash': '0xu2', 'log_index': 1,
         'direction': 'in', 'counterparty': '0xbbb', 'amount': 2_000_000,
         'decimals': 6, 'label': 'unlabelled', 'rule': 'unlabelled'},
        {'project': PROJECT, 'block': 253, 'tx_hash': '0xv1', 'log_index': 1,
         'direction': 'in', 'counterparty': MORPHO_VAULT, 'amount': 5_000_000,
         'decimals': 6, 'label': 'unlabelled', 'rule': 'unlabelled'},
        {'project': PROJECT, 'block': 350, 'tx_hash': '0xo1', 'log_index': 1,
         'direction': 'out', 'counterparty': '0xddd', 'amount': 3_000_000,
         'decimals': 6, 'label': 'unlabelled', 'rule': 'unlabelled'},
    ])
    repository.commit()
    return run_id


def _body(client, **params):
    response = client.get('/project-monitor/netnet/report', params=params)
    assert response.status_code == 200, response.text
    return response.json()


# -- AC-D1: one derivation -------------------------------------------------


def test_the_route_serves_exactly_the_rows_the_cli_prints(repository, client):
    """AC-D1. Compared as parsed JSON, key for key: the point is that both come
    from `_jsonable`, so a Decimal is the same STRING on both surfaces. Letting
    FastAPI encode the payload would make it a float here and a string there."""
    _seed_three_epochs(repository)
    cli_rows = json.loads(render_json(load_epoch_rows(repository, NETNET)))
    assert _body(client)['rows'] == cli_rows


def test_a_decimal_is_served_as_a_string_not_a_float(repository, client):
    _seed_three_epochs(repository)
    assert isinstance(_body(client)['rows'][1]['backing_per_token'], str)


# -- DR14: the slice is additive to `report --json` -------------------------


DR14_KEYS = frozenset({'growth_minus_dilution_pct', 'sign_agreement', 'inflows_chart'})


def _baseline():
    """The rows `report --json` produced for `seed_golden` BEFORE this slice.

    Captured from commit `1cf2f66` by
    `scripts/capture_dashboard_row_baseline.py`, which the fixture names in its
    own `pre_slice_rev` key.
    """
    baseline = json.loads((FIXTURE_DIR / 'dashboard_pre_dr14_rows.json').read_text())
    assert baseline['seed'] == 'dashboard_route_test.seed_golden'
    return baseline['rows']


def test_no_existing_row_key_changed_name_type_or_value(repository):
    """DR14's promise, as a comparison rather than an assurance.

    The slice rewrote `load_epoch_rows` into a five-query prefetch. That is the
    change most able to move a figure silently -- a window boundary off by one
    block regroups a flow into the wrong epoch and every downstream percentage
    with it. Comparing today's rows against the pre-slice output on the same
    seed is what makes the refactor's row-equality claim checkable in CI; the
    implement stage could only check it by hand against the operator store,
    which no test can reach.
    """
    seed_golden(repository)
    rows = json.loads(render_json(load_epoch_rows(repository, NETNET)))
    stripped = [{k: v for k, v in row.items() if k not in DR14_KEYS} for row in rows]
    assert stripped == _baseline()


def test_the_slice_added_exactly_the_three_dr14_keys(repository):
    """The other half: additive means additive. A fourth new key would pass the
    comparison above (which strips only these three) and fail here."""
    seed_golden(repository)
    rows = json.loads(render_json(load_epoch_rows(repository, NETNET)))
    for row, before in zip(rows, _baseline()):
        added = set(row) - set(before)
        assert added == (DR14_KEYS if row['present'] else set()), row['epoch']
        assert not set(before) - set(row), row['epoch']


# -- AC-D2: notes and alerts travel ----------------------------------------


def test_every_header_note_and_the_identity_alert_reach_the_body(repository, client):
    """AC-D2. The identity break is seeded in wei, on the same four readings the
    check reads, so the alert is produced by the report's own function rather
    than asserted into existence."""
    _seed_three_epochs(repository, break_identity=True)
    body = _body(client)
    assert body['notes'] == list(HEADER_NOTES)
    assert body['identity_broken_epochs'] == [12]
    assert any('IDENTITY BROKEN' in line for line in body['alerts'])


def test_the_interest_excursion_alert_reaches_the_body(repository, client):
    """AC-D2's second alert class: a vault rate outside the tolerance band."""
    run_id = repository.start_run('test')
    _commit(repository, run_id, block=100, epoch=10, rfv=1000 * WAD,
            backing=80 * WAD, supply=12_500_000_000, morpho=100 * WAD)
    # morphoAssets moves 100 -> 130 with no booked flow: 300,000 ppm, three
    # orders of magnitude above the 10-95 ppm envelope.
    _commit(repository, run_id, block=200, epoch=11, rfv=1300 * WAD,
            backing=82 * WAD, supply=13_000_000_000, morpho=130 * WAD)
    repository.commit()
    body = _body(client)
    assert any('vault interest ran at' in line for line in body['alerts'])


def test_the_page_puts_the_alert_container_before_every_chart_canvas():
    """AC-D2, page half. A caveat under the picture it qualifies is a caveat
    nobody reads, so document order is the assertion."""
    page = PAGE.read_text()
    alerts_at = page.index('id="alerts"')
    for canvas in ('id="d1"', 'id="d2"', 'id="d3"'):
        assert alerts_at < page.index(canvas)
    assert page.index('id="identity-band"') < alerts_at


def test_the_identity_band_is_keyed_to_the_epoch_list_not_to_alert_text():
    assert 'body.identity_broken_epochs.length' in PAGE.read_text()


def test_the_page_renders_every_note_above_the_charts_too():
    """AC-D2 asks for the notes as well as the alerts, and D4 is the goal that
    the two surfaces cannot disagree about what is unavailable. The alert
    assertion above would hold on a page that dropped `body.notes` on the
    floor -- and the notes are the caveats ("fees unattributed", "Sleeve total
    is not in rfv") that stop a column being read as something it is not."""
    page = PAGE.read_text()
    assert 'body.notes' in page
    notes_at = page.index('id="notes"')
    for canvas in ('id="d1"', 'id="d2"', 'id="d3"'):
        assert notes_at < page.index(canvas)


# -- AC-D3: gaps stay gaps -------------------------------------------------


def test_a_missing_epoch_is_a_null_in_every_d1_series(repository, client):
    """AC-D3. The gap must be a null AT ITS INDEX, not a dropped point: a
    dropped point would close the line over the missing epoch silently."""
    run_id = repository.start_run('test')
    _commit(repository, run_id, block=100, epoch=10, rfv=1000 * WAD,
            backing=80 * WAD, supply=12_500_000_000)
    _commit(repository, run_id, block=300, epoch=12, rfv=1210 * WAD,
            backing=84 * WAD, supply=13_500_000_000)
    repository.commit()
    body = _body(client)
    assert [row['epoch'] for row in body['rows']] == [10, 11, 12]
    assert body['rows'][1]['present'] is False
    charts = body['charts']
    assert charts['d1']['epochs'] == [10, 11, 12]
    assert charts['d1']['backing_per_token'][1] is None
    assert charts['d1']['pair_price'][1] is None
    assert charts['d2']['treasury_growth_pct'][1] is None


def test_the_page_breaks_the_line_at_a_gap_rather_than_spanning_it():
    """AC-D3, page half: the literal Chart.js option that does it."""
    page = PAGE.read_text()
    d1 = page[page.index('function configD1'):page.index('function configD2')]
    d2 = page[page.index('function configD2'):page.index('function configD3')]
    assert d1.count('spanGaps: false') == 2
    assert d2.count('spanGaps: false') == 2


# -- AC-D4: sign and disagreement ------------------------------------------


def test_a_falling_backing_with_positive_growth_minus_dilution_reads_disagree(
    repository, client
):
    """AC-D4. Backing falls while the treasury outgrows emission -- the shape a
    burn or a data gap makes, and the one thing on D2 worth marking."""
    run_id = repository.start_run('test')
    _commit(repository, run_id, block=100, epoch=10, rfv=1000 * WAD,
            backing=80 * WAD, supply=12_500_000_000)
    _commit(repository, run_id, block=200, epoch=11, rfv=1100 * WAD,
            backing=79 * WAD, supply=13_000_000_000)
    repository.commit()
    row = _body(client)['rows'][1]
    assert Decimal(row['backing_change_pct']) < 0
    assert Decimal(row['growth_minus_dilution_pct']) > 0
    assert row['sign_agreement'] == 'disagree'


def test_an_agreeing_epoch_is_not_marked(repository, client):
    _seed_three_epochs(repository)
    row = _body(client)['rows'][1]
    assert Decimal(row['backing_change_pct']) > 0
    assert Decimal(row['growth_minus_dilution_pct']) > 0
    assert row['sign_agreement'] == 'agree'


def test_growth_minus_dilution_is_null_when_either_side_is(repository, client):
    """The first epoch has no previous close, so neither figure exists. A
    subtraction against an assumed zero would invent a difference."""
    _seed_three_epochs(repository)
    first = _body(client)['rows'][0]
    assert first['treasury_growth_pct'] is None
    assert first['growth_minus_dilution_pct'] is None
    assert first['sign_agreement'] == 'unavailable'


def test_the_page_marks_a_disagreeing_epoch_with_its_own_point_style():
    assert "'disagree' ? 'rectRot'" in PAGE.read_text()


# -- AC-D5: buckets from data ----------------------------------------------


def _seed_inflow_buckets(repository):
    run_id = repository.start_run('test')
    _commit(repository, run_id, block=100, epoch=10, rfv=1000 * WAD,
            backing=80 * WAD, supply=12_500_000_000)
    _commit(repository, run_id, block=200, epoch=11, rfv=1100 * WAD,
            backing=82 * WAD, supply=13_000_000_000)
    repository.insert_flows([
        # One labelled bucket, in epoch 11 only.
        {'project': PROJECT, 'block': 150, 'tx_hash': '0xb1', 'log_index': 1,
         'direction': 'in', 'counterparty': '0x9', 'amount': 90_000_000,
         'decimals': 6, 'label': 'bond', 'rule': 'bond:BondCreated'},
        # Three distinct unlabelled senders, which `report` keys per address.
        {'project': PROJECT, 'block': 151, 'tx_hash': '0xu1', 'log_index': 1,
         'direction': 'in', 'counterparty': '0xaaa', 'amount': 1_000_000,
         'decimals': 6, 'label': 'unlabelled', 'rule': 'unlabelled'},
        {'project': PROJECT, 'block': 152, 'tx_hash': '0xu2', 'log_index': 1,
         'direction': 'in', 'counterparty': '0xbbb', 'amount': 2_000_000,
         'decimals': 6, 'label': 'unlabelled', 'rule': 'unlabelled'},
        {'project': PROJECT, 'block': 153, 'tx_hash': '0xu3', 'log_index': 1,
         'direction': 'in', 'counterparty': '0xccc', 'amount': 4_000_000,
         'decimals': 6, 'label': 'unlabelled', 'rule': 'unlabelled'},
        # An inflow from inside rfv()'s boundary: the Morpho vault.
        {'project': PROJECT, 'block': 154, 'tx_hash': '0xv1', 'log_index': 1,
         'direction': 'in', 'counterparty': MORPHO_VAULT, 'amount': 5_000_000,
         'decimals': 6, 'label': 'unlabelled', 'rule': 'unlabelled'},
    ])
    repository.commit()


def test_per_address_unlabelled_keys_fold_into_one_bucket(repository, client):
    """AC-D5. On the real data the per-address keys are 220 one-epoch series;
    folded they are one. The flow detail keeps the addresses."""
    _seed_inflow_buckets(repository)
    row = _body(client)['rows'][1]
    buckets = row['inflows_chart']['buckets']
    assert set(buckets) == {'bond', 'unlabelled'}
    assert buckets['bond'] == '90'
    assert Decimal(buckets['unlabelled']) == Decimal(1 + 2 + 4 + 5)
    # The per-address keys are untouched in `inflows` itself.
    assert len([k for k in row['inflows'] if k.startswith('unlabelled:')]) == 4


def test_a_bucket_holding_an_internal_flow_keeps_its_internal_marker(
    repository, client
):
    """The vault sender folds into `unlabelled` with three external ones, and
    the marker must survive that sum -- an internal flow shown as ordinary is
    exactly what the marker exists to prevent."""
    _seed_inflow_buckets(repository)
    row = _body(client)['rows'][1]
    assert 'unlabelled' in row['inflows_chart']['internal']
    assert any(k.startswith('unlabelled:') for k in row['internal_flows']['in'])


def test_a_labelled_bucket_is_absent_in_the_epoch_it_did_not_appear_in(
    repository, client
):
    _seed_inflow_buckets(repository)
    charts = _body(client)['charts']
    assert set(charts['d3']['buckets']) == {'bond', 'unlabelled'}
    assert charts['d3']['buckets']['bond'] == [None, '90']
    assert charts['d3']['internal'] == [[], ['unlabelled']]


def test_the_page_stacks_d3(repository):
    page = PAGE.read_text()
    d3 = page[page.index('function configD3'):page.index('function rawOrNa')]
    assert d3.count('stacked: true') == 2


# -- AC-D6: traceable point ------------------------------------------------


def test_every_chart_index_carries_the_epoch_block_and_close_time(
    repository, client
):
    """AC-D6. `points` is indexed the same as every series, so a hovered point
    resolves to the sample it was read at without the page joining anything."""
    _seed_three_epochs(repository)
    body = _body(client)
    for index, row in enumerate(body['rows']):
        point = body['charts']['points'][index]
        assert point['epoch'] == row['epoch']
        assert point['block'] == row.get('block')
        assert point['block_time_utc'] == row.get('block_time_utc')


def test_every_chart_config_reads_the_shared_tooltip_title():
    page = PAGE.read_text()
    assert 'charts.points[index]' in page
    for name in ('configD1', 'configD2', 'configD3'):
        start = page.index('function ' + name)
        end = page.index('\n}\n', start)
        assert 'title: titleCallback(charts)' in page[start:end]


# -- AC-D7: bounded render -------------------------------------------------


def _count_queries(monkeypatch, repository):
    """Every statement `load_epoch_rows` issues, in order."""
    statements = []
    original = ProjectMonitorRepository.fetch_all

    def counting(self, statement, params=()):
        statements.append(' '.join(statement.split()))
        return original(self, statement, params)

    monkeypatch.setattr(ProjectMonitorRepository, 'fetch_all', counting)
    load_epoch_rows(repository, NETNET)
    monkeypatch.undo()
    return statements


def test_the_store_is_read_in_five_queries_whatever_the_epoch_count(
    monkeypatch, repository
):
    """AC-D7's mechanism, which the 6-second bound rests on.

    The bound itself is measured by hand against a 1,095-epoch fill
    (`scripts/seed_project_monitor_dashboard_bench.py`); a suite that seeded a
    year of epochs would cost minutes per run and would still time this
    machine, not the operator's. What CI can hold instead is the property that
    makes the measurement generalise: the query count is a constant, not a
    multiple of the epoch count. The old pattern was five queries PER epoch,
    which is the regression this refuses.
    """
    _seed_three_epochs(repository)
    three = _count_queries(monkeypatch, repository)

    with repository.connection.cursor() as cursor:
        cursor.execute(
            'TRUNCATE sample, reading, mint, flow, event, run RESTART IDENTITY CASCADE'
        )
    repository.commit()
    seed_golden(repository)
    five = _count_queries(monkeypatch, repository)

    assert len(three) == len(five) == 5, (three, five)


def test_the_event_read_is_filtered_to_the_names_the_table_uses(
    monkeypatch, repository
):
    """The prefetch's one selective read. Unfiltered it would pull the whole
    event table -- 76,151 rows on the operator store, 94% of them transfers no
    column reads -- and the constant above would still hold while the timing
    stopped meeting the bound."""
    _seed_three_epochs(repository)
    event_reads = [s for s in _count_queries(monkeypatch, repository) if 'FROM event' in s]
    assert len(event_reads) == len(EVENT_NAMES_READ)
    for statement in event_reads:
        assert 'AND name = %s' in statement


# -- AC-D8: freshness ------------------------------------------------------


def _rows_closed_hours_ago(now, hours, minutes=0):
    closed = now - timedelta(hours=hours, minutes=minutes)
    return [{
        'epoch': 138, 'present': True, 'block': 1,
        'block_timestamp': int(closed.timestamp()),
        'block_time_utc': closed.strftime('%Y-%m-%dT%H:%M:%SZ'),
    }]


def test_freshness_is_not_stale_just_under_the_threshold():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    state = freshness(_rows_closed_hours_ago(now, 15, 59), now)
    assert state['stale'] is False
    assert state['latest_epoch'] == 138
    assert state['threshold_hours'] == STALE_AFTER_HOURS


def test_freshness_is_stale_just_over_the_threshold():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    assert freshness(_rows_closed_hours_ago(now, 16, 1), now)['stale'] is True


def test_an_empty_store_is_stale_not_fresh():
    """No record is the stalest record: reporting it fresh would let a store
    that was never written look like a market that never moved."""
    state = freshness([], datetime.now(timezone.utc))
    assert state['stale'] is True
    assert state['latest_epoch'] is None


def test_the_route_reports_freshness_against_the_latest_present_epoch(
    repository, client
):
    _seed_three_epochs(repository)
    state = _body(client)['freshness']
    assert state['latest_epoch'] == 12
    assert state['threshold_hours'] == STALE_AFTER_HOURS
    # The fixture's close times are in 2026-08, so the record is long stale.
    assert state['stale'] is True


def test_the_route_calls_a_recently_closed_epoch_fresh(repository, client):
    """AC-D8's other half through the route. The stale case above would also
    pass if the route reported `stale: True` unconditionally -- which is the
    likelier bug here, because with no scheduled writer stale is the state the
    operator sees every time and a hardcoded badge would look right."""
    now = datetime.now(timezone.utc)
    run_id = repository.start_run('test')
    _commit(repository, run_id, block=100, epoch=10, rfv=1000 * WAD,
            backing=80 * WAD, supply=12_500_000_000,
            timestamp=int((now - timedelta(hours=1)).timestamp()))
    repository.commit()
    state = _body(client)['freshness']
    assert state['stale'] is False
    assert state['latest_epoch'] == 10


# -- AC-D9: local only -----------------------------------------------------


class _DummyRequest:
    """Varies the client address only; `Host` is held at a local name so these
    cases isolate the address check from the rebinding check below."""

    def __init__(self, host):
        self.client = SimpleNamespace(host=host) if host is not None else None
        self.headers = {'host': '127.0.0.1:8765'}


@pytest.mark.asyncio
async def test_the_middleware_refuses_a_non_loopback_client():
    async def call_next(_):
        raise AssertionError('the request should not have reached the route')

    response = await dashboard.loopback_only(_DummyRequest('203.0.113.10'), call_next)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_the_middleware_refuses_a_request_with_no_client_address():
    """An unknown origin is not a local one."""
    async def call_next(_):
        raise AssertionError('the request should not have reached the route')

    response = await dashboard.loopback_only(_DummyRequest(None), call_next)
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize('host', ['127.0.0.1', '::1'])
async def test_the_middleware_passes_a_loopback_client(host):
    async def call_next(_):
        return SimpleNamespace(status_code=200)

    response = await dashboard.loopback_only(_DummyRequest(host), call_next)
    assert response.status_code == 200


def test_a_public_client_is_refused_through_the_whole_stack(repository):
    """The unit test above proves the function; this proves it is wired in."""
    public = _loopback_client(build_app(), host='203.0.113.10')
    assert public.get('/project-monitor/netnet/report').status_code == 403
    assert public.get('/project-monitor/').status_code == 403


class _HostRequest:
    """Loopback client address, arbitrary `Host` -- the DNS-rebinding shape."""

    def __init__(self, host_header):
        self.client = SimpleNamespace(host='127.0.0.1')
        self.headers = {'host': host_header} if host_header is not None else {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'host_header',
    ['attacker.example', 'attacker.example:8765', 'netnet.local', '', None,
     '[::1', '127.0.0.1.attacker.example'],
)
async def test_a_rebound_host_is_refused_even_from_a_loopback_client(host_header):
    """DNS rebinding: the operator's own browser makes the request, so the
    client address IS loopback. The attacker's domain in `Host` is the only
    thing that distinguishes it, which is why this check exists beside the
    address one rather than instead of it."""
    async def call_next(_):
        raise AssertionError('the request should not have reached the route')

    response = await dashboard.loopback_only(_HostRequest(host_header), call_next)
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'host_header',
    ['127.0.0.1', '127.0.0.1:8765', 'localhost', 'localhost:8765', '[::1]:8765'],
)
async def test_the_names_a_local_browser_actually_sends_are_allowed(host_header):
    """The other half of the check: it must not refuse the operator. Every form
    a browser puts in `Host` for this server, with and without the port."""
    async def call_next(_):
        return SimpleNamespace(status_code=200)

    response = await dashboard.loopback_only(_HostRequest(host_header), call_next)
    assert response.status_code == 200


def test_a_rebound_host_is_refused_through_the_whole_stack(repository):
    rebound = _loopback_client(build_app(), host_header=b'attacker.example')
    assert rebound.get('/project-monitor/netnet/report').status_code == 403
    assert rebound.get('/project-monitor/').status_code == 403


# -- AC-D10: no egress -----------------------------------------------------


def test_the_page_references_no_host_but_this_one():
    """AC-D10. Scans every URL-bearing form the page could carry: a `src`, an
    `href`, a CSS `url(`, and any protocol-absolute string."""
    import re

    page = PAGE.read_text()
    for match in re.findall(r'(?:src|href)\s*=\s*"([^"]*)"', page):
        assert match.startswith('static/') or match.startswith('data:'), match
    assert 'url(' not in page
    for scheme in ('http://', 'https://', '//cdn', 'ws://', 'wss://'):
        assert scheme not in page, scheme


def test_the_vendored_library_is_the_recorded_release():
    """The licence file's checksum is the claim; this is the check on it."""
    import hashlib

    licence = (Path(dashboard.STATIC_DIR) / 'LICENSE-chartjs').read_text()
    recorded = [
        line.split(':', 1)[1].strip()
        for line in licence.splitlines()
        if line.startswith('sha256:')
    ]
    library = (Path(dashboard.STATIC_DIR) / 'chart.umd.js').read_bytes()
    assert recorded == [hashlib.sha256(library).hexdigest()]


def test_the_static_routes_serve_the_two_vendored_files(repository, client):
    page = client.get('/project-monitor/')
    assert page.status_code == 200
    assert page.headers['cache-control'] == 'no-store'
    assert '<canvas id="d1"' in page.text
    library = client.get('/project-monitor/static/chart.umd.js')
    assert library.status_code == 200
    assert 'Chart.js v4.4.1' in library.text[:200]


# -- AC-D11: no writes -----------------------------------------------------


def test_a_read_only_repository_makes_the_server_refuse_a_write(database_url):
    """AC-D11's mechanism. Not "the route only runs SELECTs today" -- that is a
    property a reviewer has to re-check on every change. `read_only=True` opens
    each transaction `BEGIN READ ONLY`, so Postgres itself refuses."""
    with ProjectMonitorRepository(database_url, read_only=True) as repository:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            repository.fetch_all(
                "INSERT INTO run (job, outcome) VALUES ('dashboard-test', 'ok')"
            )


def test_the_route_opens_the_store_read_only(monkeypatch, repository, database_url):
    """The wiring the test above cannot see.

    A route that only issues SELECTs today passes the count check below whether
    or not `read_only` is set, so the count check would go on passing after
    someone opened the connection normally -- and the server-side refusal, the
    thing that makes DR12 a guarantee rather than a habit, would be gone with
    nothing red.
    """
    opened = []
    original = dashboard.ProjectMonitorRepository

    def record(url, **kwargs):
        opened.append(kwargs.get('read_only'))
        return original(database_url, **kwargs)

    monkeypatch.setattr(
        dashboard, 'get_project_monitor_database_url', lambda runtime_mode: database_url
    )
    monkeypatch.setattr(dashboard, 'ProjectMonitorRepository', record)
    _seed_three_epochs(repository)
    _body(_loopback_client(build_app()))
    assert opened == [True]


def test_ten_fetches_create_no_run_row_and_change_no_count(repository, client):
    """AC-D11 in the shape the criterion states it."""
    counts = _table_counts(repository)
    for _ in range(10):
        _body(client)
    assert _table_counts(repository) == counts


# Every table in the store, not only the six the report path reads: AC-D11 asks
# for the count of every table, and a write the dashboard should never make is
# likelier to land somewhere unexpected than in `sample`. Kept in step with
# `conftest.TABLES` by `test_the_count_check_covers_every_table_in_the_store`.
ALL_TABLES = (
    'manifest_diff', 'manifest_snapshot', 'raw_response', 'backfill_log_raw_response',
    'reading', 'sample', 'mint', 'flow', 'event', 'epoch_boundary', 'contract',
    'project', 'run',
)


def _table_counts(repository):
    return {
        table: repository.fetch_all(f'SELECT count(*) AS n FROM {table}')[0]['n']
        for table in ALL_TABLES
    }


def test_the_count_check_covers_every_table_in_the_store(repository):
    """A table added to the schema and not to `ALL_TABLES` is a table the
    no-writes check stops watching, silently and while staying green."""
    in_store = {
        row['tablename'] for row in repository.fetch_all(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
    }
    assert set(ALL_TABLES) == in_store


# -- AC-D12: unavailable stays unavailable ---------------------------------


def test_buyback_filled_is_null_beside_its_state_never_zero(repository, client):
    _seed_three_epochs(repository)
    buyback = _body(client)['rows'][1]['buyback']
    assert buyback['filled'] is None
    assert buyback['filled_state'] == 'no_onchain_source'


def test_the_page_draws_no_row_field_beyond_the_charted_ones():
    """AC-D12's page half. The property is stronger than "no buyback column":
    the page never touches `body.rows` at all, so there is no row field for it
    to list and nowhere for an invented value to appear. Asserted on the READ,
    not on the words -- `residual` occurs in the identity band's prose."""
    page = PAGE.read_text()
    assert 'body.rows' not in page
    assert '.rows[' not in page
    for read in ('buyback', 'sleeve_total_usd', 'loopback.', 'rfv_components'):
        assert read not in page, read


# -- AC-D13: price beside backing ------------------------------------------


def test_a_null_price_is_a_missing_point_not_a_zero(repository, client):
    """AC-D13. `pair_price` needs the pair readings; the second epoch here has
    none, which is the same shape a failed peripheral read makes."""
    run_id = repository.start_run('test')
    _commit(repository, run_id, block=100, epoch=10, rfv=1000 * WAD,
            backing=80 * WAD, supply=12_500_000_000)
    _commit(repository, run_id, block=200, epoch=11, rfv=1100 * WAD,
            backing=82 * WAD, supply=13_000_000_000, reserves=False)
    repository.commit()
    charts = _body(client)['charts']
    assert charts['d1']['pair_price'][0] is not None
    assert charts['d1']['pair_price'][1] is None
    assert charts['d1']['backing_per_token'][1] is not None


def test_the_page_gives_price_its_own_axis():
    page = PAGE.read_text()
    d1 = page[page.index('function configD1'):page.index('function configD2')]
    assert "yAxisID: 'y2'" in d1
    assert 'y2: {' in d1


# -- charts <-> rows invariant ---------------------------------------------


def _assert_charts_match_rows(body):
    rows, charts = body['rows'], body['charts']

    assert charts['d1']['epochs'] == [row['epoch'] for row in rows]
    for view, keys in (
        ('d1', ('backing_per_token', 'pair_price')),
        ('d2', ('treasury_growth_pct', 'dilution_pct', 'backing_change_pct',
                'growth_minus_dilution_pct', 'sign_agreement')),
    ):
        for key in keys:
            expected = [row[key] if row['present'] else None for row in rows]
            assert charts[view][key] == expected, (view, key)

    union = sorted({
        key for row in rows for key in (row.get('inflows_chart') or {}).get('buckets', {})
    })
    assert sorted(charts['d3']['buckets']) == union
    for key in union:
        assert charts['d3']['buckets'][key] == [
            (row.get('inflows_chart') or {}).get('buckets', {}).get(key) for row in rows
        ]
    assert charts['d3']['internal'] == [
        list((row.get('inflows_chart') or {}).get('internal') or []) for row in rows
    ]
    for index, row in enumerate(rows):
        assert charts['points'][index] == {
            'epoch': row['epoch'],
            'block': row.get('block'),
            'block_time_utc': row.get('block_time_utc'),
        }


def test_chart_series_is_a_lossless_reshaping_of_the_rows(repository, client):
    """One body carries the same figures twice. This pins the second copy to the
    first, so a divergence cannot be visible only on the page."""
    _seed_inflow_buckets(repository)
    _assert_charts_match_rows(_body(client))


def test_the_charts_match_the_rows_on_a_gap_and_a_null_price_too(repository, client):
    """The same invariant over the seed that carries every awkward shape at
    once: a gap row, an epoch with no pair readings, a folded unlabelled
    bucket, an internal marker, and a broken identity. The buckets test above
    runs on two clean epochs, where a reshaping bug that only mishandles a gap
    would not show."""
    seed_golden(repository)
    _assert_charts_match_rows(_body(client))


def test_chart_series_holds_on_a_gap_row_too(repository):
    """The invariant, checked directly on `chart_series` so it is pinned even
    where no route is involved."""
    run_id = repository.start_run('test')
    _commit(repository, run_id, block=100, epoch=10, rfv=1000 * WAD,
            backing=80 * WAD, supply=12_500_000_000)
    _commit(repository, run_id, block=300, epoch=12, rfv=1210 * WAD,
            backing=84 * WAD, supply=13_500_000_000)
    repository.commit()
    rows = load_epoch_rows(repository, NETNET)
    charts = chart_series(rows)
    assert charts['points'][1] == {'epoch': 11, 'block': None, 'block_time_utc': None}
    assert charts['d3']['internal'][1] == []


# -- error handling --------------------------------------------------------


def test_an_unreachable_store_answers_503_naming_only_the_exception_class(
    monkeypatch, repository
):
    """The page must be able to SAY the store is down. What it must never carry
    is the connection string, which psycopg puts in some of its messages."""
    def boom(*_args, **_kwargs):
        raise psycopg.OperationalError(
            'connection to postgresql://postgres:devpass@127.0.0.1:55432/x failed'
        )

    monkeypatch.setattr(dashboard, 'ProjectMonitorRepository', boom)
    response = _loopback_client(build_app()).get('/project-monitor/netnet/report')
    assert response.status_code == 503
    assert response.json() == {'error': 'OperationalError'}
    assert 'postgres' not in response.text


def test_a_store_with_no_tables_yet_is_a_503_not_a_traceback(monkeypatch, repository):
    """The other 503 the design names, and the one the operator will actually
    meet: the dashboard opens the store `read_only=True`, which skips the DDL,
    so a database nobody has run `report` or the suite against has no tables.
    `UndefinedTable` is a `ProgrammingError`, a different `psycopg.Error`
    subclass from the unreachable-store case above -- an `except` narrowed to
    `OperationalError` would pass that test and 500 here."""
    def boom(*_args, **_kwargs):
        raise psycopg.errors.UndefinedTable('relation "sample" does not exist')

    monkeypatch.setattr(dashboard, 'ProjectMonitorRepository', boom)
    response = _loopback_client(build_app()).get('/project-monitor/netnet/report')
    assert response.status_code == 503
    assert response.json() == {'error': 'UndefinedTable'}


# -- the entrypoint --------------------------------------------------------


def test_the_entrypoint_binds_loopback_and_offers_no_host_flag():
    """DR10's first mechanism. The absence of a `--host` flag is half of it:
    the middleware can be reasoned about, but a flag that can open the bind is
    one typo from making the page reachable, so there must not be one."""
    import re

    source = Path(dashboard_job.__file__).read_text()
    assert dashboard_job.HOST == '127.0.0.1'
    # The flags the entrypoint actually registers, not every mention of one:
    # `--host` appears in the comment that explains its absence.
    flags = re.findall(r"add_argument\(\s*'(--[a-z-]+)'", source)
    assert flags == ['--port']
    assert 'uvicorn.run(app, host=HOST' in source


def test_the_dashboard_app_carries_only_the_read_routes_and_no_startup_hook():
    """DQ1's answer, pinned. The reason this is a second entrypoint rather than
    a route on `src/server.py` is that the main app's startup builds the
    Telegram bots and the Redis client and binds `0.0.0.0`. Including this
    router there, or copying a startup handler in, would put those credentials
    back on a path that needs none of them."""
    app = build_app()
    paths = {route.path for route in app.routes if route.path.startswith('/project-monitor')}
    assert paths == {
        '/project-monitor/',
        '/project-monitor/static/chart.umd.js',
        '/project-monitor/netnet/report',
    }
    assert app.router.on_startup == []
    assert app.router.on_shutdown == []


def test_test_mode_selects_the_test_database(monkeypatch, repository):
    """DQ4: one query parameter, resolved through the same `RuntimeMode` the CLI
    flag uses -- not a second process and not an environment variable."""
    seen = []

    def record_mode(runtime_mode):
        seen.append(runtime_mode.is_test_mode)
        return repository.database_url

    monkeypatch.setattr(dashboard, 'get_project_monitor_database_url', record_mode)
    client = _loopback_client(build_app())
    client.get('/project-monitor/netnet/report')
    client.get('/project-monitor/netnet/report', params={'test_mode': 1})
    assert seen == [False, True]
