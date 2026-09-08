"""The dossier as text and as JSON, from stored rows only (design D6).

No chain access and no provider call: every figure comes out of the store, which
is what makes the report runnable on a `pg_dump` restored somewhere else, and
what makes the dashboard page and `report --json` incapable of disagreeing --
they call these same functions.

The reading order is the design's: **flagged changes first**, then the rest of
the diff, then the current fields. A dossier the operator reads before acting is
a diff document with a state appendix, not a state document with a diff footnote.
"""
import json
import logging
from typing import Any, Dict, List, Optional

from src.service.onchain import diff as diff_module
from src.service.onchain.collectors.base import SECTION_ORDER
from src.service.onchain.repository import OnchainRepository

logger = logging.getLogger('Onchain report')

UNAVAILABLE = 'unavailable'


def load_dossier(
    repository: OnchainRepository, project_key: str, *, build_id: Optional[int] = None
) -> Dict[str, Any]:
    """One project's latest build (or a named older one) with its diffs."""
    entity = repository.get_entity_by_key(f'project:{project_key}')
    if entity is None:
        raise UnknownProjectError(f'no project entity for {project_key!r}')

    build = (
        repository.get_build(build_id)
        if build_id is not None
        else repository.get_latest_build(int(entity['id']))
    )
    if build is None:
        return {
            'project': project_key,
            'display_name': entity.get('display_name'),
            'build': None,
            'sections': [],
        }

    sections = []
    for row in repository.get_sections_for_build(int(build['id'])):
        section_diff = repository.get_section_diff(int(row['id'])) or {}
        sections.append(
            {
                'name': row['name'],
                'status': row['status'],
                'error_class': row['error_class'],
                'span_id': row['span_id'],
                'evidence_ids': list(row['evidence_ids'] or []),
                'fields': row['fields_json'] or {},
                'changes': section_diff.get('changes_json') or {},
                'flagged': section_diff.get('flagged_json') or [],
                'previous_section_id': section_diff.get('previous_section_id'),
            }
        )
    sections.sort(key=lambda section: _section_order(section['name']))
    return {
        'project': project_key,
        'display_name': entity.get('display_name'),
        'archetype': (entity.get('attrs_json') or {}).get('archetype'),
        'build': {
            'id': int(build['id']),
            'run_id': int(build['run_id']),
            'block': build['block'],
            'block_timestamp': build['block_timestamp'],
            'outcome': build['outcome'],
            'threshold_version': build['threshold_version'],
            'failed_units': build['failed_units_json'] or [],
            'started_at': _isoformat(build['started_at']),
            'finished_at': _isoformat(build['finished_at']),
        },
        'sections': sections,
    }


class UnknownProjectError(KeyError):
    """No project entity by that key. Distinct from "no build yet"."""


def _section_order(name: str) -> int:
    return SECTION_ORDER.index(name) if name in SECTION_ORDER else len(SECTION_ORDER)


def _isoformat(value: Any) -> Optional[str]:
    return None if value is None else value.isoformat()


def load_all(repository: OnchainRepository) -> List[Dict[str, Any]]:
    return [
        load_dossier(repository, str(row['key']).split(':', 1)[1])
        for row in repository.get_projects()
    ]


def render_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, default=str)


def render_dossier(dossier: Dict[str, Any]) -> str:
    lines: List[str] = []
    build = dossier.get('build')
    header = f"{dossier.get('display_name') or dossier['project']} ({dossier['project']})"
    lines.append(header)
    lines.append('=' * len(header))
    if build is None:
        lines.append('no build yet')
        return '\n'.join(lines)
    lines.append(
        f"build {build['id']} · run {build['run_id']} · block {build['block']} · "
        f"{build['outcome']} · thresholds {build['threshold_version']}"
    )
    if build['failed_units']:
        lines.append('failed units: ' + ', '.join(
            f"{unit.get('unit')} ({unit.get('error_class')})"
            for unit in build['failed_units']
        ))
    for section in dossier['sections']:
        lines.append('')
        lines.extend(_render_section(section))
    return '\n'.join(lines)


def _render_section(section: Dict[str, Any]) -> List[str]:
    title = f"-- {section['name']} [{section['status']}]"
    if section['error_class']:
        title += f" ({section['error_class']})"
    lines = [title]

    for flag in section['flagged']:
        lines.append(f"  ! {flag.get('field')}: {flag.get('reason')}")

    lines.extend(_render_changes(section))

    fields = section['fields'] or {}
    # A4 is a RENDERING rule as much as a collection one: "no gameable metric
    # appears without its paired counterpart on the same row". A field that is
    # already a metric inside `pairs` must therefore not also get a bare line of
    # its own, where it would read as an unguarded figure. Enforced here rather
    # than only in the collector so a future collector storing a paired metric at
    # the top level cannot quietly reintroduce the unguarded row.
    paired = {
        str(pair.get('metric'))
        for pair in (fields.get('pairs') or [])
        if isinstance(pair, dict)
    }
    for name, value in sorted(fields.items()):
        if name in paired:
            continue
        if diff_module.is_failed(value):
            lines.append(
                f"  {name}: FAILED ({value.get('error_class')}), "
                f"baseline {_short(value.get('baseline'))}"
            )
        elif name == 'pairs':
            lines.extend(_render_pairs(value))
        else:
            lines.append(f'  {name}: {_short(value)}')
    return lines


def _render_changes(section: Dict[str, Any]) -> List[str]:
    """The diff, flagged entries first.

    Split out of `_render_section` so the section renderer stays one shape --
    title, flags, diff, fields -- and this holds the ordering rule on its own.
    """
    changes = section['changes'] or {}
    if not changes:
        return ['  (no diff: section failed, or first build)']
    if diff_module.is_empty(changes):
        return ['  no change']

    flagged_fields = {flag.get('field') for flag in section['flagged']}
    lines: List[str] = []
    # Flagged first, then the rest -- the ordering the operator reads by.
    entries = sorted(
        changes.get('changed') or [],
        key=lambda entry: (entry.get('field') not in flagged_fields, entry.get('field')),
    )
    for entry in entries:
        delta = f" (delta {entry['delta']})" if 'delta' in entry else ''
        lines.append(
            f"  ~ {entry['field']}: {_short(entry.get('old'))} -> "
            f"{_short(entry.get('new'))}{delta}"
        )
    for name, value in (changes.get('added') or {}).items():
        lines.append(f'  + {name}: {_short(value)}')
    for name, value in (changes.get('removed') or {}).items():
        lines.append(f'  - {name}: {_short(value)}')
    return lines


def _render_pairs(pairs: Any) -> List[str]:
    """Each pair on one row, both members and the gaming mode together.

    Rendered as one line per pair rather than a field per member, because A4 is
    about what the operator SEES: a metric and its counterpart on the same row.
    """
    if not isinstance(pairs, list):
        return [f'  pairs: {_short(pairs)}']
    lines = ['  pairs (metric | counterpart | guards against):']
    for pair in pairs:
        pool_type = pair.get('pool_type')
        suffix = f" [pool type {pool_type}]" if pool_type else ''
        lines.append(
            f"    {pair.get('metric')}={_short(pair.get('value'))} | "
            f"{pair.get('counterpart')}={_short(pair.get('counterpart_value'))} | "
            f"{pair.get('guards_against')}{suffix}"
        )
    return lines


def _short(value: Any, limit: int = 160) -> str:
    text = json.dumps(value, sort_keys=True, default=str) if isinstance(value, (dict, list)) else str(value)
    return text if len(text) <= limit else text[: limit - 1] + '…'


def render_runs(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The ledger route's payload (P14): what each run did and what it cost."""
    return {
        'runs': [
            {
                'id': int(run['id']),
                'job': run['job'],
                'started_at': _isoformat(run['started_at']),
                'finished_at': _isoformat(run['finished_at']),
                'outcome': run['outcome'],
                'failed_units': run['failed_units_json'] or [],
                'spend': run['spend_json'] or {},
                'notes': run['notes'],
            }
            for run in runs
        ]
    }
