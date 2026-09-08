"""The build's failure units (A3) and its diff wiring (A2), against real Postgres.

The collectors are replaced by fakes here on purpose: what is under test is the
BUILD -- which sections still run when one raises, what the build row records,
and whether a failed section's baseline survives -- and a real collector would
put four network dependencies between the test and that question. The collectors
have their own tests, and the real path is exercised by an actual build.
"""
from pathlib import Path

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

# A distinct job name from `builder.JOB_BUILD` so TestLogEvidenceJoin's log
# lines land in their own handler rather than being interleaved into whatever
# handler another test file's job name already installed this session.
JOB_BUILD_FOR_LOG_TEST = 'onchain.build.log_join_test'

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


class TestBaselineAcrossAnOutage:
    """The one contract the builder owns that no builder test covered.

    `with_baselines` is what makes a `partial` section's failure marker carry the
    last good value, so the next build reads a real change as a change and not as
    "appeared". Two reviewer rounds recorded that a builder which forgot to call
    it would reintroduce the round-1 defect and that only a builder-level test
    could catch it -- and every collector fake in this file returns plain scalar
    fields, on which `with_baselines` is the identity function. So a builder that
    passed `section.fields` straight to `insert_section` passed the whole file.

    These fakes return failure markers instead, which is the only input shape on
    which the call is observable.
    """

    @staticmethod
    def _partial(name, fields):
        async def collect(context, *args):
            return SectionResult(name=name, status='partial', fields=dict(fields))
        return collect

    @pytest.mark.asyncio
    async def test_a_failed_field_is_stored_carrying_its_last_good_value(
        self, onchain_repository, seeded, monkeypatch
    ):
        from src.service.onchain import diff as diff_module

        registry, project_id, chain_id = seeded

        _install(monkeypatch)
        run_id = onchain_repository.start_run(builder.JOB_BUILD)
        await builder.build_project(
            _context(onchain_repository, registry, project_id, chain_id),
            run_id, project_entity_id=project_id,
        )
        onchain_repository.commit()

        # Night two: the same section, one field now unreadable.
        _install(monkeypatch, **{SECTION_CONTRACT_SAFETY: self._partial(
            SECTION_CONTRACT_SAFETY,
            {'owner': diff_module.failed_field('BlockscoutApiError')},
        )})
        run_id = onchain_repository.start_run(builder.JOB_BUILD)
        result = await builder.build_project(
            _context(onchain_repository, registry, project_id, chain_id),
            run_id, project_entity_id=project_id,
        )
        onchain_repository.commit()

        stored = onchain_repository.get_latest_section(project_id, SECTION_CONTRACT_SAFETY)
        assert stored['status'] == 'partial'
        assert stored['fields_json']['owner']['baseline'] == 'absent'
        # And the build row names the field-level unit, not just the section.
        build = onchain_repository.get_build(result.build_id)
        assert {unit['unit'] for unit in build['failed_units_json']} == {
            'touch-grass/contract_safety/owner'
        }

    @pytest.mark.asyncio
    async def test_a_change_across_an_outage_is_a_change_and_not_an_appearance(
        self, onchain_repository, seeded, monkeypatch
    ):
        """The defect end to end, through the store: True on night one, the read
        failing on night two, False on night three. Without the carried baseline
        night three reports `owner` as ADDED and the frozen table flags it
        `structural_appeared` -- an outage manufacturing a structural event."""
        from src.service.onchain import diff as diff_module

        registry, project_id, chain_id = seeded
        nights = [
            _ok(SECTION_CONTRACT_SAFETY, {'owner': 'absent'}),
            self._partial(
                SECTION_CONTRACT_SAFETY,
                {'owner': diff_module.failed_field('BlockscoutApiError')},
            ),
            _ok(SECTION_CONTRACT_SAFETY, {'owner': '0xdeadbeef'}),
        ]
        for collector in nights:
            _install(monkeypatch, **{SECTION_CONTRACT_SAFETY: collector})
            run_id = onchain_repository.start_run(builder.JOB_BUILD)
            result = await builder.build_project(
                _context(onchain_repository, registry, project_id, chain_id),
                run_id, project_entity_id=project_id,
            )
            onchain_repository.commit()

        third = next(
            s for s in result.sections if s['name'] == SECTION_CONTRACT_SAFETY
        )
        assert third['changes']['added'] == {}
        assert third['changes']['changed'] == [
            {'field': 'owner', 'old': 'absent', 'new': '0xdeadbeef'}
        ]


class TestLogEvidenceJoin:
    """A12 end to end (test round 1, F2): the round-1 plan called this
    "untestable at reasonable cost" on the theory that joining the three legs
    needs a real build against four network dependencies. It does not -- the
    join is a property of `run_context`/`collector_span`/`store_response`/
    `_store_section`, none of which is collector-specific, and every one of
    them is already driven here by the fake collectors `_install` installs.

    Three assertions, matching A12's own wording: every log line written under
    this run carries its run id; every evidence row's span id appears on some
    log line from the same run (the join an operator's grep actually performs);
    and the failed section's span id -- the row with no evidence, since the
    collector raised before writing any -- is still on a log line, because
    `_run_section` stamps the span on the failure line itself.
    """

    @pytest.mark.asyncio
    async def test_the_alert_worthy_run_id_joins_the_log_to_the_evidence(
        self, onchain_repository, seeded, monkeypatch, tmp_path
    ):
        import json
        import logging

        from src.service.onchain import evidence as evidence_module
        from src.service.onchain.observability import configure_job_logging, run_context

        # Idempotent: if an earlier test in this session already installed the
        # job's handler (tests/unit/conftest.py's session-scoped log-dir
        # override), this returns THAT handler rather than one pointed at
        # `tmp_path` -- which is fine, since the file we read back is whichever
        # one `baseFilename` names.
        handler = configure_job_logging(JOB_BUILD_FOR_LOG_TEST, log_dir=str(tmp_path))
        log_path = handler.baseFilename

        async def collect_with_evidence(context, *args):
            logging.getLogger('test collector').info('token economics collected')
            evidence_module.store_response(
                context.repository, entity_id=context.project_entity_id,
                kind=evidence_module.KIND_JSONRPC, method_or_url='eth_call',
                body={'ok': True}, endpoint_kind='public',
            )
            return SectionResult(
                name=SECTION_TOKEN_ECONOMICS, status='ok', fields={'total_supply': '1'}
            )

        registry, project_id, chain_id = seeded
        _install(monkeypatch, **{
            SECTION_TOKEN_ECONOMICS: collect_with_evidence,
            SECTION_ONCHAIN_HEALTH: _raises(SECTION_ONCHAIN_HEALTH, _EvmRpcError('boom')),
        })
        run_id = onchain_repository.start_run(JOB_BUILD_FOR_LOG_TEST)
        context = _context(onchain_repository, registry, project_id, chain_id)
        with run_context(run_id, JOB_BUILD_FOR_LOG_TEST):
            result = await builder.build_project(context, run_id, project_entity_id=project_id)
        onchain_repository.commit()
        handler.flush()

        lines = [
            json.loads(line) for line in Path(log_path).read_text().splitlines() if line.strip()
        ]
        this_runs_lines = [line for line in lines if line['run_id'] == run_id]
        assert this_runs_lines, 'no log line at all was written under this run'
        # A12, round 2: filtering by run id and then asserting the filtered
        # lines carry it is trivially true of the filter and catches only a
        # TOTAL loss of run id. The real claim -- "every LINE OF THAT RUN
        # appears with its run id" -- is available for free here: this
        # handler's file is per-job (`configure_job_logging` keys on job
        # name) and `JOB_BUILD_FOR_LOG_TEST` is unique to this test, and this
        # assertion runs immediately after `handler.flush()`, before any
        # later test's log calls can reach the same file. So every line
        # in the file at this point belongs to THIS run, and a PARTIAL loss
        # (e.g. a context-var reset mid-span dropping run_id off some lines
        # but not all) is now caught too, not only the total-loss case the
        # filtered check already covered.
        assert all(line['run_id'] == run_id for line in lines)

        evidence_rows = onchain_repository.get_evidence_for_run(run_id)
        assert evidence_rows, 'the fake collector did not write the evidence row it claims to'
        evidence_spans = {row['span_id'] for row in evidence_rows}
        log_spans = {line['span_id'] for line in this_runs_lines if line['span_id']}
        assert evidence_spans, 'evidence rows carry no span id to join on'
        assert evidence_spans <= log_spans

        # `result.sections` (the dicts `_store_section` returns) do not carry
        # `span_id`; the stored row does -- read it back the way the operator's
        # own SQL query would.
        stored_sections = onchain_repository.get_sections_for_build(result.build_id)
        failed_section = next(s for s in stored_sections if s['status'] == 'failed')
        assert failed_section['span_id'] in log_spans


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

    def test_a_provider_link_already_admitted_is_not_stored_again_as_a_candidate(
        self, onchain_repository
    ):
        """Upsert is idempotent per (class, url), so the same handle admitted as
        class `x` would otherwise reappear as a `web` candidate and phase 1b's
        admission run would see two rows for one source."""
        project = onchain_repository.upsert_entity(
            level='project', key='project:touch-grass'
        )
        handle = 'https://x.com/TouchGrassRWA'
        admitted = onchain_repository.upsert_source(
            source_class='x', url_or_handle=handle, admission='admitted',
            admitted_by='registry', evidence={},
        )
        onchain_repository.link_source_to_entity(admitted, project)
        stored = builder.store_candidate_sources(
            onchain_repository, project, [handle, 'https://touchgrass.family/new']
        )
        onchain_repository.commit()
        assert stored == 1
        urls = [
            row['url_or_handle']
            for row in onchain_repository.get_sources_for_entity(project)
        ]
        assert sorted(urls) == sorted([handle, 'https://touchgrass.family/new'])

    def test_a_trailing_slash_is_the_same_source_and_a_social_link_gets_its_class(
        self, onchain_repository
    ):
        """The two variants `onchain_demo` actually held: the registry's
        `https://www.touchgrass.family` beside the provider's
        `https://www.touchgrass.family/`, and every social link stored as class
        `web` next to the same handle admitted as class `x`."""
        project = onchain_repository.upsert_entity(
            level='project', key='project:predict-fwa'
        )
        site = 'https://www.touchgrass.family'
        admitted = onchain_repository.upsert_source(
            source_class='web', url_or_handle=site, admission='admitted',
            admitted_by='registry', evidence={},
        )
        onchain_repository.link_source_to_entity(admitted, project)

        stored = builder.store_candidate_sources(
            onchain_repository, project, [site + '/', 'https://x.com/pfwafun',
                                          'https://t.me/pfwafun'],
        )
        onchain_repository.commit()
        assert stored == 2
        rows = {
            row['url_or_handle']: row['class']
            for row in onchain_repository.get_sources_for_entity(project)
        }
        assert site + '/' not in rows
        assert rows['https://x.com/pfwafun'] == 'x'
        assert rows['https://t.me/pfwafun'] == 'telegram'

    def test_an_unknown_project_says_what_to_edit(self, onchain_registry_payload):
        registry = parse_registry(onchain_registry_payload)
        with pytest.raises(KeyError, match='projects.json'):
            builder.select_projects(registry, 'nope')
