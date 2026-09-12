"""The market overview route and page (UX brief, slice B), through the REAL
loopback middleware, with the same shim the dossier route tests use.

The properties under test: one row per project and one grid cell per project
per class; both panels carry their decision tag; the page loads nothing from
outside itself and carries no URL outside navigation anchors; and
`?format=json` is the loader's payload."""
import json
import re

import pytest

from src.job.project_monitor.dashboard import build_app
from src.router.project_monitor import dashboard
from src.service.onchain import builder, overview, page
from src.service.onchain.registry import parse_registry, upsert_registry
from src.service.onchain.report import SOURCE_BADGE_ORDER, load_overview
from tests.unit.conftest import FAKE_RPC_HOST
from tests.unit.service.onchain.dossier_route_test import _loopback_client

ROBINHOOD = 4663


@pytest.fixture
def client(monkeypatch, onchain_database_url):
    monkeypatch.setattr(
        dashboard, 'get_onchain_database_url', lambda runtime_mode: onchain_database_url
    )
    return _loopback_client(build_app())


@pytest.fixture
def seeded(onchain_repository, onchain_registry_payload):
    """Three projects: one with operator sources and a build carrying every
    KPI, one with a build-written candidate and a failed latest build, one
    with no build at all."""
    payload = onchain_registry_payload
    payload['projects'][0]['sources'] = [
        {'class': 'web', 'url': 'https://www.touchgrass.family'},
        {'class': 'web', 'url': 'http://notawebsite.fun/'},
        {'class': 'x', 'url': 'https://x.com/TouchGrassRWA'},
    ]
    payload['projects'].extend([
        {'key': 'zzz', 'display_name': 'ZZZ', 'chain_id': ROBINHOOD,
         'archetype': 'launchpad-fixed-supply', 'pool_ref': '0x' + 'bb' * 32, 'sources': []},
        {'key': 'quiet', 'display_name': 'Quiet <One>', 'chain_id': ROBINHOOD,
         'archetype': 'launchpad-fixed-supply', 'pool_ref': '0x' + 'cc' * 20, 'sources': []},
    ])
    ids = upsert_registry(onchain_repository, parse_registry(payload))
    builder.store_candidate_sources(onchain_repository, ids['zzz'], ['https://zzz.example/'])
    _build(onchain_repository, ids['touch-grass'], block=1, liquidity=173_738.69)
    _build(onchain_repository, ids['zzz'], block=1, liquidity=50.0)
    _build(onchain_repository, ids['zzz'], block=2, liquidity=None, outcome='failed')
    onchain_repository.commit()
    return ids


def _build(repository, project_id, *, block, liquidity, outcome='ok'):
    run_id = repository.start_run(builder.JOB_BUILD)
    build_id = repository.start_build(
        run_id=run_id, project_id=project_id, block=block, block_timestamp=1_789_000_000 + block
    )
    if outcome == 'ok':
        repository.insert_section(
            build_id=build_id, name='identity', status='ok',
            fields={'token_symbol': 'GRASS', 'decimals': 18, 'pool_count': 12},
        )
        repository.insert_section(
            build_id=build_id, name='onchain_health', status='ok',
            fields={'pairs': [
                {'metric': 'liquidity_usd', 'value': liquidity, 'counterpart': 'custody',
                 'counterpart_value': {}, 'guards_against': 'liquidity_pull'},
                {'metric': 'dex_volume_h24_usd', 'value': 418_800.0, 'counterpart': 'a',
                 'counterpart_value': 1, 'guards_against': 'wash'},
                {'metric': 'dex_trades_h24', 'value': 1_161, 'counterpart': 'b',
                 'counterpart_value': 1, 'guards_against': 'churn'},
                {'metric': 'holder_count', 'value': 6_598, 'counterpart': 'c',
                 'counterpart_value': {}, 'guards_against': 'split'},
            ], 'price_usd': 0.003095, 'fdv_usd': 3_048_089.0},
        )
        repository.insert_section(
            build_id=build_id, name='token_economics', status='ok',
            fields={'top_ten_share': 0.266329},
        )
    else:
        repository.insert_section(
            build_id=build_id, name='onchain_health', status='failed',
            error_class='EvmTransportError', fields={},
        )
    repository.finish_build(build_id, outcome=outcome, threshold_version='2026-09-06.1')
    repository.finish_run(run_id, outcome=outcome, failed_units=[], spend={})
    return build_id


def _body(client, path='/project-monitor/onchain?test_mode=1'):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    return response.text


class TestOverviewRoute:
    def test_the_page_has_one_row_per_project_and_one_cell_per_project_per_class(
        self, client, seeded
    ):
        body = _body(client)
        rows = re.findall(r'<tr data-project="([^"]+)">', body)
        # Each project appears once in the table and once in the grid, in the
        # loader's order: liquidity descending, then the two with no figure
        # (a failed latest build, no build at all) by key.
        assert rows == ['touch-grass', 'quiet', 'zzz'] * 2
        cells = re.findall(r'<td class="c"( data-target="cov-[^"]+")? title="([^"]+)"', body)
        assert len(cells) == 3 * len(SOURCE_BADGE_ORDER)
        assert [t.split(':', 1)[0] for _, t in cells[:6]] == list(SOURCE_BADGE_ORDER)
        # Only a cell with source rows opens a list, and only those lists are
        # on the page: touch-grass has 3 chain + 2 web + 1 x, quiet 3 chain,
        # zzz 3 chain + 1 candidate web.
        targets = re.findall(r'<td class="c" data-target="(cov-[^"]+)"', body)
        assert len(targets) == 5 + 3 + 4
        assert targets[:5] == [f'cov-touch-grass-{c}' for c in ('chain_rpc', 'chain_explorer', 'dex_provider', 'web', 'x')]
        assert re.findall(r'<details id="(cov-[^"]+)"', body) == targets
        assert '<td class="c" title="telegram: no source row reaches this project">' in body

    def test_trailing_slash_and_bare_paths_both_serve_the_page(self, client, seeded):
        assert '<h1>Market overview</h1>' in _body(client, '/project-monitor/onchain/?test_mode=1')
        assert '<h1>Market overview</h1>' in _body(client)

    def test_both_panels_carry_their_decision_tags(self, client, seeded):
        body = _body(client)
        table = body[body.index('id="projects"'):body.index('id="coverage"')]
        grid = body[body.index('id="coverage"'):]
        assert f'<span class="tag" title="{page.DECISION_TAGS["projects"]}">enter / add / reduce / exit</span>' in table
        assert f'<span class="tag" title="{page.DECISION_TAGS["coverage"]}">admit / drop a source</span>' in grid
        assert page.DECISION_TAGS['projects'].startswith('enter / add / reduce / exit · slow: where to spend attention')
        assert page.DECISION_TAGS['coverage'].startswith('admit / drop a source · slow: which classes are missing')

    def test_the_table_reads_the_briefs_figures_with_deltas_and_status(self, client, seeded):
        body = _body(client)
        row = re.search(r'<tr data-project="touch-grass">.*?</tr>', body).group(0)
        assert '<a href="/project-monitor/onchain/dossier/touch-grass?test_mode=1" title="touch-grass">Touch Grass</a>' in row
        assert '<td class="n" title="$173,738.69">$173.7k' in row
        assert '<td class="n" title="26.6329%">26.63%' in row
        assert '<td class="n" title="$0.003095">$0.003095' in row
        assert '<td class="n" title="12">12' in row
        assert 'first build</div>' in row  # no diff row stored: no delta invented
        assert '<span class="tag p" title="build 1 (run 1) 2026-09-10 00:26 UTC">ok</span>' in row
        failed = re.search(r'<tr data-project="zzz">.*?</tr>', body).group(0)
        assert '<span class="tag bad"' in failed and '>failed</span>' in failed
        assert failed.count('title="—">—') == 8
        quiet = re.search(r'<tr data-project="quiet">.*?</tr>', body).group(0)
        assert 'no build yet' in quiet and 'Quiet &lt;One&gt;' in quiet

    def test_the_grid_glyphs_follow_the_legend(self, client, seeded):
        body = _body(client)
        grid = body[body.index('id="coverage"'):]
        cell = lambda project, cls: re.search(  # noqa: E731
            rf'<td class="c" data-target="cov-{project}-{cls}" title="([^"]*)">(.*?)</td>', grid
        )
        admitted, candidate, none = (
            overview.glyph(overview.STATE_ADMITTED), overview.glyph(overview.STATE_CANDIDATE),
            overview.glyph(overview.STATE_NONE),
        )
        inherited = overview.INHERITED_MARK
        for cls in ('chain_rpc', 'chain_explorer', 'dex_provider'):
            assert cell('touch-grass', cls).group(2) == f'{admitted}1{inherited}', cls
            assert cell('quiet', cls).group(2) == f'{admitted}1{inherited}', cls
        assert cell('touch-grass', 'web').group(2) == f'{admitted}2'
        assert cell('touch-grass', 'x').group(2) == f'{admitted}1'
        assert cell('zzz', 'web').group(2) == f'{candidate}1'
        # A cell with no rows: the empty mark, no list to open.
        empty = re.search(r'<td class="c" title="telegram: [^"]*">(.*?)</td>', grid)
        assert empty.group(1) == none
        assert 'data-target="cov-touch-grass-telegram"' not in grid
        assert 'data-target="cov-zzz-x"' not in grid
        assert cell('touch-grass', 'web').group(1) == (
            'web: 2 admitted\nwww.touchgrass.family · admitted by registry\n'
            'notawebsite.fun/ · admitted by registry'
        )
        assert cell('zzz', 'web').group(1) == 'web: 1 candidate\nzzz.example/ · candidate · hop from dex_provider'
        assert 'inherited from the chain' in cell('zzz', 'chain_rpc').group(1)
        legend = re.search(r'<div class="legend">(.*?)</div>', grid).group(1)
        assert legend == (
            f'{admitted} admitted (n) · {candidate} candidate (n) · {none} none · '
            f'{overview.glyph(overview.STATE_OUT)} suspended/retired · {inherited} inherited from the chain · '
            'click a cell for its sources'
        )
        # The four states are four distinct drawings, none a bare Unicode
        # symbol the default sans stack might lack.
        assert len({admitted, candidate, none, overview.glyph(overview.STATE_OUT)}) == 4
        assert '<details id="cov-zzz-web"><summary>ZZZ · web (1)</summary><ul><li>' \
               '<a href="https://zzz.example/" target="_blank" rel="noopener noreferrer">zzz.example/</a>' \
               ' · candidate · hop from dex_provider</li></ul></details>' in grid

    def test_source_lists_are_folded_in_markup_and_hidden_only_by_script(self, client, seeded):
        """Without JS every rendered list is reachable as a folded `<details>`;
        the script hides them at load and reveals the clicked cell's one."""
        body = _body(client)
        assert ' hidden' not in body and ' open' not in body
        script = re.search(r'<script>(.*?)</script>', body, re.S).group(1)
        assert 'd.hidden=true' in script and 'o.hidden=o!==d' in script and 'd.open=true' in script

    def test_a_suspended_source_is_a_cross(self, client, seeded, onchain_repository):
        with onchain_repository.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE onchain.source SET admission = 'suspended', admitted_by = 'operator' "
                'WHERE url_or_handle = %s', ('https://x.com/TouchGrassRWA',)
            )
        onchain_repository.commit()
        grid = _body(client)
        assert (
            '<td class="c" data-target="cov-touch-grass-x" title="x: 1 suspended\n'
            f'x.com/TouchGrassRWA · suspended by operator">{overview.glyph(overview.STATE_OUT)}</td>'
        ) in grid

    def test_a_changed_rpc_host_retires_the_old_row_and_the_cell_stays_admitted(
        self, client, seeded, onchain_repository, onchain_registry_payload, monkeypatch
    ):
        monkeypatch.setenv('ROBINHOOD_CHAIN_RPC_URL', 'https://moved-rpc.example/v2/FAKEKEY')
        upsert_registry(onchain_repository, parse_registry(onchain_registry_payload))
        onchain_repository.commit()
        grid = _body(client)
        cell = re.search(
            r'<td class="c" data-target="cov-touch-grass-chain_rpc" title="([^"]*)">(.*?)</td>', grid
        )
        # One admitted row remains, so the cell is admitted (the cross is for
        # a class with NO admitted row left); the hover names both rows.
        assert cell.group(2) == f'{overview.glyph(overview.STATE_ADMITTED)}1{overview.INHERITED_MARK}'
        assert cell.group(1).startswith('chain_rpc: 1 admitted, 1 retired\n')
        rpc = re.search(r'<details id="cov-touch-grass-chain_rpc">.*?</details>', grid).group(0)
        assert '(2)</summary>' in rpc
        assert 'moved-rpc.example · admitted by registry · inherited from the chain' in rpc
        assert f'{FAKE_RPC_HOST} · retired by registry · inherited from the chain' in rpc

    def test_the_page_loads_nothing_from_outside_itself(self, client, seeded):
        """Local-first: no `<script src>` at all on this page, no loaded
        resource off this origin, and once every navigation anchor is
        stripped no URL of any kind remains -- so the source list's links are
        the only place a host appears, and the RPC row shows no URL."""
        body = _body(client)
        assert '<script src=' not in body
        assert not re.search(r'<(?:script|link|img|iframe)[^>]*(?:src|href)="(?:https?:)?//', body)
        assert '<link' not in body and 'fetch(' not in body and 'url(' not in body
        anchor = r'<a(?: class="[^"]*")? href="https?://[^"]*" target="_blank" rel="noopener noreferrer"[^>]*>'
        assert re.findall(anchor, body)
        assert 'http' not in re.sub(anchor, '', body)

    def test_the_rpc_cell_names_a_host_and_no_key(self, client, seeded):
        """The env is the conftest's fake keyed URL, so the expected host is
        exact and the key's characters are known: none may reach the page."""
        body = _body(client)
        rpc = re.search(r'<details id="cov-touch-grass-chain_rpc">.*?</details>', body).group(0)
        assert f'<li>{FAKE_RPC_HOST} · admitted by registry · inherited from the chain</li>' in rpc
        assert '?' not in rpc and 'key' not in rpc.lower() and '://' not in rpc
        assert 'FAKE' not in body and 'token=' not in body
        assert '<a ' not in rpc  # a bare host is text, never a link

    def test_json_format_is_the_loader_payload(self, client, seeded, onchain_repository):
        response = client.get('/project-monitor/onchain?format=json&test_mode=1')
        assert response.status_code == 200
        payload = json.loads(response.text)
        assert set(payload) == {'projects', 'coverage', 'classes'}
        assert payload['classes'] == list(SOURCE_BADGE_ORDER)
        assert [p['key'] for p in payload['projects']] == ['touch-grass', 'quiet', 'zzz']
        assert set(payload['projects'][0]) == {
            'key', 'display_name', 'chain', 'chain_key', 'build', 'flag_count', 'values', 'previous',
        }
        assert json.loads(json.dumps(load_overview(onchain_repository), default=str)) == payload

    def test_chain_supplied_text_is_escaped(self, client, seeded):
        body = _body(client)
        assert '<One>' not in body and 'Quiet &lt;One&gt;' in body

    def test_a_remote_client_is_refused(self, seeded, monkeypatch, onchain_database_url):
        monkeypatch.setattr(
            dashboard, 'get_onchain_database_url', lambda runtime_mode: onchain_database_url
        )
        remote = _loopback_client(build_app(), host='10.0.0.5', host_header=b'10.0.0.5')
        assert remote.get('/project-monitor/onchain').status_code == 403

    def test_the_dossier_page_links_back_to_the_overview(self, client, seeded):
        body = client.get('/project-monitor/onchain/dossier/touch-grass?test_mode=1').text
        assert '<nav><a href="/project-monitor/onchain/?test_mode=1">overview</a> ' in body
        assert page.OVERVIEW_PATH == '/project-monitor/onchain/'


class TestRenderWithoutAStore:
    def test_an_empty_overview_still_renders_both_panels(self):
        html = overview.render_overview_page({'projects': [], 'coverage': {}, 'classes': list(SOURCE_BADGE_ORDER)})
        assert 'no projects in the registry' in html
        assert 'id="projects"' in html and 'id="coverage"' in html
        assert html.endswith('</body></html>')

    def test_an_unsafe_project_key_is_never_an_href_or_an_id(self):
        html = overview.render_overview_page({
            'projects': [{'key': 'javascript:alert(1)', 'display_name': 'Bad', 'values': {}, 'previous': {}}],
            'coverage': {'javascript:alert(1)': {'web': [
                {'url_or_handle': 'https://bad.example', 'admission': 'admitted', 'inherited': False},
            ]}},
            'classes': ['web'],
        })
        assert 'href="javascript:' not in html and 'href="/project-monitor/onchain/dossier/javascript' not in html
        assert 'data-target="cov-0-web"' in html and 'id="cov-0-web"' in html

    def test_a_cell_mixing_a_chain_row_and_the_projects_own_row_is_not_marked_inherited(self):
        """The chain mark says EVERY counted row came through the chain; one
        project-level row of the same class removes it, and the count is of
        both."""
        html = overview.render_overview_page({
            'projects': [{'key': 'p', 'display_name': 'P', 'values': {}, 'previous': {}}],
            'coverage': {'p': {'chain_explorer': [
                {'url_or_handle': 'https://chain.example/api', 'admission': 'admitted', 'inherited': True},
                {'url_or_handle': 'https://own.example/api', 'admission': 'admitted', 'inherited': False},
            ]}},
            'classes': ['chain_explorer'],
        })
        cell = re.search(r'<td class="c" data-target="cov-p-chain_explorer" title="[^"]*">(.*?)</td>', html)
        assert cell.group(1) == f'{overview.glyph(overview.STATE_ADMITTED)}2'
        assert overview.INHERITED_MARK not in cell.group(1)
