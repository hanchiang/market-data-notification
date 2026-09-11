"""One nightly build: run setup, four sections per project, diff, close (P4).

Split from `src/job/onchain/build.py` deliberately. The entrypoint owns argument
parsing, client lifetimes and the alert; this owns what a build IS, so the build
can be driven from a test, from the fixture-capture script or from a future
scheduler without any of them growing a copy of the sequence.

The order below is the design's, and each step is placed where it is for a
reason the next reader should not have to rediscover:

1. **The lock, then the run row, then the registry upsert.** The lock is taken
   before anything is written so two builds cannot interleave their entity
   upserts, and the run row exists before the first read so a crash in setup is
   still a run in the ledger rather than a silence.
2. **One pinned block for the whole run.** Sections of different projects are
   only comparable at one height.
3. **Sections in order, each in its own span, each its own failure unit.** A
   collector that raises after the client's retries marks its section `failed`
   with the error CLASS and the build continues (A3).
4. **Identity failing does not skip the other three.** They run from the latest
   `ok` or `partial` identity section, whose chain-resolved addresses are
   immutable. With no prior identity section at all they are recorded `failed`
   with `identity_unresolved`, because there is genuinely nothing to read.
5. **The diff last, against the latest previous section of the same name,
   whatever its age.** A section that has not been built for a week still diffs
   against the last time it was.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.service.onchain import chain as chain_module
from src.service.onchain import diff as diff_module
from src.service.onchain import thresholds
from src.service.onchain.collectors import (
    contract_safety,
    health,
    identity,
    token_economics,
)
from src.service.onchain.collectors.base import (
    SECTION_CONTRACT_SAFETY,
    SECTION_IDENTITY,
    SECTION_ONCHAIN_HEALTH,
    SECTION_ORDER,
    SECTION_TOKEN_ECONOMICS,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_PARTIAL,
    BuildContext,
    SectionResult,
)
from src.service.onchain.observability import collector_span, failed_unit
from src.service.onchain.registry import ProjectEntry, Registry
from src.service.onchain.repository import OnchainRepository

logger = logging.getLogger('Onchain builder')

JOB_BUILD = 'onchain.build'
JOB_WATCH = 'onchain.watch'

IDENTITY_UNRESOLVED = 'identity_unresolved'

COLLECTORS: Dict[str, Callable[..., Any]] = {
    SECTION_IDENTITY: identity.collect,
    SECTION_CONTRACT_SAFETY: contract_safety.collect,
    SECTION_ONCHAIN_HEALTH: health.collect,
    SECTION_TOKEN_ECONOMICS: token_economics.collect,
}


@dataclass
class ProjectBuildResult:
    project_key: str
    build_id: Optional[int]
    outcome: str
    sections: List[Dict[str, Any]] = field(default_factory=list)
    failed_units: List[Dict[str, str]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


@dataclass
class RunResult:
    run_id: int
    outcome: str
    builds: List[ProjectBuildResult] = field(default_factory=list)
    failed_units: List[Dict[str, str]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


async def build_project(
    context: BuildContext, run_id: int, *, project_entity_id: int
) -> ProjectBuildResult:
    """One project's build row, its four sections and their diffs."""
    repository = context.repository
    build_id = repository.start_build(
        run_id=run_id,
        project_id=project_entity_id,
        block=context.pinned.block,
        block_timestamp=context.pinned.timestamp,
    )
    # Committed before the first collector runs: a section row and an evidence
    # row both reference it, so it has to be durable before anything a savepoint
    # might roll back is written on top of it.
    repository.commit()
    result = ProjectBuildResult(
        project_key=context.project.key, build_id=build_id, outcome=STATUS_OK
    )

    previous_identity = repository.get_latest_section(project_entity_id, SECTION_IDENTITY)
    if previous_identity is not None and previous_identity['status'] != STATUS_FAILED:
        # Seeded BEFORE the identity collector runs, so a collector that fails
        # this build still leaves the other three with the addresses they need.
        context.identity.update(
            diff_module.effective_fields(previous_identity['fields_json'] or {}, None)
        )

    for name in SECTION_ORDER:
        section = await _run_section(context, name, project_entity_id, previous_identity)
        stored = _store_section(context, build_id, project_entity_id, section)
        # Committed per section, so a build that dies on section three keeps the
        # two it finished. The advisory lock is session-scoped and survives a
        # commit, so this does not release the single-writer guarantee.
        repository.commit()
        result.sections.append(stored)
        if section.status == STATUS_FAILED:
            result.failed_units.append(
                failed_unit(
                    f'{context.project.key}/{name}', section.error_class or 'unknown'
                )
            )
        elif section.status == STATUS_PARTIAL:
            result.failed_units.extend(_field_units(context.project.key, name, section.fields))

    outcome = _build_outcome(result.sections)
    result.outcome = outcome
    thresholds.record_version(repository)
    repository.finish_build(
        build_id,
        outcome=outcome,
        failed_units=result.failed_units,
        threshold_version=thresholds.THRESHOLD_VERSION,
    )
    return result


async def _run_section(
    context: BuildContext,
    name: str,
    project_entity_id: int,
    previous_identity: Optional[Dict[str, Any]],
) -> SectionResult:
    """One collector under one span, with its failures converted to a status.

    The broad `except Exception` is the failure unit itself: A3 says a collector
    that raises after the client's retries makes its section `failed` with the
    error class and the build continues, so narrowing this would turn an
    unanticipated exception into a lost build rather than a named section.
    """
    with collector_span(context.project.key) as span_id:
        # F2 residual (test round 1): a collector that succeeds leaves its span
        # id on the section row with nothing to grep for, because the only
        # other line the section can produce is the FAILURE line below. A12's
        # join needs at least one log line per span whichever way the section
        # ends.
        logger.info('section %s started', name)
        # Cleared per section so `evidence_ids` on a section row are that
        # section's own, which is what A12's "the rows this collector wrote"
        # means.
        context.evidence_ids = []
        if name != SECTION_IDENTITY and not context.identity.get('token_address'):
            logger.error(
                'section %s cannot run: no identity has ever resolved for %s',
                name,
                context.project.key,
            )
            unresolved = SectionResult(
                name=name, status=STATUS_FAILED, error_class=IDENTITY_UNRESOLVED
            )
            unresolved.span_id = span_id
            return unresolved
        # Everything written before this collector is made durable FIRST, so the
        # rollback below can only undo this collector's own half-written
        # evidence. Rolling back without it destroyed the build row and every
        # section already written under it, and the next section's insert then
        # failed with a foreign-key violation that looked like a store bug
        # rather than the collector failure it actually was.
        #
        # A commit and not a savepoint, because a collector is allowed to commit
        # inside itself -- the transfer fetch commits per window so that an
        # interrupted first build resumes from the last window rather than from
        # the token's creation block -- and a commit inside a savepoint raises.
        context.repository.commit()
        try:
            if name == SECTION_IDENTITY:
                section = await COLLECTORS[name](
                    context,
                    (previous_identity or {}).get('fields_json') if previous_identity else None,
                )
            else:
                section = await COLLECTORS[name](context)
        except Exception as exc:
            context.repository.rollback()
            error_class = type(exc).__name__
            logger.error('section %s failed: %s', name, error_class, exc_info=True)
            failure = SectionResult(
                name=name,
                status=STATUS_FAILED,
                error_class=error_class,
                evidence_ids=[],
            )
            # The span is stamped on the FAILED row too. A12's whole promise is
            # that an alert leads to log lines and log lines lead to rows; a
            # failed section with no span id is exactly the row an operator
            # would be trying to reach from the alert that named it.
            failure.span_id = span_id
            return failure
        section.evidence_ids = list(context.evidence_ids)
        section.span_id = span_id
        if name == SECTION_IDENTITY:
            # The builder publishes identity to the other three, not the
            # collector: the same three fields have to be there whether identity
            # was built this run or carried forward from the last one, and one
            # writer is what makes those two paths the same path.
            context.identity.update(section.fields)
        return section


def _store_section(
    context: BuildContext,
    build_id: int,
    project_entity_id: int,
    section: SectionResult,
) -> Dict[str, Any]:
    """Write the section row, then its diff against the latest previous one.

    `with_baselines` runs before the write, not after: a `partial` section stores
    failure markers, and a marker that did not carry the last good value would
    make the next build read the outage as a change (diff module, rule 1).
    """
    repository = context.repository
    previous = repository.get_latest_section(project_entity_id, section.name)
    previous_fields = (previous or {}).get('fields_json') or {}
    stored_fields = diff_module.with_baselines(section.fields, previous_fields)

    section_id = repository.insert_section(
        build_id=build_id,
        name=section.name,
        status=section.status,
        fields=stored_fields,
        span_id=section.span_id,
        error_class=section.error_class,
        evidence_ids=section.evidence_ids,
    )

    changes: Dict[str, Any] = {}
    flagged: List[Dict[str, Any]] = []
    if section.status != STATUS_FAILED:
        changes = diff_module.diff_fields(previous_fields, stored_fields)
        flagged = thresholds.flagged_changes(section.name, changes)
        repository.insert_section_diff(
            section_id=section_id,
            previous_section_id=(previous or {}).get('id'),
            changes=changes,
            flagged=flagged,
        )
    return {
        'id': section_id,
        'project': context.project.key,
        'name': section.name,
        'status': section.status,
        'error_class': section.error_class,
        'fields_json': stored_fields,
        'changes': changes,
        'flagged': flagged,
    }


def _field_units(project_key: str, name: str, fields: Dict[str, Any]) -> List[Dict[str, str]]:
    units = []
    for field_name, value in (fields or {}).items():
        if diff_module.is_failed(value):
            units.append(
                failed_unit(
                    f'{project_key}/{name}/{field_name}',
                    str(value.get('error_class') or 'unknown'),
                )
            )
    return units


def _build_outcome(sections: Sequence[Dict[str, Any]]) -> str:
    statuses = {section['status'] for section in sections}
    if STATUS_FAILED in statuses or STATUS_PARTIAL in statuses:
        return STATUS_FAILED if statuses == {STATUS_FAILED} else STATUS_PARTIAL
    return STATUS_OK


def run_outcome(builds: Sequence[ProjectBuildResult]) -> str:
    outcomes = {build.outcome for build in builds}
    if not outcomes or outcomes == {STATUS_OK}:
        return STATUS_OK
    if outcomes == {STATUS_FAILED}:
        return STATUS_FAILED
    return STATUS_PARTIAL


def select_projects(registry: Registry, project_key: Optional[str]) -> List[ProjectEntry]:
    if project_key is None:
        return list(registry.projects.values())
    if project_key not in registry.projects:
        raise KeyError(
            f'{project_key!r} is not in the registry; adding a project is one '
            f'object in projects.json, not a code change'
        )
    return [registry.projects[project_key]]


# Which class a published link belongs to, by host. The registry admits an X
# handle as class `x`, so a provider link to the same account stored as `web`
# is a second row for one source -- the duplicate this table exists to stop.
# Only hosts whose class is one the registry validator already knows
# (`config.SOURCE_CLASSES`). A Discord invite has no class in phase 1a, so it
# stays `web` rather than inventing a value the registry would reject.
SOURCE_CLASS_BY_HOST = {
    'x.com': 'x',
    'www.x.com': 'x',
    'twitter.com': 'x',
    'www.twitter.com': 'x',
    't.me': 'telegram',
    'telegram.me': 'telegram',
}


def source_class_for(url: str) -> str:
    """The source class a published link belongs to, `web` when the host is not
    one this phase knows."""
    from urllib.parse import urlsplit

    from src.service.onchain.config import SOURCE_CLASSES

    found = SOURCE_CLASS_BY_HOST.get(urlsplit(str(url)).netloc.lower(), 'web')
    return found if found in SOURCE_CLASSES else 'web'


def normalise_source_url(url: str) -> str:
    """The comparison key for "is this the same source".

    Lowercased, with a trailing slash removed: the registry holds
    `https://www.touchgrass.family` and the provider publishes
    `https://www.touchgrass.family/`, which are one website and were stored as
    two rows. Nothing more is normalised here -- `www.` and the scheme are NOT
    stripped, because two hosts that differ in either can be genuinely different
    sites, and phase 1b's admission run reads these rows to decide what to fetch.
    """
    text = str(url).strip().lower()
    return text[:-1] if text.endswith('/') and not text.endswith('//') else text


def store_candidate_sources(
    repository: OnchainRepository, project_entity_id: int, links: Sequence[str]
) -> int:
    """The provider's published links, stored as `candidate` and read by nothing.

    Phase 1a's whole participation in source admission (P5, P6): the structural
    hop is recorded so phase 1b's admission run has something to admit, and no
    scheduled capture touches them until the operator's queue does.

    A link the project already has as a source is SKIPPED, whatever class it
    carries, compared on `normalise_source_url` rather than on the raw string.
    Upsert is idempotent per `(class, url)`, so without this the same
    `https://x.com/...` the registry admitted as class `x` is stored again as a
    `candidate` of class `web`, and phase 1b's admission run sees two rows for
    one source -- one already admitted, one asking to be.
    """
    known = {
        normalise_source_url(row['url_or_handle'])
        for row in repository.get_sources_for_entity(project_entity_id)
    }
    stored = 0
    for url in links:
        key = normalise_source_url(url)
        if key in known:
            continue
        source_id = repository.upsert_source(
            source_class=source_class_for(url),
            url_or_handle=url,
            admission='candidate',
            admitted_by=None,
            evidence={'hop_from': 'dex_provider', 'phase': '1a'},
        )
        repository.link_source_to_entity(source_id, project_entity_id)
        known.add(key)
        stored += 1
    return stored


__all__ = [
    'IDENTITY_UNRESOLVED',
    'JOB_BUILD',
    'JOB_WATCH',
    'ProjectBuildResult',
    'RunResult',
    'build_project',
    'run_outcome',
    'select_projects',
    'store_candidate_sources',
    'chain_module',
]
