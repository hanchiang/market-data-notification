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
        from src.service.onchain.report import load_dossier, render_dossier

        expected = render_dossier(load_dossier(onchain_repository, 'touch-grass'))
        response = client.get('/project-monitor/onchain/dossier/touch-grass?test_mode=1')
        assert response.status_code == 200
        assert 'GRASS' in response.text
        # The renderer's own output, escaped, is inside the page.
        assert expected.splitlines()[0] in response.text

    def test_json_format_returns_the_loader_payload(self, client, seeded):
        response = client.get(
            '/project-monitor/onchain/dossier/touch-grass?format=json&test_mode=1'
        )
        payload = json.loads(response.text)
        assert payload['project'] == 'touch-grass'
        assert payload['sections'][0]['name'] == 'identity'

    def test_the_page_references_nothing_outside_itself(self, client, seeded):
        """Local-first: opening this page must send nothing anywhere. The
        cheapest guarantee is a page with no external reference at all."""
        body = client.get('/project-monitor/onchain/dossier/touch-grass?test_mode=1').text
        assert 'http://' not in body and 'https://' not in body
        assert '<script' not in body

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

    def test_an_unknown_project_is_a_404(self, client, seeded):
        assert client.get(
            '/project-monitor/onchain/dossier/nope?test_mode=1'
        ).status_code == 404
