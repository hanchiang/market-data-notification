"""The market overview: the page the operator lands on (UX brief, slice B).

Two panels over `report.load_overview`, the same payload `?format=json`
returns, so the page and any other reader of the loader cannot disagree.
Panel 1 is one row per project with the eight KPIs and their deltas against
the build each was diffed with, in the dossier tiles' brief-on-hover-exact
form; panel 2 is the source-coverage grid, projects by source class, drawn
from the source rows the registry load and the build wrote. Every number is
formatted by `report.py`; this module only chooses shape.

Local-first like the dossier page: inline CSS, a few lines of inline JS to
unfold a cell's source list, no `<script src>` at all, and the only URLs on
the page are navigation anchors to the sources themselves.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from src.service.onchain import report
from src.service.onchain.page import (
    CSS,
    _glossary_block,
    _link,
    _tag,
    escape,
    is_link_safe,
)
from src.service.onchain.report import (
    DASH,
    GLOSSARY,
    SOURCE_BADGE_ORDER,
    Formatting,
    brief,
    delta_kind,
    delta_text,
)

DOSSIER_PATH = '/project-monitor/onchain/dossier/'

# (column label, metric name in `report.HISTORY_METRICS`), the dossier tiles'
# order with the labels shortened to a column width.
COLUMNS: Tuple[Tuple[str, str], ...] = (
    ('liquidity', 'liquidity_usd'),
    ('volume 24h', 'volume_h24_usd'),
    ('trades 24h', 'trades_h24'),
    ('holders', 'holders'),
    ('top-10', 'top_ten_share'),
    ('pools', 'pool_count'),
    ('price', 'price_usd'),
    ('FDV', 'fdv_usd'),
)

# The cell states, in the order `_cell` resolves them. Each is drawn as a tiny
# CSS shape rather than a Unicode glyph: `◐` (half circle) falls back to a
# symbol font Chrome renders as a triangle on the operator's machine, and a
# page whose legend depends on font coverage is not local-first. The `×` for
# suspended/retired is U+00D7, in every Latin font.
STATE_ADMITTED, STATE_CANDIDATE, STATE_NONE, STATE_OUT = 'admitted', 'candidate', 'none', 'out'
INHERITED_MARK = '<sup>c</sup>'
OUT_ADMISSIONS = frozenset({'suspended', 'retired'})


def glyph(state: str) -> str:
    """The HTML for one cell state. Tests compare against this by state name,
    so the drawing can change without the semantics moving."""
    if state == STATE_OUT:
        return '<i class="g g-out">×</i>'
    return f'<i class="g g-{state}"></i>'


LEGEND = (
    f'{glyph(STATE_ADMITTED)} admitted (n) · {glyph(STATE_CANDIDATE)} candidate (n) · '
    f'{glyph(STATE_NONE)} none · {glyph(STATE_OUT)} suspended/retired · '
    f'{INHERITED_MARK} inherited from the chain · click a cell for its sources'
)

CSS_EXTRA = (
    '.cov td.c{text-align:center;cursor:pointer;white-space:nowrap}'
    '.cov td.c sup,.legend sup{font-size:11px;color:var(--mute)}.cov td.c:hover{background:var(--line)}'
    '.g{display:inline-block;width:11px;height:11px;border-radius:50%;box-sizing:border-box;'
    'vertical-align:-1px;margin-right:2px;font-style:normal}'
    '.g-admitted{background:var(--fg)}'
    '.g-candidate{border:1.5px solid var(--fg);background:linear-gradient(90deg,var(--fg) 50%,transparent 50%)}'
    '.g-none{border:1.5px solid var(--mute)}'
    '.g-out{width:auto;height:auto;border-radius:0;color:var(--mute);font-size:16px;line-height:1}'
    '.srcs{margin-top:10px}.srcs details{margin:2px 0}.srcs ul{margin:4px 0 8px 18px;padding:0}'
    '.srcs li{color:var(--mute)}.srcs li a{color:var(--acc)}'
    'td .d{font-size:12px}'
)

# With JS: every source list starts hidden, and clicking a cell shows and
# opens that cell's list alone. Without JS the lists are all on the page,
# folded, under the grid -- `hidden` is set by script, never in the markup,
# so a no-script reader still reaches them.
JS = (
    "var L=document.querySelectorAll('.srcs details');L.forEach(function(d){d.hidden=true});"
    "document.querySelectorAll('td[data-target]').forEach(function(c){c.addEventListener("
    "'click',function(){var d=document.getElementById(c.dataset.target);if(!d)return;"
    "L.forEach(function(o){o.hidden=o!==d});d.open=true;d.scrollIntoView({block:'nearest'})})})"
)


def render_overview_page(overview: Dict[str, Any], *, test_mode: bool = False) -> str:
    """The overview as one self-contained page from `load_overview`'s payload."""
    query = '?test_mode=1' if test_mode else ''
    projects: List[Dict[str, Any]] = list(overview.get('projects') or [])
    coverage: Dict[str, Dict[str, List[Dict[str, Any]]]] = overview.get('coverage') or {}
    classes = [str(c) for c in overview.get('classes') or SOURCE_BADGE_ORDER]
    nav = '<nav><strong>overview</strong> ' + ' '.join(
        f'<a href="{DOSSIER_PATH}{p["key"]}{query}">{escape(p["key"])}</a>'
        for p in projects if is_link_safe(p.get('key'))
    ) + '</nav>'
    parts = [
        '<!doctype html><html><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f'<title>Market overview</title><style>{CSS}{CSS_EXTRA}</style></head><body>',
        nav,
        '<header class="head" id="header"><h1>Market overview</h1>'
        f'<span class="m">{len(projects)} projects · sorted by primary-pool liquidity</span></header>',
        '<div class="grid">',
        _project_table(projects, query),
        _coverage_grid(projects, coverage, classes),
        '</div>',
        f'<script>{JS}</script></body></html>',
    ]
    return ''.join(parts)


# -- Panel 1: the project table ------------------------------------------------


def _project_table(projects: List[Dict[str, Any]], query: str) -> str:
    formatting = Formatting({})
    head = ''.join(
        f'<th class="n" title="{escape(GLOSSARY.get(_field(metric), ""))}">{escape(label)}</th>'
        for label, metric in COLUMNS
    )
    rows = ''.join(_project_row(p, formatting, query) for p in projects)
    if not projects:
        rows = f'<tr><td colspan="{len(COLUMNS) + 4}" class="foot">no projects in the registry</td></tr>'
    return (
        f'<section class="panel wide" id="projects"><h2>Projects ({len(projects)}){_tag("projects")}</h2>'
        f'{_glossary_block(tuple(_field(m) for _, m in COLUMNS) + ("flag_count",))}'
        f'<table><tr><th>project</th><th>chain</th>{head}<th>last build</th>'
        f'<th class="n" title="{escape(GLOSSARY["flag_count"])}">flags</th></tr>{rows}</table>'
        '<div class="foot">Δ against the build each section was diffed with; hover a value for the exact form</div>'
        '</section>'
    )


def _project_row(project: Dict[str, Any], formatting: Formatting, query: str) -> str:
    key = str(project.get('key'))
    values = project.get('values') or {}
    previous = project.get('previous') or {}
    cells = []
    for _, metric in COLUMNS:
        value, before = values.get(metric), previous.get(metric)
        text, direction = delta_text(before, value, delta_kind(metric))
        cells.append(
            f'<td class="n" title="{escape(formatting.exact(metric, value))}">{escape(brief(metric, value))}'
            f'<div class="d {direction}" title="previous build: {escape(formatting.exact(metric, before))}">'
            f'{escape(text)}</div></td>'
        )
    build = project.get('build')
    if build:
        outcome = str(build.get('outcome'))
        when = build.get('block_timestamp')
        stamp = formatting.scalar('block_timestamp', when) if when else ''
        status = (
            f'<span class="tag {"p" if outcome == "ok" else "bad"}" title="build {build.get("id")} '
            f'(run {build.get("run_id")}) {escape(stamp)}">{escape(outcome)}</span>'
        )
    else:
        status = '<span class="m">no build yet</span>'
    flags = int(project.get('flag_count') or 0)
    return (
        f'<tr data-project="{escape(key)}"><td>{_project_link(project, query)}</td>'
        f'<td>{escape(project.get("chain") or DASH)}</td>{"".join(cells)}<td>{status}</td>'
        f'<td class="n{" flagn" if flags else ""}">{flags}</td></tr>'
    )


def _project_link(project: Dict[str, Any], query: str) -> str:
    key = str(project.get('key'))
    name = escape(project.get('display_name') or key)
    if not is_link_safe(key):
        return name
    return f'<a href="{DOSSIER_PATH}{key}{query}" title="{escape(key)}">{name}</a>'


def _field(metric: str) -> str:
    return report.HISTORY_METRICS[metric][1][-1]


# -- Panel 2: the source-coverage grid ----------------------------------------


def _coverage_grid(
    projects: List[Dict[str, Any]],
    coverage: Dict[str, Dict[str, List[Dict[str, Any]]]],
    classes: List[str],
) -> str:
    head = ''.join(
        f'<th title="{escape(GLOSSARY.get(c, ""))}">{escape(c)}</th>' for c in classes
    )
    rows, details = [], []
    for index, project in enumerate(projects):
        key = str(project.get('key'))
        name = escape(project.get('display_name') or key)
        by_class = coverage.get(key) or {}
        cells = []
        for source_class in classes:
            sources = [s for s in by_class.get(source_class) or [] if isinstance(s, dict)]
            mark, shown = _cell(sources)
            title = f'{source_class}: ' + _summary(sources)
            if shown:
                title += '\n' + '\n'.join(_source_text(s) for s in shown)
            # A cell with no source rows has no list to open: no `data-target`,
            # no `<details>`, so the page carries only the lists that say
            # something (24 empty folds under the grid was the alternative).
            target = f' data-target="{_target(key, index, source_class)}"' if sources else ''
            cells.append(f'<td class="c"{target} title="{escape(title)}">{mark}</td>')
            if sources:
                details.append(_details(_target(key, index, source_class), name, source_class, sources))
        rows.append(f'<tr data-project="{escape(key)}"><td>{name}</td>{"".join(cells)}</tr>')
    body = ''.join(rows) or (
        f'<tr><td colspan="{len(classes) + 1}" class="foot">no projects in the registry</td></tr>'
    )
    return (
        f'<section class="panel wide" id="coverage"><h2>Source coverage{_tag("coverage")}</h2>'
        f'{_glossary_block(tuple(classes) + ("inherited", "candidate"))}'
        f'<table class="cov"><tr><th>project</th>{head}</tr>{body}</table>'
        f'<div class="legend">{LEGEND}</div>'
        f'<div class="srcs">{"".join(details)}</div></section>'
    )


def _cell(sources: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
    """The glyph for one project-by-class cell and the rows it counts.

    Admitted rows win, then candidates, then a suspended or retired remainder;
    the count is of the rows in the winning state, so `●1` over one admitted
    and two candidate rows says one is read and the hover lists the rest. The
    chain mark is added when every counted row hangs off the chain entity.
    """
    admitted = [s for s in sources if s.get('admission') == 'admitted']
    candidates = [s for s in sources if s.get('admission') == 'candidate']
    out = [s for s in sources if s.get('admission') in OUT_ADMISSIONS]
    if admitted:
        mark, shown = f'{glyph(STATE_ADMITTED)}{len(admitted)}', admitted
    elif candidates:
        mark, shown = f'{glyph(STATE_CANDIDATE)}{len(candidates)}', candidates
    elif out:
        mark, shown = glyph(STATE_OUT), out
    else:
        return glyph(STATE_NONE), []
    if all(s.get('inherited') for s in shown):
        mark += INHERITED_MARK
    return mark, shown


def _summary(sources: List[Dict[str, Any]]) -> str:
    counts: Dict[str, int] = {}
    for source in sources:
        admission = str(source.get('admission'))
        counts[admission] = counts.get(admission, 0) + 1
    if not counts:
        return 'no source row reaches this project'
    return ', '.join(f'{n} {admission}' for admission, n in sorted(counts.items()))


def _source_text(source: Dict[str, Any]) -> str:
    """`x.com/TouchGrassRWA · admitted by registry · inherited from the chain`:
    the handle without its scheme, then the admission and where it came from."""
    parts = [_handle_text(source.get('url_or_handle')), str(source.get('admission'))]
    if source.get('admitted_by'):
        parts[-1] += f' by {source["admitted_by"]}'
    evidence = source.get('evidence') or {}
    if isinstance(evidence, dict) and evidence.get('hop_from'):
        parts.append(f'hop from {evidence["hop_from"]}')
    if source.get('inherited'):
        parts.append('inherited from the chain')
    return ' · '.join(parts)


def _handle_text(handle: Any) -> str:
    return re.sub(r'^https?://', '', str(handle or DASH))


def _details(target: str, name: str, source_class: str, sources: List[Dict[str, Any]]) -> str:
    """The folded source list one grid cell opens; rendered only for a cell
    with at least one row. A source is linked only when its handle is a web
    URL: the RPC row is a bare host, and a candidate handle comes from the
    provider's published links, so a scheme other than http(s) never becomes
    an href."""
    items = ''.join(
        f'<li>{_source_link(s)} · {escape(_source_text(s).split(" · ", 1)[1])}</li>' for s in sources
    )
    return (
        f'<details id="{target}"><summary>{name} · {escape(source_class)} ({len(sources)})</summary>'
        f'<ul>{items}</ul></details>'
    )


def _source_link(source: Dict[str, Any]) -> str:
    handle = source.get('url_or_handle')
    text = _handle_text(handle)
    url: Optional[str] = str(handle) if str(handle or '').startswith(('https://', 'http://')) else None
    return _link(url, text)


def _target(key: str, index: int, source_class: str) -> str:
    # Element ids: a registry slug and a config constant, both `[A-Za-z0-9._-]`,
    # so no escaping is needed; a key outside that set is replaced by the row
    # index rather than trusted.
    return f'cov-{key if is_link_safe(key) else index}-{source_class}'
