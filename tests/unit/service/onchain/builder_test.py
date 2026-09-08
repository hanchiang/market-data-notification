"""The build's failure units (A3) and its diff wiring (A2), against real Postgres.

The collectors are replaced by fakes here on purpose: what is under test is the
BUILD -- which sections still run when one raises, what the build row records,
and whether a failed section's baseline survives -- and a real collector would
put four network dependencies between the test and that question. The collectors
have their own tests, and the real path is exercised by an actual build.
"""
import pytest

from src.service.onchain import builder
from src.service.onchain.collectors.base import (
    SECTION_CONTRACT_SAFETY,
    SECTION_IDENTITY,
    SECTION_ONCHAIN_HEALTH,
    SECTION_TOKEN_ECONOMICS,
    BuildContext,
    SectionResult,
)
from src.service.onchain.chain import PinnedBlock
from src.service.onchain.registry import parse_registry, upsert_registry

TOKEN = '0x' + '16' * 20

PINNED = PinnedBlock(
    block=1000, timestamp=1_700_000_000, window_start_block=100,
    window_start_timestamp=1_699_913_600, header_reads=5,
)


def _context(repository, registry, project_entity_id, chain_entity_id):
    registry_projects = list(registry.projects.values())
    return BuildContext(
        repository=repository, registry=registry,
        chain=registry.chains[4663], project=registry_projects[0],
        chain_entity_id=chain_entity_id, project_entity_id=project_entity_id,
        pinned=PINNED,
        # None because the fake collectors do not read: a fake that took a
        # client would only be testing the fake.
        state_client=None, log_client=None, dexscreener=None, explorer=None,
    )


def _ok(name, fields):
    async def collect(context, *args):
        return SectionResult(name=name, status='ok', fields=dict(fields))
    return collect


def _raises(name, exception):
    async def collect(context, *args):
        raise exception
    return collect


class _EvmRpcError(RuntimeError):
    """Stands in for the library's transport error, so the test does not depend
    on which exception a collector happens to raise."""


@pytest.fixture
def seeded(onchain_repository, onchain_registry_payload):
    registry = parse_registry(onchain_registry_payload)
    project_ids = upsert_registry(onchain_repository, registry)
    onchain_repository.commit()
    chain_entity = onchain_repository.get_entity_by_key('chain:4663')
    return registry, project_ids['touch-grass'], int(chain_entity['id'])


def _install(monkeypatch, **overrides):
    collectors = {
        SECTION_IDENTITY: _ok(SECTION_IDENTITY, {'token_address': TOKEN, 'version': 'v4'}),
        SECTION_CONTRACT_SAFETY: _ok(SECTION_CONTRACT_SAFETY, {'owner': 'absent'}),
        SECTION_ONCHAIN_HEALTH: _ok(SECTION_ONCHAIN_HEALTH, {'holder_count': 41}),
        SECTION_TOKEN_ECONOMICS: _ok(SECTION_TOKEN_ECONOMICS, {'total_supply': '1000'}),
    }
    collectors.update(overrides)
    monkeypatch.setattr(builder, 'COLLECTORS', collectors)


class TestHappyBuild:
    @pytest.mark.asyncio
    async def test_a_clean_build_writes_four_sections_and_an_ok_build_row(
        self, onchain_repository, seeded, monkeypatch
    ):
        registry, project_id, chain_id = seeded
        _install(monkeypatch)
        run_id = onchain_repository.start_run(builder.JOB_BUILD)
        context = _context(onchain_repository, registry, project_id, chain_id)

        result = await builder.build_project(context, run_id, project_entity_id=project_id)
        onchain_repository.commit()

        assert result.outcome == 'ok'
        assert [section['name'] for section in result.sections] == [
            SECTION_IDENTITY, SECTION_CONTRACT_SAFETY,
            SECTION_ONCHAIN_HEALTH, SECTION_TOKEN_ECONOMICS,
        ]
        build = onchain_repository.get_build(result.build_id)
        assert build['outcome'] == 'ok'
        assert build['failed_units_json'] == []
        assert build['threshold_version'] is not None

    @pytest.mark.asyncio
    async def test_two_identical_builds_yield_an_empty_diff(
        self, onchain_repository, seeded, monkeypatch
    ):
        """A2 over the store: a section whose inputs did not change reports an
        empty diff, and the first build reports every field as added."""
        registry, project_id, chain_id = seeded
        _install(monkeypatch)
        for _ in range(2):
            run_id = onchain_repository.start_run(builder.JOB_BUILD)
            context = _context(onchain_repository, registry, project_id, chain_id)
            result = await builder.build_project(context, run_id, project_entity_id=project_id)
            onchain_repository.commit()

        second = next(s for s in result.sections if s['name'] == SECTION_CONTRACT_SAFETY)
        assert second['changes'] == {'added': {}, 'removed': {}, 'changed': []}


class TestFailureUnits:
    @pytest.mark.asyncio
    async def test_one_failing_collector_costs_only_its_own_section(
        self, onchain_repository, seeded, monkeypatch
    ):
        """A3, the whole criterion in one test: the section reads `failed` with
        the error class, every other section is built, and the build row names
        the failed section."""
        registry, project_id, chain_id = seeded
        _install(monkeypatch, **{
            SECTION_ONCHAIN_HEALTH: _raises(SECTION_ONCHAIN_HEALTH, _EvmRpcError('boom')),
        })
        run_id = onchain_repository.start_run(builder.JOB_BUILD)
        context = _context(onchain_repository, registry, project_id, chain_id)

        result = await builder.build_project(context, run_id, project_entity_id=project_id)
        onchain_repository.commit()

        statuses = {section['name']: section['status'] for section in result.sections}
        assert statuses == {
            SECTION_IDENTITY: 'ok', SECTION_CONTRACT_SAFETY: 'ok',
            SECTION_ONCHAIN_HEALTH: 'failed', SECTION_TOKEN_ECONOMICS: 'ok',
        }
        failed = next(s for s in result.sections if s['status'] == 'failed')
        assert failed['error_class'] == '_EvmRpcError'
        # A12: the row an operator reaches from the alert has to carry the span
        # the collector's log lines carry, failed or not.
        stored = onchain_repository.get_sections_for_build(result.build_id)
        failed_row = next(row for row in stored if row['status'] == 'failed')
        assert failed_row['span_id'] is not None
        build = onchain_repository.get_build(result.build_id)
        assert build['outcome'] == 'partial'
        assert build['failed_units_json'] == [
            {'unit': 'touch-grass/onchain_health', 'error_class': '_EvmRpcError'}
        ]

    @pytest.mark.asyncio
    async def test_a_failed_section_keeps_its_previous_build_as_the_baseline(
        self, onchain_repository, seeded, monkeypatch
    ):
        """The other half of A3. The good build's section is still the latest
        `ok` one, so tomorrow's build diffs against a real value rather than
        against nothing."""
        registry, project_id, chain_id = seeded
        _install(monkeypatch)
        run_id = onchain_repository.start_run(builder.JOB_BUILD)
        context = _context(onchain_repository, registry, project_id, chain_id)
        await builder.build_project(context, run_id, project_entity_id=project_id)
        onchain_repository.commit()

        _install(monkeypatch, **{
            SECTION_ONCHAIN_HEALTH: _raises(SECTION_ONCHAIN_HEALTH, _EvmRpcError('boom')),
        })
        run_id = onchain_repository.start_run(builder.JOB_BUILD)
        context = _context(onchain_repository, registry, project_id, chain_id)
        await builder.build_project(context, run_id, project_entity_id=project_id)
        onchain_repository.commit()

        latest_ok = onchain_repository.get_latest_section(project_id, SECTION_ONCHAIN_HEALTH)
        assert latest_ok['status'] == 'ok'
        assert latest_ok['fields_json'] == {'holder_count': 41}

    @pytest.mark.asyncio
    async def test_identity_failing_does_not_skip_the_other_three(
        self, onchain_repository, seeded, monkeypatch
    ):
        """Design, The build step 4: the other three run from the latest `ok`
        identity section, whose chain-resolved addresses are immutable."""
        registry, project_id, chain_id = seeded
        _install(monkeypatch)
        run_id = onchain_repository.start_run(builder.JOB_BUILD)
        context = _context(onchain_repository, registry, project_id, chain_id)
        await builder.build_project(context, run_id, project_entity_id=project_id)
        onchain_repository.commit()

        _install(monkeypatch, **{
            SECTION_IDENTITY: _raises(SECTION_IDENTITY, _EvmRpcError('provider down')),
        })
        run_id = onchain_repository.start_run(builder.JOB_BUILD)
        context = _context(onchain_repository, registry, project_id, chain_id)
        result = await builder.build_project(context, run_id, project_entity_id=project_id)
        onchain_repository.commit()

        statuses = {section['name']: section['status'] for section in result.sections}
        assert statuses[SECTION_IDENTITY] == 'failed'
        assert statuses[SECTION_CONTRACT_SAFETY] == 'ok'
        assert statuses[SECTION_ONCHAIN_HEALTH] == 'ok'
        assert statuses[SECTION_TOKEN_ECONOMICS] == 'ok'

    @pytest.mark.asyncio
    async def test_with_no_identity_ever_the_other_three_are_identity_unresolved(
        self, onchain_repository, seeded, monkeypatch
    ):
        registry, project_id, chain_id = seeded
        _install(monkeypatch, **{
            SECTION_IDENTITY: _raises(SECTION_IDENTITY, _EvmRpcError('provider down')),
        })
        run_id = onchain_repository.start_run(builder.JOB_BUILD)
        context = _context(onchain_repository, registry, project_id, chain_id)
        result = await builder.build_project(context, run_id, project_entity_id=project_id)
        onchain_repository.commit()

        others = [s for s in result.sections if s['name'] != SECTION_IDENTITY]
        assert all(section['status'] == 'failed' for section in others)
        assert all(
            section['error_class'] == builder.IDENTITY_UNRESOLVED for section in others
        )
        build = onchain_repository.get_build(result.build_id)
        assert build['outcome'] == 'failed'

    @pytest.mark.asyncio
    async def test_a_failing_section_does_not_destroy_the_build_row(
        self, onchain_repository, seeded, monkeypatch
    ):
        """Regression. The failure path used to roll the whole transaction back,
        which deleted the build row and every section already written under it;
        the next section's insert then failed with a foreign-key violation that
        looked like a store bug rather than the collector failure it was."""
        registry, project_id, chain_id = seeded
        _install(monkeypatch, **{
            SECTION_CONTRACT_SAFETY: _raises(SECTION_CONTRACT_SAFETY, _EvmRpcError('boom')),
        })
        run_id = onchain_repository.start_run(builder.JOB_BUILD)
        context = _context(onchain_repository, registry, project_id, chain_id)
        result = await builder.build_project(context, run_id, project_entity_id=project_id)
        onchain_repository.commit()

        assert onchain_repository.get_build(result.build_id) is not None
        assert len(onchain_repository.get_sections_for_build(result.build_id)) == 4


class TestProjectSelection:
    def test_adding_a_project_is_a_registry_edit_and_not_a_code_change(
        self, onchain_registry_payload
    ):
        """A1's mechanism. The build selects projects from the parsed registry,
        so a fifth object in `projects` is built by the same code path with no
        branch anywhere that names a project."""
        payload = onchain_registry_payload
        payload['projects'] = list(payload['projects']) + [
            {
                'key': 'fifth-project', 'display_name': 'Fifth', 'chain_id': 4663,
                'archetype': 'launchpad-fixed-supply',
                'pool_ref': '0x' + 'ab' * 20, 'sources': [],
            }
        ]
        registry = parse_registry(payload)
        keys = [project.key for project in builder.select_projects(registry, None)]
        assert keys == ['touch-grass', 'fifth-project']
        assert [p.key for p in builder.select_projects(registry, 'fifth-project')] == [
            'fifth-project'
        ]

    def test_an_unknown_project_says_what_to_edit(self, onchain_registry_payload):
        registry = parse_registry(onchain_registry_payload)
        with pytest.raises(KeyError, match='projects.json'):
            builder.select_projects(registry, 'nope')
