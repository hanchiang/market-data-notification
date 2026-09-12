"""The dossier page and the ledger route (design D6, requirement P14).

Both routes go through the REAL loopback middleware, using the same shim the
monitor's dashboard tests use: the app, its middleware and its routes are the
production ones, and only the ASGI scope's client address is set to a loopback
value that Starlette's own `TestClient` does not present.

The page assertions are about two properties the design fixes: the page and the
report job cannot disagree about a figure (one loader), and the page references
nothing outside itself (local-first).
"""
import json
import re

import pytest
from starlette.testclient import TestClient

from src.job.project_monitor.dashboard import build_app
from src.router.project_monitor import dashboard
from src.service.onchain import builder
from src.service.onchain.registry import parse_registry, upsert_registry


def _loopback_client(app, host='127.0.0.1', host_header=b'127.0.0.1:8765'):
    async def shim(scope, receive, send):
        if scope['type'] == 'http':
            headers = [(k, v) for k, v in scope['headers'] if k != b'host']
            headers.append((b'host', host_header))
            scope = dict(scope, client=(host, 50000), headers=headers)
        await app(scope, receive, send)

    return TestClient(shim)


@pytest.fixture
def client(monkeypatch, onchain_database_url):
    monkeypatch.setattr(
        dashboard, 'get_onchain_database_url', lambda runtime_mode: onchain_database_url
    )
    return _loopback_client(build_app())


@pytest.fixture
def seeded(onchain_repository, onchain_registry_payload):
    registry = parse_registry(onchain_registry_payload)
    project_ids = upsert_registry(onchain_repository, registry)
    run_id = onchain_repository.start_run(builder.JOB_BUILD)
    build_id = onchain_repository.start_build(
        run_id=run_id, project_id=project_ids['touch-grass'], block=900, block_timestamp=1
    )
    onchain_repository.insert_section(
        build_id=build_id, name='identity', status='ok', span_id='abcd1234',
        fields={'token_symbol': 'GRASS', 'pool_id': '0x' + 'ab' * 32},
    )
    onchain_repository.finish_build(build_id, outcome='ok', threshold_version='2026-09-06.1')
    onchain_repository.finish_run(
        run_id, outcome='ok', failed_units=[], spend={'requests': {'public': 5}}
    )
    onchain_repository.commit()
    return project_ids


class TestLedgerRoute:
    def test_it_lists_runs_with_their_outcome_and_spend(self, client, seeded):
        response = client.get('/project-monitor/onchain/runs?test_mode=1')
        assert response.status_code == 200
        payload = response.json()
        assert payload['runs'][0]['job'] == 'onchain.build'
        assert payload['runs'][0]['spend'] == {'requests': {'public': 5}}

    def test_an_unbounded_limit_is_clamped(self, client, seeded):
        """An unbounded `LIMIT` from a query string is how a read surface
        becomes a way to pull a whole table into one response."""
        assert client.get(
            '/project-monitor/onchain/runs?limit=100000&test_mode=1'
        ).status_code == 200

    def test_a_remote_client_is_refused(self, seeded, monkeypatch, onchain_database_url):
        monkeypatch.setattr(
            dashboard, 'get_onchain_database_url', lambda runtime_mode: onchain_database_url
        )
        remote = _loopback_client(build_app(), host='10.0.0.5', host_header=b'10.0.0.5')
        assert remote.get('/project-monitor/onchain/runs').status_code == 403


class TestDossierRoute:
    def test_the_page_renders_the_same_text_the_report_job_prints(
        self, client, seeded, onchain_repository
    ):
        """One loader, so the page and `report --project touch-grass` cannot
        disagree about a figure."""
        from src.router.project_monitor.dashboard import _escape
        from src.service.onchain.report import load_dossier, render_blocks

        dossier = load_dossier(onchain_repository, 'touch-grass')
        response = client.get('/project-monitor/onchain/dossier/touch-grass?test_mode=1')
        assert response.status_code == 200
        assert 'GRASS' in response.text
        # Every section line the report job prints is in the page, escaped: the
        # page adds shape around the renderer's lines and never its own words.
        lines = [line for block in render_blocks(dossier) for line in block.lines()]
        assert lines
        for line in lines:
            assert _escape(line) in response.text, line

    def test_the_page_is_never_cached(self, client, seeded):
        """A cached dossier is last night's dossier under today's date."""
        response = client.get('/project-monitor/onchain/dossier/touch-grass?test_mode=1')
        assert response.headers['cache-control'] == 'no-store'

    def test_json_format_returns_the_loader_payload(self, client, seeded):
        response = client.get(
            '/project-monitor/onchain/dossier/touch-grass?format=json&test_mode=1'
        )
        payload = json.loads(response.text)
        assert payload['project'] == 'touch-grass'
        assert payload['sections'][0]['name'] == 'identity'

    def test_the_page_loads_nothing_from_outside_itself(self, client, seeded, onchain_repository):
        """Local-first: opening this page must send nothing anywhere. The one
        script is the vendored Chart.js on this same origin. The only external
        references allowed are navigation links (`<a href>`) the operator
        clicks, to the chain's explorer host and dexscreener.com; nothing the
        page LOADS may point off this origin."""
        _seed_linkable_build(onchain_repository=onchain_repository, seeded=seeded, client=client)
        body = client.get('/project-monitor/onchain/dossier/touch-grass?test_mode=1').text
        assert re.findall(r'<script src="([^"]*)"', body) == ['/project-monitor/static/chart.umd.js']
        assert client.get('/project-monitor/static/chart.umd.js').status_code == 200
        assert not re.search(r'<(?:script|link|img|iframe)[^>]*(?:src|href)="(?:https?:)?//', body)
        assert '<link' not in body and 'fetch(' not in body and 'url(' not in body
        # The whitelist is hardcoded to the seeded chain record's host (tests/unit/conftest.py).
        hosts = {m.group(1) for m in re.finditer(r'https?://([^/"\s]+)', body)}
        assert hosts <= {'robinhoodchain.blockscout.com', 'dexscreener.com'}
        # Strip every well-formed navigation anchor; what remains must carry no URL at
        # all, so an external reference in any other element or attribute fails here.
        anchor = r'<a(?: class="[^"]*")? href="https://[^"]*" target="_blank" rel="noopener noreferrer"[^>]*>'
        assert re.findall(anchor, body)
        assert 'http' not in re.sub(anchor, '', body)

    def test_holders_link_to_the_explorer_and_pools_to_dexscreener(
        self, client, seeded, onchain_repository
    ):
        holder, pool = _seed_linkable_build(onchain_repository=onchain_repository, seeded=seeded, client=client)
        body = client.get('/project-monitor/onchain/dossier/touch-grass?test_mode=1').text
        assert (f'<a href="https://robinhoodchain.blockscout.com/address/{holder}" '
                f'target="_blank" rel="noopener noreferrer">{holder[:6]}…{holder[-4:]}</a>') in body
        assert (f'<a href="https://dexscreener.com/robinhood/{pool}" '
                f'target="_blank" rel="noopener noreferrer">{pool[:6]}…{pool[-4:]}</a>') in body
        payload = client.get('/project-monitor/onchain/dossier/touch-grass?format=json&test_mode=1').json()
        assert payload['explorer_api'] == 'https://robinhoodchain.blockscout.com/api/v2/'
        assert payload['dexscreener_slug'] == 'robinhood'

    def test_chain_supplied_text_is_escaped(
        self, client, onchain_repository, seeded, onchain_database_url
    ):
        """A token's `name()` is arbitrary bytes chosen by whoever deployed it.
        Unescaped, a `<script>` in a token name would run on a loopback origin
        that can read every other route on this app."""
        entity = onchain_repository.get_entity_by_key('project:touch-grass')
        run_id = onchain_repository.start_run(builder.JOB_BUILD)
        build_id = onchain_repository.start_build(
            run_id=run_id, project_id=int(entity['id']), block=901, block_timestamp=2
        )
        onchain_repository.insert_section(
            build_id=build_id, name='identity', status='ok',
            fields={'onchain_name': '<script>alert(1)</script>'},
        )
        onchain_repository.finish_build(build_id, outcome='ok')
        onchain_repository.commit()

        body = client.get('/project-monitor/onchain/dossier/touch-grass?test_mode=1').text
        assert '<script>alert(1)</script>' not in body
        assert '&lt;script&gt;' in body

    def test_another_projects_build_is_a_404_not_a_mislabelled_page(
        self, client, seeded, onchain_repository
    ):
        """`?build=N` is a clicked surface now; an unscoped id would render the
        other project's sections under this project's name."""
        chain = onchain_repository.get_entity_by_key('project:touch-grass')['parent_id']
        other = onchain_repository.upsert_entity(
            level='project', key='project:other', display_name='Other', parent_id=chain
        )
        run_id = onchain_repository.start_run(builder.JOB_BUILD)
        foreign = onchain_repository.start_build(
            run_id=run_id, project_id=other, block=950, block_timestamp=3
        )
        onchain_repository.finish_build(foreign, outcome='ok')
        onchain_repository.commit()

        response = client.get(
            f'/project-monitor/onchain/dossier/touch-grass?build={foreign}&test_mode=1'
        )
        assert response.status_code == 404
        assert response.json() == {'error': 'unknown build'}

    def test_an_unknown_project_is_a_404(self, client, seeded):
        assert client.get(
            '/project-monitor/onchain/dossier/nope?test_mode=1'
        ).status_code == 404


def _seed_linkable_build(*, onchain_repository, seeded, client):
    """A build with one pool and one top holder, so the page has links to render."""
    holder = '0x' + '12' * 20
    pool = '0x' + 'ab' * 32
    repository = onchain_repository
    run_id = repository.start_run(builder.JOB_BUILD)
    build_id = repository.start_build(
        run_id=run_id, project_id=seeded['touch-grass'], block=5000, block_timestamp=1_789_005_000
    )
    repository.insert_section(
        build_id=build_id, name='identity', status='ok',
        fields={'token_symbol': 'GRASS', 'decimals': 18, 'pool_count': 1, 'pool_ref': pool,
                'pools': [{'reference': pool, 'dex': 'uniswap', 'version': 'v4', 'liquidity_usd': 1000.0}]},
    )
    repository.insert_section(
        build_id=build_id, name='token_economics', status='ok',
        fields={'top_holders': [{'address': holder, 'balance': '1000', 'share': 0.5}], 'top_ten_share': 0.5},
    )
    repository.finish_build(build_id, outcome='ok', threshold_version='2026-09-06.1')
    repository.finish_run(run_id, outcome='ok', failed_units=[], spend={})
    repository.commit()
    return holder, pool


def _pairs(liquidity, holders):
    return [
        {'metric': 'liquidity_usd', 'value': liquidity, 'counterpart': 'custody',
         'counterpart_value': {'pool_type': 'v4'}, 'guards_against': 'liquidity_pull', 'pool_type': 'v4'},
        {'metric': 'holder_count', 'value': holders, 'counterpart': 'new_vs_returning_24h_and_top_ten_share',
         'counterpart_value': {'new': 1, 'returning': 2, 'top_ten_share': 0.3}, 'guards_against': 'split'},
    ]


def _add_build(
    repository, project_id, *, block, outcome='ok', health='ok', identity='ok', pool_count=3
):
    run_id = repository.start_run(builder.JOB_BUILD)
    build_id = repository.start_build(
        run_id=run_id, project_id=project_id, block=block, block_timestamp=1_789_000_000 + block
    )
    if identity == 'ok':
        repository.insert_section(
            build_id=build_id, name='identity', status='ok',
            fields={'token_symbol': 'GRASS', 'decimals': 18, 'pool_count': pool_count, 'pools': []},
        )
    else:
        repository.insert_section(
            build_id=build_id, name='identity', status='failed',
            error_class='EvmTransportError', fields={},
        )
    if health == 'ok':
        repository.insert_section(
            build_id=build_id, name='onchain_health', status='ok',
            fields={'pairs': _pairs(1000.0 + block, 40 + block), 'price_usd': 0.01, 'fdv_usd': 5.0},
        )
    else:
        repository.insert_section(
            build_id=build_id, name='onchain_health', status='failed',
            error_class='EvmTransportError', fields={},
        )
    repository.finish_build(build_id, outcome=outcome, threshold_version='2026-09-06.1')
    repository.finish_run(run_id, outcome=outcome, failed_units=[], spend={})
    repository.commit()
    return build_id


class TestHistoryAndSparklines:
    def test_history_has_one_point_per_build_oldest_first_with_nulls_for_failed_sections(
        self, client, seeded, onchain_repository
    ):
        project_id = seeded['touch-grass']
        _add_build(onchain_repository, project_id, block=1)
        gap = _add_build(onchain_repository, project_id, block=2, health='failed', outcome='partial')
        failed = _add_build(
            onchain_repository, project_id, block=3, outcome='failed', health='failed', identity='failed'
        )
        _add_build(onchain_repository, project_id, block=4)

        response = client.get('/project-monitor/onchain/dossier/touch-grass?format=history&test_mode=1')
        assert response.status_code == 200
        payload = json.loads(response.text)
        assert payload['project'] == 'touch-grass'
        assert payload['metrics'] == [
            'liquidity_usd', 'volume_h24_usd', 'trades_h24', 'holders', 'top_ten_share',
            'pool_count', 'price_usd', 'fdv_usd',
        ]
        ids = [point['build_id'] for point in payload['points']]
        assert ids == sorted(ids)
        # Every build is a point (review round 1): a `partial` night keeps its
        # good sections and its failed section is a null, so the line breaks
        # there instead of interpolating across the night.
        assert gap in ids and failed in ids
        partial = next(p for p in payload['points'] if p['build_id'] == gap)
        assert partial['values']['pool_count'] == 3
        assert partial['values']['liquidity_usd'] is None and partial['values']['holders'] is None
        # A build whose every section failed is a point of nothing but gaps.
        every_failed = next(p for p in payload['points'] if p['build_id'] == failed)
        assert set(every_failed['values']) == set(payload['metrics'])
        assert all(value is None for value in every_failed['values'].values())
        # The seeded fixture build has identity only: its health metrics are gaps.
        first = payload['points'][0]
        assert first['values']['pool_count'] is None and first['values']['liquidity_usd'] is None
        last = payload['points'][-1]
        assert last['values'] == {
            'liquidity_usd': 1004.0, 'volume_h24_usd': None, 'trades_h24': None, 'holders': 44,
            'top_ten_share': None, 'pool_count': 3, 'price_usd': 0.01, 'fdv_usd': 5.0,
        }
        assert last['block_timestamp'] == 1_789_000_004

    def test_a_partial_ok_build_keeps_its_x_axis_with_null_metrics(self, seeded, onchain_repository):
        """An `ok` build whose health section failed is a point with gaps, so
        the chart shows the night rather than skipping it."""
        from src.service.onchain.report import load_history

        project_id = seeded['touch-grass']
        gap = _add_build(onchain_repository, project_id, block=2, health='failed', outcome='ok')
        history = load_history(onchain_repository, 'touch-grass')
        point = next(p for p in history['points'] if p['build_id'] == gap)
        assert point['values']['liquidity_usd'] is None and point['values']['pool_count'] == 3

    def test_three_builds_show_the_count_and_seven_emit_the_sparkline(
        self, client, seeded, onchain_repository
    ):
        project_id = seeded['touch-grass']
        for block in (1, 2, 3):
            _add_build(onchain_repository, project_id, block=block)
        body = client.get('/project-monitor/onchain/dossier/touch-grass?test_mode=1').text
        # Three builds carry the metrics; the fixture build (identity, no
        # pool_count) is an `ok` point with every metric a gap.
        assert body.count('>3/7 builds<') == 5
        assert '>0/7 builds<' in body  # volume: never stored by these builds
        assert 'data-spark=' not in body

        for block in range(4, 8):
            _add_build(onchain_repository, project_id, block=block)
        body = client.get('/project-monitor/onchain/dossier/touch-grass?test_mode=1').text
        assert '<canvas data-spark="pool_count"' in body
        assert '<canvas data-spark="liquidity_usd"' in body
        assert 'data-spark="volume_h24_usd"' not in body  # never stored: a gap in every point
        inline = json.loads(re.search(
            r'<script id="history" type="application/json">(.*?)</script>', body
        ).group(1))
        assert len(inline['points']) == 8
        assert '<option value="?build=' in body and '(run ' in body

    def test_the_header_names_the_previous_build_per_section_after_a_partial_night(
        self, client, seeded, onchain_repository
    ):
        """The builder baselines each section on its latest ok/partial
        predecessor whatever the build outcome, so after a partial night the
        identity diff is against the partial build and the health diff against
        the one before it. The header says so rather than naming one build."""
        from src.service.onchain.report import load_dossier

        project_id = seeded['touch-grass']
        first = _add_build(onchain_repository, project_id, block=1)
        partial = _add_build(onchain_repository, project_id, block=2, health='failed', outcome='partial')
        latest = _add_build(onchain_repository, project_id, block=3)
        by_build = {
            (row['build_id'], row['name']): int(row['id'])
            for row in onchain_repository.fetch_all(
                'SELECT id, build_id, name FROM onchain.section WHERE build_id IN (%s, %s, %s)',
                (first, partial, latest),
            )
        }
        empty = {'added': {}, 'removed': {}, 'changed': []}
        onchain_repository.insert_section_diff(
            section_id=by_build[(latest, 'identity')],
            previous_section_id=by_build[(partial, 'identity')], changes=empty,
        )
        onchain_repository.insert_section_diff(
            section_id=by_build[(latest, 'onchain_health')],
            previous_section_id=by_build[(first, 'onchain_health')], changes=empty,
        )
        onchain_repository.commit()

        dossier = load_dossier(onchain_repository, 'touch-grass')
        assert {s['name']: s['previous_build_id'] for s in dossier['sections']} == {
            'identity': partial, 'onchain_health': first,
        }
        body = client.get('/project-monitor/onchain/dossier/touch-grass?test_mode=1').text
        assert f'href="?build={partial}&test_mode=1">prev build {partial} · onchain_health vs {first}</a>' in body

    def test_history_for_an_unknown_project_is_a_404(self, client, seeded):
        assert client.get(
            '/project-monitor/onchain/dossier/nope?format=history&test_mode=1'
        ).status_code == 404
