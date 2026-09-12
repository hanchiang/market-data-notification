"""The dossier as text and as JSON, from stored rows only (design D6).

No chain access and no provider call: every figure comes out of the store, which
is what makes the report runnable on a `pg_dump` restored somewhere else, and
what makes the dashboard page and `report --json` incapable of disagreeing --
they call these same functions.

The reading order is the design's: **flagged changes first**, then the rest of
the diff, then the current fields. A dossier the operator reads before acting is
a diff document with a state appendix, not a state document with a diff footnote.
"""
import copy
import json
import logging
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any, Dict, List, Optional, Set, Tuple

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
    chain = repository.get_entity(int(entity['parent_id'])) if entity.get('parent_id') else None
    # The header's source badges: one row per source reaching this project,
    # directly or through its chain (the explorer hangs off the chain entity).
    sources = [
        {'class': row['class'], 'admission': row['admission'], 'scope': 'project'}
        for row in repository.get_sources_for_entity(int(entity['id']))
    ] + [
        {'class': row['class'], 'admission': row['admission'], 'scope': 'chain'}
        for row in (repository.get_sources_for_entity(int(chain['id'])) if chain else [])
    ]

    if build_id is not None:
        # Scoped to the project: an unscoped id would render another project's
        # sections under this project's name.
        build = repository.get_build(build_id, project_id=int(entity['id']))
        if build is None:
            raise UnknownBuildError(f'no build {build_id} for {project_key!r}')
    else:
        build = repository.get_latest_build(int(entity['id']))
    if build is None:
        return {
            'project': project_key,
            'display_name': entity.get('display_name'),
            'archetype': (entity.get('attrs_json') or {}).get('archetype'),
            'chain': chain.get('display_name') if chain else None,
            'sources': sources,
            'builds': [],
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
    # The last few builds, so a reader can step back through diffs (the page
    # links them; `--build N` on the CLI takes the same ids).
    recent = [
        {
            'id': int(row['id']), 'run_id': int(row['run_id']), 'block': row['block'],
            'outcome': row['outcome'], 'block_timestamp': row['block_timestamp'],
        }
        for row in repository.get_builds_for_project(int(entity['id']), limit=10)
    ]
    return {
        'project': project_key,
        'display_name': entity.get('display_name'),
        'archetype': (entity.get('attrs_json') or {}).get('archetype'),
        'chain': chain.get('display_name') if chain else None,
        'sources': sources,
        'builds': recent,
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


class UnknownBuildError(KeyError):
    """A named build id that is not this project's (or does not exist)."""


def project_key(entity_key: Any) -> str:
    """`project:touch-grass` -> `touch-grass`; a key without the prefix is
    returned whole rather than raising. One parser for the CLI and the page."""
    text = str(entity_key)
    return text.partition(':')[2] or text


def _section_order(name: str) -> int:
    return SECTION_ORDER.index(name) if name in SECTION_ORDER else len(SECTION_ORDER)


def _isoformat(value: Any) -> Optional[str]:
    return None if value is None else value.isoformat()


def load_all(repository: OnchainRepository) -> List[Dict[str, Any]]:
    return [
        load_dossier(repository, project_key(row['key']))
        for row in repository.get_projects()
    ]


def render_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, default=str)


# The KPI series (UX brief, slice C): one value per build, read from the same
# stored sections the dossier shows. Metric name -> (section, path), where a
# path into `pairs` names the row's metric.
HISTORY_METRICS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    'liquidity_usd': ('onchain_health', ('pairs', 'liquidity_usd')),
    'volume_h24_usd': ('onchain_health', ('pairs', 'dex_volume_h24_usd')),
    'trades_h24': ('onchain_health', ('pairs', 'dex_trades_h24')),
    'holders': ('onchain_health', ('pairs', 'holder_count')),
    'top_ten_share': ('token_economics', ('top_ten_share',)),
    'pool_count': ('identity', ('pool_count',)),
    'price_usd': ('onchain_health', ('price_usd',)),
    'fdv_usd': ('onchain_health', ('fdv_usd',)),
}


def metric_values(sections: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """The eight KPI figures out of one build's sections; a metric whose
    section is not `ok`, or whose field failed, is None -- a gap, never a
    carried or interpolated number."""
    by_name = {str(section.get('name')): section for section in sections}
    values: Dict[str, Optional[float]] = {}
    for metric, (section_name, path) in HISTORY_METRICS.items():
        section = by_name.get(section_name)
        value: Any = None
        if section is not None and section.get('status') == 'ok':
            fields = section.get('fields') or {}
            if path[0] == 'pairs':
                value = next(
                    (pair.get('value') for pair in fields.get('pairs') or []
                     if isinstance(pair, dict) and pair.get('metric') == path[1]),
                    None,
                )
            else:
                value = fields.get(path[0])
        if diff_module.is_failed(value) or isinstance(value, bool):
            value = None
        values[metric] = value if isinstance(value, (int, float)) else None
    return values


def load_history(
    repository: OnchainRepository, project_key: str, *, limit: int = 30
) -> Dict[str, Any]:
    """The last `limit` `ok` builds as one point each, oldest first.

    Only `ok` builds are points: a failed night contributes nothing rather than
    a zero. Within an `ok` build a section that failed still yields its metrics
    as null, so the series keeps its x-axis and the chart shows a gap.
    """
    entity = repository.get_entity_by_key(f'project:{project_key}')
    if entity is None:
        raise UnknownProjectError(f'no project entity for {project_key!r}')
    builds = repository.get_builds_for_project(
        int(entity['id']), limit=max(1, int(limit)), outcome='ok'
    )
    points = []
    for build in reversed(builds):
        sections = [
            {'name': row['name'], 'status': row['status'], 'fields': row['fields_json'] or {}}
            for row in repository.get_sections_for_build(int(build['id']))
        ]
        points.append({
            'build_id': int(build['id']),
            'block_timestamp': build['block_timestamp'],
            'values': metric_values(sections),
        })
    return {'project': project_key, 'metrics': list(HISTORY_METRICS), 'points': points}


# --- Formatting -------------------------------------------------------------
#
# The store holds chain-native values: token amounts in base units, shares as
# fractions, timestamps as epoch seconds or milliseconds. The 2026-09-10 read of
# the page found them unreadable at exactly the moment the operator needed them
# (a burn of `24003596905748870304250637`, a holder table cut off at 160
# characters). Formatting lives here, on top of the loader, so the JSON payload
# stays raw and re-derivable while the text and the page read as words.

# Base-unit token amounts, scaled by the token's `decimals`.
AMOUNT_FIELDS = frozenset({'burned', 'total_supply', 'balance', 'amount'})
# Fractions of one, printed as percentages.
SHARE_FIELDS = frozenset({
    'burned_share', 'top_ten_share', 'share', 'largest_owner_share',
    'primary_pool_share_of_provider_liquidity',
})
# US dollars, as the provider quotes them. `price_usd` needs its significant
# digits (a launchpad token trades at $0.003), so it is not the plain float rule.
MONEY_FIELDS = frozenset({'price_usd', 'fdv_usd'})
# Epoch seconds; `pair_created_at` is the provider's milliseconds.
SECOND_FIELDS = frozenset({'from_timestamp', 'to_timestamp', 'block_timestamp'})
MILLISECOND_FIELDS = frozenset({'pair_created_at'})
# Integers that are coordinates, not counts: a thousands separator misreads them.
RAW_INT_FIELDS = frozenset({'tick', 'tick_lower', 'tick_upper', 'sqrt_price_x96', 'bits'})
# Fields whose items carry an identity, so a change inside the list can be
# reported per item rather than as two truncated blobs.
LIST_KEYS = {'pools': 'reference', 'top_holders': 'address', 'pairs': 'metric'}
# Per-build bookkeeping that moves every night by construction (the fetch
# window, the cursor walk, the row count). Shown in the section, but never
# what the diff leads with and never on its own a reason to open a section:
# on 2026-09-12 these three buried the one real change under twelve lines.
# Keyed by section, as SCALED_SECTIONS is: `window` is a generic name, and a
# future section's `window` may be the finding.
BOOKKEEPING_FIELDS = {'onchain_health': frozenset({'transfer_fetch', 'transfer_rows', 'window'})}


class Formatting:
    """What a dossier needs to print its own numbers: the token's decimals and
    symbol, read from its identity section (falling back to token economics)."""

    # Only this section's amounts are the project token's. The name sets match
    # bare field names, so without this gate a future `amount` in another
    # section would be scaled by these decimals and stamped with this symbol.
    SCALED_SECTIONS = frozenset({'token_economics'})

    def __init__(self, dossier: Dict[str, Any], *, section: Optional[str] = None) -> None:
        self.section = section
        fields: Dict[str, Any] = {}
        for candidate in dossier.get('sections') or []:
            if candidate.get('name') in ('identity', 'token_economics'):
                fields = {**(candidate.get('fields') or {}), **fields}
        decimals = fields.get('decimals')
        self.decimals = decimals if isinstance(decimals, int) else None
        symbol = fields.get('token_symbol')
        self.symbol = str(symbol) if isinstance(symbol, str) and symbol else None

    def for_section(self, section: str) -> 'Formatting':
        bound = copy.copy(self)
        bound.section = section
        return bound

    def amount(self, value: Any) -> str:
        """`24,003,597 GRASS` from base units; the raw integer when the token's
        decimals are unknown, because a wrong scale reads as a wrong number."""
        try:
            raw = int(str(value))
        except (TypeError, ValueError):
            return str(value)
        if self.decimals is None:
            return f'{raw:,} (base units)'
        # Precision sized to the value: under the default 28-digit context
        # `scaleb` silently rounds and `quantize` raises above 1e24 units, and a
        # uint256-max sentinel supply (2**256 - 1) is a real field on troll tokens.
        with localcontext() as context:
            context.prec = len(str(abs(raw))) + 8
            units = Decimal(raw).scaleb(-self.decimals)
            if abs(units) < 1 and units != 0:
                # Below one unit: significant digits, so dust is not rounded to 0.
                text = f'{float(units):.6g}'
            else:
                # Four decimals then trailing zeros trimmed, quantized on the
                # whole value so 1000.99999 carries to 1,001, not `1,000.`.
                # (A move smaller than one unit must still not print as two
                # identical sides of a change; below 0.00005 it is dust.)
                quantized = units.quantize(Decimal('0.0001'), rounding=ROUND_HALF_EVEN)
                text = f'{quantized:,f}'
                if '.' in text:
                    text = text.rstrip('0').rstrip('.')
        return f'{text} {self.symbol}' if self.symbol else text

    def scalar(self, name: str, value: Any) -> str:
        if value is None or isinstance(value, bool):
            return str(value)
        if name in AMOUNT_FIELDS and self.section in self.SCALED_SECTIONS:
            return self.amount(value)
        if name in SHARE_FIELDS and isinstance(value, (int, float)):
            return f'{value * 100:.2f}%'
        if name in MONEY_FIELDS and isinstance(value, (int, float)):
            return money_exact(value)
        if name in SECOND_FIELDS and isinstance(value, (int, float)):
            return _utc(value)
        if name in MILLISECOND_FIELDS and isinstance(value, (int, float)):
            return _utc(value / 1000)
        if isinstance(value, float):
            return f'{value:,.2f}' if abs(value) >= 1000 else f'{value:g}'
        if isinstance(value, int):
            if name in RAW_INT_FIELDS or abs(value) < 10_000:
                return str(value)
            return f'{value:,}'
        return str(value)

    def inline(self, name: str, value: Any) -> str:
        """One value on one line: a dict as `k=v` pairs, a list as its items."""
        if isinstance(value, dict):
            if not value:
                return '{}'  # an empty object is not a missing one
            return ', '.join(f'{k}={self.inline(k, v)}' for k, v in sorted(value.items()))
        if isinstance(value, list):
            return '[' + ', '.join(self.inline(name, item) for item in value) + ']'
        return self.scalar(name, value)


    def amount_brief(self, value: Any) -> str:
        """`24.80M GRASS`: the tile and cell form of `amount`; the exact form
        goes in the title attribute."""
        try:
            raw = int(str(value))
        except (TypeError, ValueError):
            return str(value)
        if self.decimals is None:
            return f'{abbrev_count(raw)} (base units)'
        with localcontext() as context:
            context.prec = len(str(abs(raw))) + 8
            units = float(Decimal(raw).scaleb(-self.decimals))
        text = _abbreviate(units, (('T', 1e12), ('B', 1e9), ('M', 1e6), ('k', 1e3)), 2)
        return f'{text} {self.symbol}' if self.symbol else text


# -- Brief forms: tiles and table cells (UX brief, Number formatting) ---------
#
# Abbreviated magnitudes right-aligned are what every product in the research
# pass shares, and their absence was the first reason the page read badly. Each
# brief form is paired on the page with the exact form in a `title` attribute;
# nothing here replaces the exact forms above, which the CLI and the raw diff
# keep printing.

DASH = '—'


def money_exact(value: float) -> str:
    value = float(value)
    if value == 0:
        return '$0'
    return f'${value:,.2f}' if abs(value) >= 1 else f'${value:.4g}'


def abbrev_money(value: Any) -> str:
    """`$173.7k`, `$1.2m`; under a dollar the significant digits, which is
    where a launchpad token's price lives."""
    if value is None:
        return DASH
    value = float(value)
    if abs(value) >= 1000:
        return '$' + _abbreviate(value, (('b', 1e9), ('m', 1e6), ('k', 1e3)), 1)
    return money_exact(value)


def abbrev_count(value: Any) -> str:
    if value is None:
        return DASH
    value = float(value)
    if abs(value) < 1e6:
        return f'{int(value):,}'
    return _abbreviate(value, (('T', 1e12), ('B', 1e9), ('M', 1e6)), 2)


def abbrev_pct(value: Any) -> str:
    return DASH if value is None else f'{float(value) * 100:.2f}%'


def short_address(text: Any) -> str:
    """`0x8366…0951`; anything that is not a hex identifier (a metric name)
    is returned whole, since it is the name and not a copy target."""
    text = str(text)
    if not text.startswith('0x') or len(text) < 14:
        return text
    return f'{text[:6]}…{text[-4:]}'


def brief(name: str, value: Any) -> str:
    """The brief form chosen by the field's name, the way `scalar` chooses the
    exact one: a share as a percentage, a dollar figure abbreviated, a count
    with separators."""
    if value is None:
        return DASH
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return str(value)
    if name in SHARE_FIELDS or 'share' in name:
        return abbrev_pct(value)
    if name in MONEY_FIELDS or name.endswith('_usd'):
        return abbrev_money(value)
    return abbrev_count(value)


def _abbreviate(value: float, units: Tuple[Tuple[str, float], ...], places: int) -> str:
    for unit, divisor in units:
        if abs(value) >= divisor:
            return f'{value / divisor:,.{places}f}{unit}'
    return f'{value:,.{places}f}'


def delta_kind(name: str) -> str:
    return 'share' if 'share' in name else ('money' if name.endswith('_usd') else 'count')


def delta_text(old: Any, new: Any, kind: str) -> Tuple[str, str]:
    """(text, direction) for a tile or a What-moved cell.

    Computed on the stored values and rounded once, so 0.268672 -> 0.266329
    reads as 26.87% -> 26.63%, delta -0.23 pp -- never the difference of the
    two rounded strings. A share moves in percentage POINTS, never in percent
    of itself; a dollar figure moves in percent of its previous value; a count
    moves by its difference. Direction is `up`, `down` or `flat` for the CSS.
    """
    if new is None:
        return (DASH, 'flat')
    if old is None:
        return ('first build', 'flat')
    old, new = float(old), float(new)
    if old == new:
        return ('=', 'flat')
    direction = 'up' if new > old else 'down'
    if kind == 'share':
        return (f'{(new - old) * 100:+.2f} pp', direction)
    if kind == 'count':
        return (f'{int(round(new - old)):+,}', direction)
    if old:
        return (f'{(new - old) / old * 100:+.1f}%', direction)
    return (f'{new - old:+,.0f}', direction)


def _utc(seconds: float) -> str:
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    except (OverflowError, OSError, ValueError):
        return str(seconds)


# --- Rendering --------------------------------------------------------------


class SectionBlock:
    """One section rendered as three line groups, so the text report joins them
    and the page can lead with the diff and fold the state."""

    def __init__(self, section: Dict[str, Any], formatting: Formatting) -> None:
        self.name = str(section['name'])
        formatting = formatting.for_section(self.name)
        self.status = str(section['status'])
        self.error_class = section.get('error_class')
        self.title = f"-- {self.name} [{self.status}]"
        if self.error_class:
            self.title += f" ({self.error_class})"
        self.flag_lines = [
            f"  ! {flag.get('field')}: {flag.get('reason')}" for flag in section['flagged']
        ]
        changes = section['changes'] or {}
        self.changed = bool(changes) and not diff_module.is_empty(_substantive(changes, self.name))
        self.change_lines = _render_changes(section, formatting)
        self.field_lines = _render_fields(section, formatting)

    @property
    def has_changes(self) -> bool:
        """What the page opens and leads with. A failed or partial section
        counts: a section that could not be built is what changed since the
        previous build, even with no field diff to show."""
        return bool(self.flag_lines) or self.changed or self.status != 'ok'

    def lines(self) -> List[str]:
        return [self.title, *self.flag_lines, *self.change_lines, *self.field_lines]


def header_lines(dossier: Dict[str, Any], formatting: Formatting) -> List[str]:
    build = dossier['build']
    when = build.get('block_timestamp')
    stamp = f" · {formatting.scalar('block_timestamp', when)}" if when else ''
    lines = [
        f"build {build['id']} · run {build['run_id']} · block {build['block']}{stamp} · "
        f"{build['outcome']} · thresholds {build['threshold_version']}"
    ]
    if build['failed_units']:
        lines.append('failed units: ' + ', '.join(
            f"{unit.get('unit')} ({unit.get('error_class')})"
            for unit in build['failed_units']
        ))
    return lines


def render_blocks(dossier: Dict[str, Any]) -> List[SectionBlock]:
    formatting = Formatting(dossier)
    return [SectionBlock(section, formatting) for section in dossier['sections']]


def render_dossier(dossier: Dict[str, Any]) -> str:
    lines: List[str] = []
    build = dossier.get('build')
    header = f"{dossier.get('display_name') or dossier['project']} ({dossier['project']})"
    lines.append(header)
    lines.append('=' * len(header))
    if build is None:
        lines.append('no build yet')
        return '\n'.join(lines)
    formatting = Formatting(dossier)
    lines.extend(header_lines(dossier, formatting))
    for block in render_blocks(dossier):
        lines.append('')
        lines.extend(block.lines())
    return '\n'.join(lines)


def _render_fields(section: Dict[str, Any], formatting: Formatting) -> List[str]:
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
    lines: List[str] = []
    for name, value in sorted(fields.items()):
        if name in paired:
            continue
        if diff_module.is_failed(value):
            lines.append(
                f"  {name}: FAILED ({value.get('error_class')}), "
                f"baseline {formatting.inline(name, value.get('baseline'))}"
            )
        elif name == 'pairs':
            lines.extend(_render_pairs(value, formatting))
        else:
            lines.extend(_render_value(name, value, formatting, indent='  '))
    return lines


def _render_value(name: str, value: Any, formatting: Formatting, *, indent: str) -> List[str]:
    """A field on as many lines as it needs, never truncated: a list of records
    one record per line, a dict one key per line, everything else inline."""
    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        lines = [f'{indent}{name} ({len(value)}):']
        key = LIST_KEYS.get(name)
        for item in value:
            rest = {k: v for k, v in item.items() if k != key}
            label = f"{item.get(key)}  " if key else ''
            lines.append(f'{indent}  - {label}{formatting.inline(name, rest)}')
        return lines
    if isinstance(value, dict) and value:
        lines = [f'{indent}{name}:']
        for key, inner in sorted(value.items()):
            lines.append(f'{indent}  {key}: {formatting.inline(key, inner)}')
        return lines
    return [f'{indent}{name}: {formatting.inline(name, value)}']


def _render_changes(section: Dict[str, Any], formatting: Formatting) -> List[str]:
    """The diff, flagged entries first.

    Split out so the section renderer stays one shape -- title, flags, diff,
    fields -- and this holds the ordering rule on its own. A section whose
    previous build failed diffs as every field added; that is a baseline being
    laid, not a change, and is said in one line rather than repeating the
    fields that follow anyway.
    """
    changes = section['changes'] or {}
    if not changes:
        return ['  (no diff: section failed, or first build)']
    if diff_module.is_empty(changes):
        return [] if section['flagged'] else ['  no change']
    added = changes.get('added') or {}
    if added and not changes.get('changed') and not changes.get('removed'):
        fields = section['fields'] or {}
        present = {name for name in fields if not diff_module.is_failed(fields[name])}
        if present and set(added) >= present:
            return [f'  first successful build of this section: {len(added)} fields, no baseline']

    flagged_fields = {flag.get('field') for flag in section['flagged']}
    lines: List[str] = []
    substantive = _substantive(changes, str(section['name']))
    # A flagged field prints above these lines; `no change` under it would
    # contradict the flag.
    if diff_module.is_empty(substantive) and not flagged_fields:
        lines.append('  no change beyond bookkeeping' if _bookkeeping_names(changes, str(section['name'])) else '  no change')
    # Flagged first, then the rest -- the ordering the operator reads by.
    entries = sorted(
        substantive.get('changed') or [],
        key=lambda entry: (entry.get('field') not in flagged_fields, entry.get('field')),
    )
    for entry in entries:
        lines.extend(_render_change(entry, formatting))
    for name, value in (substantive.get('added') or {}).items():
        lines.append(f'  + {name}: {formatting.inline(name, value)}')
    for name, value in (substantive.get('removed') or {}).items():
        lines.append(f'  - {name}: {formatting.inline(name, value)}')
    moved = sorted(_bookkeeping_names(changes, str(section['name'])))
    if moved:
        lines.append(f"  (bookkeeping moved: {', '.join(moved)})")
    return lines


def _bookkeeping_names(changes: Dict[str, Any], section: str) -> Set[str]:
    names = {str(entry.get('field')) for entry in changes.get('changed') or []}
    names |= set(changes.get('added') or {}) | set(changes.get('removed') or {})
    return names & BOOKKEEPING_FIELDS.get(section, frozenset())


def _substantive(changes: Dict[str, Any], section: str) -> Dict[str, Any]:
    """The diff without its bookkeeping fields. Presentation only: `render_json`
    and the store keep the full diff."""
    bookkeeping = BOOKKEEPING_FIELDS.get(section, frozenset())
    return {
        'changed': [e for e in changes.get('changed') or [] if e.get('field') not in bookkeeping],
        'added': {k: v for k, v in (changes.get('added') or {}).items() if k not in bookkeeping},
        'removed': {k: v for k, v in (changes.get('removed') or {}).items() if k not in bookkeeping},
    }


def _render_change(entry: Dict[str, Any], formatting: Formatting) -> List[str]:
    """One changed field. Nested values are diffed by key (dicts) or by item
    identity (lists of records with a known key) so the line names what moved
    inside them, instead of printing both whole values."""
    name = str(entry['field'])
    old, new = entry.get('old'), entry.get('new')
    if isinstance(old, dict) and isinstance(new, dict):
        inner = _dict_delta(old, new)
        if inner:
            return [f'  ~ {name}:'] + [
                f'      {key}: {formatting.inline(key, before)} -> {formatting.inline(key, after)}'
                for key, before, after in inner
            ]
    key = LIST_KEYS.get(name)
    if key and _is_record_list(old) and _is_record_list(new):
        return [f'  ~ {name}:'] + _list_delta(old, new, key, formatting)
    delta = ''
    if 'delta' in entry:
        shown = formatting.scalar(name, entry['delta'])
        # A share moves by percentage POINTS; `%` would read as a relative move.
        delta = f" (delta {shown[:-1]} pp)" if name in SHARE_FIELDS and shown.endswith('%') else f" (delta {shown})"
    return [f'  ~ {name}: {formatting.inline(name, old)} -> {formatting.inline(name, new)}{delta}']


def _dict_delta(old: Dict[str, Any], new: Dict[str, Any]) -> List[tuple]:
    return [
        (key, old.get(key), new.get(key))
        for key in sorted(set(old) | set(new))
        if old.get(key) != new.get(key)
    ]


def _is_record_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def _list_delta(old: List[Dict[str, Any]], new: List[Dict[str, Any]], key: str, formatting: Formatting) -> List[str]:
    # Matched case-insensitively (addresses arrive checksummed from one source
    # and lower-cased from another), printed as the new side spells them, and
    # compared WITHOUT the key so a case-only respelling is not a change.
    before = {str(item.get(key)).lower(): item for item in old}
    after = {str(item.get(key)).lower(): item for item in new}
    lines: List[str] = []
    for ident in sorted(set(before) | set(after)):
        if ident not in before:
            item = after[ident]
            lines.append(f"      + {item.get(key)}  {_record_inline(item, key, formatting)}")
        elif ident not in after:
            item = before[ident]
            lines.append(f"      - {item.get(key)}  {_record_inline(item, key, formatting)}")
        else:
            shown = after[ident].get(key)
            for inner_key, was, now in _dict_delta(
                _without(before[ident], key), _without(after[ident], key)
            ):
                # One level deeper: a pair's counterpart can be a record
                # (custody: owner, share, positions), and two whole records
                # side by side hide the one number that moved. `_dict_delta`
                # reads absent and None alike, so two unequal records can yield
                # no leaf: then the whole value prints, never nothing.
                leaves = _dict_delta(was, now) if isinstance(was, dict) and isinstance(now, dict) else []
                if leaves:
                    for leaf, before_leaf, after_leaf in leaves:
                        lines.append(
                            f"      {shown}  {inner_key}.{leaf}: "
                            f"{formatting.inline(leaf, before_leaf)} -> {formatting.inline(leaf, after_leaf)}"
                        )
                    continue
                # A pair row's `value` is the metric named on the row, and its
                # `counterpart_value` the counterpart: format them under those
                # names, as the state block does, so one share is not printed
                # as 0.5738 in the diff and 57.38% two lines below.
                lines.append(
                    f"      {shown}  {inner_key}: "
                    f"{formatting.inline(_value_label(before[ident], key, inner_key), was)} -> "
                    f"{formatting.inline(_value_label(after[ident], key, inner_key), now)}"
                )
    return lines or ['      (reordered only)']


def _record_inline(item: Dict[str, Any], key: str, formatting: Formatting) -> str:
    """A whole record on one line, each value formatted under the name the
    state block would use for it (a pair's `value` under its metric)."""
    return ', '.join(
        f'{k}={formatting.inline(_value_label(item, key, k), v)}'
        for k, v in sorted(_without(item, key).items())
    )


def _value_label(item: Dict[str, Any], key: str, inner_key: str) -> str:
    if key == 'metric' and inner_key == 'value':
        return str(item.get('metric'))
    if key == 'metric' and inner_key == 'counterpart_value':
        return str(item.get('counterpart'))
    return inner_key


def _without(item: Dict[str, Any], key: str) -> Dict[str, Any]:
    return {k: v for k, v in item.items() if k != key}


def _render_pairs(pairs: Any, formatting: Formatting) -> List[str]:
    """Each pair on one row, both members and the gaming mode together.

    Rendered as one line per pair rather than a field per member, because A4 is
    about what the operator SEES: a metric and its counterpart on the same row.
    """
    if not isinstance(pairs, list):
        return [f'  pairs: {formatting.inline("pairs", pairs)}']
    lines = ['  pairs (metric | counterpart | guards against):']
    for pair in pairs:
        pool_type = pair.get('pool_type')
        suffix = f" [pool type {pool_type}]" if pool_type else ''
        metric, counterpart = str(pair.get('metric')), str(pair.get('counterpart'))
        lines.append(
            f"    {metric}={formatting.inline(metric, pair.get('value'))} | "
            f"{counterpart}={formatting.inline(counterpart, pair.get('counterpart_value'))} | "
            f"{pair.get('guards_against')}{suffix}"
        )
    return lines


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
