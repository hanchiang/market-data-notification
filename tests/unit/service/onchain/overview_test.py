"""The overview loader (UX brief, slice B): sort order, deltas, gaps for a
project whose latest build failed, and the inherited flag on chain-level
source rows. Store-backed, like the dossier loader it is built on."""
import copy

import pytest

from src.service.onchain import builder
from src.service.onchain.registry import parse_registry, upsert_registry
from src.service.onchain.report import SOURCE_BADGE_ORDER, load_overview, metric_values

ROBINHOOD = 4663


def _project(key, pool_ref, sources=()):
    return {
        'key': key, 'display_name': key.replace('-', ' ').title(), 'chain_id': ROBINHOOD,
        'archetype': 'launchpad-fixed-supply', 'pool_ref': pool_ref, 'sources': list(sources),
    }


@pytest.fixture
def three_projects(onchain_repository, onchain_registry_payload):
    payload = copy.deepcopy(onchain_registry_payload)
    payload['projects'] = [
        _project('alpha', '0x' + 'aa' * 32, [
            {'class': 'web', 'url': 'https://alpha.example'},
            {'class': 'x', 'url': 'https://x.com/alpha'},
        ]),
        _project('beta', '0x' + 'bb' * 20),
        _project('gamma', '0x' + 'cc' * 20),
    ]
    ids = upsert_registry(onchain_repository, parse_registry(payload))
    onchain_repository.commit()
    return ids


def _pairs(liquidity, holders):
    return [
        {'metric': 'liquidity_usd', 'value': liquidity, 'counterpart': 'custody',
         'counterpart_value': {}, 'guards_against': 'liquidity_pull'},
        {'metric': 'holder_count', 'value': holders, 'counterpart': 'new_vs_returning',
         'counterpart_value': {}, 'guards_against': 'split'},
    ]


def _build(repository, project_id, *, block, liquidity, holders, outcome='ok', flagged=None,
           previous=None):
    """One build with a health and an identity section; with `previous`, a
    section diff against it so the loader has an `old` side to read."""
    run_id = repository.start_run(builder.JOB_BUILD)
    build_id = repository.start_build(
        run_id=run_id, project_id=project_id, block=block, block_timestamp=1_789_000_000 + block
    )
    if outcome == 'failed':
        repository.insert_section(
            build_id=build_id, name='onchain_health', status='failed',
            error_class='EvmTransportError', fields={},
        )
    else:
        repository.insert_section(
            build_id=build_id, name='identity', status='ok',
            fields={'token_symbol': 'T', 'decimals': 18, 'pool_count': 2},
        )
        repository.insert_section(
            build_id=build_id, name='onchain_health', status='ok',
            fields={'pairs': _pairs(liquidity, holders), 'price_usd': 0.5, 'fdv_usd': 100.0},
        )
    repository.finish_build(build_id, outcome=outcome, threshold_version='2026-09-06.1')
    repository.finish_run(run_id, outcome=outcome, failed_units=[], spend={})
    if previous is not None:
        sections = {
            row['name']: row for row in repository.get_sections_for_build(build_id)
        }
        before = {
            row['name']: row for row in repository.get_sections_for_build(previous)
        }
        repository.insert_section_diff(
            section_id=int(sections['onchain_health']['id']),
            previous_section_id=int(before['onchain_health']['id']),
            changes={'added': {}, 'removed': {}, 'changed': [{
                'field': 'pairs',
                'old': before['onchain_health']['fields_json']['pairs'],
                'new': sections['onchain_health']['fields_json']['pairs'],
            }]},
            flagged=flagged,
        )
    repository.commit()
    return build_id


class TestLoadOverview:
    def test_projects_sort_by_liquidity_descending_with_gaps_last(
        self, onchain_repository, three_projects
    ):
        _build(onchain_repository, three_projects['alpha'], block=1, liquidity=100.0, holders=5)
        _build(onchain_repository, three_projects['beta'], block=1, liquidity=900.0, holders=7)
        good = _build(onchain_repository, three_projects['gamma'], block=1, liquidity=5000.0, holders=9)
        # Gamma had the most liquidity last night; its latest build failed, so
        # it carries no figure now and sorts last rather than on a stale one.
        failed = _build(onchain_repository, three_projects['gamma'], block=2, liquidity=0, holders=0,
                        outcome='failed')

        overview = load_overview(onchain_repository)
        assert [p['key'] for p in overview['projects']] == ['beta', 'alpha', 'gamma']
        gamma = overview['projects'][-1]
        assert gamma['build']['id'] == failed and gamma['build']['id'] != good
        assert gamma['build']['outcome'] == 'failed'
        assert all(value is None for value in gamma['values'].values())
        assert all(value is None for value in gamma['previous'].values())
        beta = overview['projects'][0]
        assert beta['values']['liquidity_usd'] == 900.0 and beta['values']['holders'] == 7
        assert beta['values']['pool_count'] == 2 and beta['values']['volume_h24_usd'] is None
        assert beta['chain'] == 'Robinhood Chain' and beta['chain_key'] == 'robinhood'
        assert set(beta['build']) == {'id', 'run_id', 'block_timestamp', 'outcome'}

    def test_a_project_with_no_build_sorts_last_with_no_build_and_no_values(
        self, onchain_repository, three_projects
    ):
        _build(onchain_repository, three_projects['alpha'], block=1, liquidity=100.0, holders=5)
        overview = load_overview(onchain_repository)
        assert [p['key'] for p in overview['projects']] == ['alpha', 'beta', 'gamma']
        for project in overview['projects'][1:]:
            assert project['build'] is None
            assert set(project['values']) == set(metric_values([]))
            assert all(v is None for v in project['values'].values())
            assert project['flag_count'] == 0

    def test_previous_values_come_from_the_section_diff_and_flags_are_counted(
        self, onchain_repository, three_projects
    ):
        """The delta is the dossier tile's delta: the `old` side of the stored
        diff, not a second read of the previous build."""
        first = _build(onchain_repository, three_projects['alpha'], block=1, liquidity=100.0, holders=5)
        _build(
            onchain_repository, three_projects['alpha'], block=2, liquidity=150.0, holders=4,
            previous=first, flagged=[{'field': 'pairs', 'reason': 'holders fell'}],
        )
        alpha = load_overview(onchain_repository)['projects'][0]
        assert alpha['values']['liquidity_usd'] == 150.0 and alpha['previous']['liquidity_usd'] == 100.0
        assert alpha['values']['holders'] == 4 and alpha['previous']['holders'] == 5
        # A metric outside the diff's changed fields is unchanged, so its
        # previous value is its current one (slice A's rule); one the build
        # never stores is None on both sides.
        assert alpha['values']['price_usd'] == 0.5 and alpha['previous']['price_usd'] == 0.5
        assert alpha['values']['volume_h24_usd'] is None and alpha['previous']['volume_h24_usd'] is None
        assert alpha['flag_count'] == 1

    def test_an_unchanged_metric_reads_its_current_value_as_previous(
        self, onchain_repository, three_projects
    ):
        first = _build(onchain_repository, three_projects['alpha'], block=1, liquidity=100.0, holders=5)
        _build(onchain_repository, three_projects['alpha'], block=2, liquidity=100.0, holders=5, previous=first)
        alpha = load_overview(onchain_repository)['projects'][0]
        # `pairs` is in `changed` (the diff lists the field even when the two
        # rows read the same), so the old side is read: equal to the new one.
        assert alpha['previous']['liquidity_usd'] == 100.0

    def test_coverage_lists_every_class_with_chain_rows_marked_inherited(
        self, onchain_repository, three_projects
    ):
        overview = load_overview(onchain_repository)
        assert overview['classes'] == list(SOURCE_BADGE_ORDER)
        alpha = overview['coverage']['alpha']
        assert set(alpha) >= set(SOURCE_BADGE_ORDER)
        for source_class in ('chain_rpc', 'chain_explorer', 'dex_provider'):
            rows = alpha[source_class]
            assert len(rows) == 1, source_class
            assert rows[0]['inherited'] is True
            assert rows[0]['admission'] == 'admitted' and rows[0]['admitted_by'] == 'registry'
            assert rows[0]['evidence']['path'].endswith('projects.json')
        assert alpha['dex_provider'][0]['url_or_handle'] == 'https://dexscreener.com/robinhood'
        assert [r['url_or_handle'] for r in alpha['web']] == ['https://alpha.example']
        assert alpha['web'][0]['inherited'] is False
        assert alpha['x'][0]['inherited'] is False
        assert alpha['telegram'] == []
        # Beta supplied nothing: its project classes are empty, its chain classes inherited.
        beta = overview['coverage']['beta']
        assert beta['web'] == [] and beta['x'] == []
        assert beta['chain_explorer'][0]['inherited'] is True

    def test_a_candidate_written_by_the_build_appears_as_a_candidate(
        self, onchain_repository, three_projects
    ):
        builder.store_candidate_sources(
            onchain_repository, three_projects['beta'], ['https://beta.example/docs']
        )
        onchain_repository.commit()
        beta = load_overview(onchain_repository)['coverage']['beta']
        assert beta['web'] == [{
            'url_or_handle': 'https://beta.example/docs', 'admission': 'candidate',
            'admitted_by': None, 'evidence': {'hop_from': 'dex_provider', 'phase': '1a'},
            'inherited': False,
        }]

    def test_the_rpc_row_is_a_host_and_never_a_keyed_url(self, onchain_repository, three_projects):
        rows = load_overview(onchain_repository)['coverage']['alpha']['chain_rpc']
        handle = rows[0]['url_or_handle']
        assert '://' not in handle and '?' not in handle and '/' not in handle
